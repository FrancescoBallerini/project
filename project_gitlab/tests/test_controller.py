# Copyright 2026 Francesco Ballerini
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

import os

from odoo.tests import tagged

from odoo.addons.project_git.tests.common import (
    GITLAB_INSTANCE_ROOT,
    TEST_TOKEN,
    ProjectGitControllerCase,
)

# Delivery header value the way GitLab sends it: the instance root URL
# without a trailing slash
GITLAB_INSTANCE_HEADER = GITLAB_INSTANCE_ROOT.rstrip("/")


@tagged("post_install", "-at_install")
class TestGitlabWebhookController(ProjectGitControllerCase):
    """Real HTTP requests with the GitLab authorization scheme: the
    webhook secret sent verbatim in the X-Gitlab-Token header and
    selected per instance by the X-Gitlab-Instance header (with a
    payload fallback for instances predating it)."""

    RES_DIR = os.path.join(os.path.dirname(__file__), "res")

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
