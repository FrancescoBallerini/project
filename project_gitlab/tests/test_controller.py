# Copyright 2026 Francesco Ballerini
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

import json
import os
import time
from base64 import b64decode, b64encode
from hashlib import sha256
from hmac import HMAC

from odoo.tests import tagged

from odoo.addons.project_git.tests.common import TEST_TOKEN, ProjectGitControllerCase

from ..models.project_git_auth import SIGNING_SECRET_PREFIX
from .common import GITLAB_INSTANCE_ROOT, TEST_SIGNING_SECRET

# Delivery header value the way GitLab sends it: the instance root URL
# without a trailing slash
GITLAB_INSTANCE_HEADER = GITLAB_INSTANCE_ROOT.rstrip("/")


@tagged("post_install", "-at_install")
class TestGitlabWebhookController(ProjectGitControllerCase):
    """Real HTTP requests with the GitLab authorization schemes: the
    legacy secret sent verbatim in the X-Gitlab-Token header, or the
    Standard Webhooks signature when the configured secret is a
    signing token (whsec_). Either way the sending instance selects
    the secret by the X-Gitlab-Instance header (with a payload
    fallback for instances predating it)."""

    RES_DIR = os.path.join(os.path.dirname(__file__), "res")

    def setUp(self):
        super().setUp()
        # The secret is keyed by the instance root the fixture repos live on
        self.config.set_param(
            f"project_gitlab.webhook_secret.{GITLAB_INSTANCE_ROOT}", TEST_TOKEN
        )

    @staticmethod
    def _gitlab_headers(token=TEST_TOKEN, instance=GITLAB_INSTANCE_HEADER):
        """Delivery headers the way GitLab sends them: the instance
        root URL comes without a trailing slash."""
        headers = {"X-Gitlab-Event": "Push Hook", "X-Gitlab-Token": token}
        if instance:
            headers["X-Gitlab-Instance"] = instance
        return headers

    def test_gitlab_wrong_token_is_rejected(self):
        jobs_before = self._job_count("_process_commit_push_gitlab")
        result = self._post_webhook(
            self._load_payload("gitlab_push.json"),
            headers=self._gitlab_headers(token="not-the-right-token"),
        )
        self.assertIs(result, False)
        self.assertEqual(self._job_count("_process_commit_push_gitlab"), jobs_before)

    def test_gitlab_valid_token_enqueues_job(self):
        jobs_before = self._job_count("_process_commit_push_gitlab")
        self._post_webhook(
            self._load_payload("gitlab_push.json"), headers=self._gitlab_headers()
        )
        self.assertEqual(
            self._job_count("_process_commit_push_gitlab"), jobs_before + 1
        )
        self.assertEqual(self._last_job().channel, "root.project_git")

    def test_gitlab_instance_without_header_falls_back_on_payload(self):
        # Instances older than GitLab 15.5 send no X-Gitlab-Instance
        # header: the instance root is derived from the project URL
        # carried by the payload
        jobs_before = self._job_count("_process_commit_push_gitlab")
        self._post_webhook(
            self._load_payload("gitlab_push.json"),
            headers=self._gitlab_headers(instance=None),
        )
        self.assertEqual(
            self._job_count("_process_commit_push_gitlab"), jobs_before + 1
        )

    def test_gitlab_unknown_instance_is_rejected(self):
        # The sending instance selects the secret: an instance with no
        # configured secret is rejected even with a matching token
        jobs_before = self._job_count("_process_commit_push_gitlab")
        result = self._post_webhook(
            self._load_payload("gitlab_push.json"),
            headers=self._gitlab_headers(instance="https://rogue.example.com"),
        )
        self.assertIs(result, False)
        self.assertEqual(self._job_count("_process_commit_push_gitlab"), jobs_before)

    def test_missing_secret_param_rejects_requests(self):
        self.config.set_param(
            f"project_gitlab.webhook_secret.{GITLAB_INSTANCE_ROOT}", False
        )
        jobs_before = self._job_count("_process_commit_push_gitlab")
        result = self._post_webhook(
            self._load_payload("gitlab_push.json"), headers=self._gitlab_headers()
        )
        self.assertIs(result, False)
        self.assertEqual(self._job_count("_process_commit_push_gitlab"), jobs_before)

    def test_insecure_default_token_rejects_requests(self):
        # The demo default "token" is publicly known: a webhook sending
        # the matching header must be rejected anyway
        self.config.set_param(
            f"project_gitlab.webhook_secret.{GITLAB_INSTANCE_ROOT}", "token"
        )
        jobs_before = self._job_count("_process_commit_push_gitlab")
        result = self._post_webhook(
            self._load_payload("gitlab_push.json"),
            headers=self._gitlab_headers(token="token"),
        )
        self.assertIs(result, False)
        self.assertEqual(self._job_count("_process_commit_push_gitlab"), jobs_before)

    # ---- signing token verification (Standard Webhooks) ----

    def _use_signing_secret(self):
        self.config.set_param(
            f"project_gitlab.webhook_secret.{GITLAB_INSTANCE_ROOT}",
            TEST_SIGNING_SECRET,
        )

    @staticmethod
    def _gitlab_signed_headers(
        payload, signing_secret=TEST_SIGNING_SECRET, timestamp=None, message_id="msg-1"
    ):
        """Delivery headers the way GitLab sends them with a signing
        token configured: the signature covers message id, timestamp
        and the raw body (serialized exactly like _post_webhook does)."""
        timestamp = str(int(time.time())) if timestamp is None else timestamp
        key = b64decode(signing_secret[len(SIGNING_SECRET_PREFIX) :])
        signed_payload = (
            f"{message_id}.{timestamp}.".encode() + json.dumps(payload).encode()
        )
        signature = b64encode(
            HMAC(key=key, msg=signed_payload, digestmod=sha256).digest()
        ).decode()
        return {
            "X-Gitlab-Event": "Push Hook",
            "X-Gitlab-Instance": GITLAB_INSTANCE_HEADER,
            "Webhook-Id": message_id,
            "Webhook-Timestamp": timestamp,
            "Webhook-Signature": f"v1,{signature}",
        }

    def test_gitlab_valid_signature_enqueues_job(self):
        self._use_signing_secret()
        payload = self._load_payload("gitlab_push.json")
        jobs_before = self._job_count("_process_commit_push_gitlab")
        self._post_webhook(payload, headers=self._gitlab_signed_headers(payload))
        self.assertEqual(
            self._job_count("_process_commit_push_gitlab"), jobs_before + 1
        )

    def test_gitlab_invalid_signature_is_rejected(self):
        # Signed with a different key: the recomputed signature cannot match
        self._use_signing_secret()
        wrong_secret = SIGNING_SECRET_PREFIX + b64encode(b"x" * 32).decode()
        payload = self._load_payload("gitlab_push.json")
        jobs_before = self._job_count("_process_commit_push_gitlab")
        result = self._post_webhook(
            payload,
            headers=self._gitlab_signed_headers(payload, signing_secret=wrong_secret),
        )
        self.assertIs(result, False)
        self.assertEqual(self._job_count("_process_commit_push_gitlab"), jobs_before)

    def test_gitlab_stale_timestamp_is_rejected(self):
        # The timestamp is part of the signed payload: an old (replayed)
        # delivery is rejected even with a valid signature
        self._use_signing_secret()
        payload = self._load_payload("gitlab_push.json")
        stale = str(int(time.time()) - 3600)
        jobs_before = self._job_count("_process_commit_push_gitlab")
        result = self._post_webhook(
            payload, headers=self._gitlab_signed_headers(payload, timestamp=stale)
        )
        self.assertIs(result, False)
        self.assertEqual(self._job_count("_process_commit_push_gitlab"), jobs_before)

    def test_gitlab_signing_secret_rejects_plain_token_header(self):
        # With a signing token configured the delivery must be signed:
        # a legacy X-Gitlab-Token header is not an alternative, not
        # even when it carries the right secret
        self._use_signing_secret()
        payload = self._load_payload("gitlab_push.json")
        jobs_before = self._job_count("_process_commit_push_gitlab")
        result = self._post_webhook(
            payload, headers=self._gitlab_headers(token=TEST_SIGNING_SECRET)
        )
        self.assertIs(result, False)
        self.assertEqual(self._job_count("_process_commit_push_gitlab"), jobs_before)

    def test_gitlab_rotated_signature_list_is_accepted(self):
        # webhook-signature is a space-separated list: during secret
        # rotation the delivery is accepted if any v1 signature matches
        self._use_signing_secret()
        payload = self._load_payload("gitlab_push.json")
        headers = self._gitlab_signed_headers(payload)
        valid_signature = headers["Webhook-Signature"]
        headers["Webhook-Signature"] = f"v1,{'0' * 44} {valid_signature}"
        jobs_before = self._job_count("_process_commit_push_gitlab")
        self._post_webhook(payload, headers=headers)
        self.assertEqual(
            self._job_count("_process_commit_push_gitlab"), jobs_before + 1
        )

    def test_gitlab_event_without_handler_is_skipped(self):
        # Authorized event kinds without a _process_* handler (e.g. note
        # events) are acknowledged without enqueueing anything
        jobs_total_before = self._job_count()
        headers = self._gitlab_headers()
        headers["X-Gitlab-Event"] = "Note Hook"
        result = self._post_webhook(
            {"object_kind": "note", "project": {}}, headers=headers
        )
        self.assertIs(result, True)
        self.assertEqual(self._job_count(), jobs_total_before)
