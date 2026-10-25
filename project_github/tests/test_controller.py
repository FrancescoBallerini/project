# Copyright 2026 Francesco Ballerini
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

import json
import os
from hashlib import sha256
from hmac import HMAC
from unittest.mock import patch

from psycopg2 import OperationalError

from odoo.tests import tagged
from odoo.tools import mute_logger

from odoo.addons.project_git.tests.common import TEST_TOKEN, ProjectGitControllerCase

from ..controllers.main import ProjectGithubWebhook


@tagged("post_install", "-at_install")
class TestGithubWebhookController(ProjectGitControllerCase):
    """Real HTTP requests with the GitHub authorization scheme: the
    request body signed with the webhook secret (HMAC-SHA256)."""

    RES_DIR = os.path.join(os.path.dirname(__file__), "res")

    def setUp(self):
        super().setUp()
        self.config.set_param("project_github.webhook_secret", TEST_TOKEN)

    @staticmethod
    def _github_signature(payload, token=TEST_TOKEN):
        """Compute the X-Hub-Signature-256 header value the way GitHub
        does: HMAC-SHA256 of the raw request body with the shared token.
        The payload must be serialized exactly like _post_webhook does."""
        digest = HMAC(
            key=token.encode("utf-8"),
            msg=json.dumps(payload).encode("utf-8"),
            digestmod=sha256,
        ).hexdigest()
        return f"sha256={digest}"

    def test_github_valid_signature_enqueues_job(self):
        payload = self._load_payload("github_push.json")
        jobs_before = self._job_count("_process_commit_push_github")
        response = self._post_webhook(
            payload=payload,
            headers={
                "X-GitHub-Event": "push",
                "X-Hub-Signature-256": self._github_signature(payload),
            },
        )
        self.assertEqual(
            self._job_count("_process_commit_push_github"), jobs_before + 1
        )
        self.assertEqual(self._last_job().channel, "root.project_git")
        # The body links the delivery to the queue job handling it
        self.assertEqual(
            response.json(),
            {"status": "queued", "job_uuid": self._last_job().uuid},
        )

    def test_missing_secret_param_rejects_requests(self):
        self.config.set_param("project_github.webhook_secret", False)
        payload = self._load_payload("github_push.json")
        jobs_before = self._job_count("_process_commit_push_github")
        self._post_webhook(
            payload=payload,
            headers={
                "X-GitHub-Event": "push",
                "X-Hub-Signature-256": self._github_signature(payload),
            },
            expected_status=503,
        )
        self.assertEqual(self._job_count("_process_commit_push_github"), jobs_before)

    def test_github_invalid_signature_is_rejected(self):
        jobs_before = self._job_count("_process_commit_push_github")
        self._post_webhook(
            payload=self._load_payload("github_push.json"),
            headers={
                "X-GitHub-Event": "push",
                "X-Hub-Signature-256": f"sha256={'0' * 64}",
            },
            expected_status=401,
        )
        self.assertEqual(self._job_count("_process_commit_push_github"), jobs_before)

    def test_github_event_without_handler_is_skipped(self):
        # GitHub events are classified by their X-GitHub-Event header:
        # types without a _process_* handler (e.g. the ping event sent
        # on hook creation) are acknowledged without enqueueing anything
        payload = {"zen": "Design for failure.", "hook_id": 1, "repository": {}}
        jobs_total_before = self._job_count()
        response = self._post_webhook(
            payload=payload,
            headers={
                "X-GitHub-Event": "ping",
                "X-Hub-Signature-256": self._github_signature(payload),
            },
        )
        self.assertEqual(self._job_count(), jobs_total_before)
        self.assertEqual(response.json(), {"status": "skipped", "reason": "no handler"})

    def test_controller_exception_fails_the_delivery(self):
        # An authorized request whose handling raises must not look
        # successful to the platform: the 500 status records the
        # delivery as failed, so it can be redelivered once fixed
        payload = self._load_payload(name="github_push.json")
        jobs_total_before = self._job_count()
        with patch.object(
            target=ProjectGithubWebhook,
            attribute="_parse_git_request_data_github",
            side_effect=ValueError("Unexpected payload"),
        ), mute_logger("odoo.http"):
            response = self._post_webhook(
                payload=payload,
                headers={
                    "X-GitHub-Event": "push",
                    "X-Hub-Signature-256": self._github_signature(payload=payload),
                },
                expected_status=500,
            )
        self.assertEqual(self._job_count(), jobs_total_before)
        self.assertEqual(
            response.json(), {"status": "error", "reason": "Internal Server Error"}
        )

    def test_database_error_fails_the_delivery(self):
        # A database error the Odoo HTTP layer does not retry (or retries
        # in vain) reaches it outside the controller: it must fail the
        # delivery as well, not look successful
        payload = self._load_payload(name="github_push.json")
        jobs_total_before = self._job_count()
        with patch.object(
            target=ProjectGithubWebhook,
            attribute="_parse_git_request_data_github",
            side_effect=OperationalError("Connection lost"),
        ), mute_logger("odoo.http", "odoo.service.model"):
            response = self._post_webhook(
                payload=payload,
                headers={
                    "X-GitHub-Event": "push",
                    "X-Hub-Signature-256": self._github_signature(payload=payload),
                },
                expected_status=500,
            )
        self.assertEqual(self._job_count(), jobs_total_before)
        self.assertEqual(
            response.json(), {"status": "error", "reason": "Internal Server Error"}
        )
