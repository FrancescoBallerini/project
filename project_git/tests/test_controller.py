# Copyright 2026 Francesco Ballerini
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

from odoo.tests import tagged

from .common import ProjectGitControllerCase


@tagged("post_install", "-at_install")
class TestWebhookController(ProjectGitControllerCase):
    """Platform-agnostic authorization layer: requests that no platform
    claims. The platform-specific layers (secret lookup, GitLab token
    and GitHub signature verification) are covered by the bridge test
    suites."""

    def test_request_without_auth_headers_is_rejected(self):
        # No platform header claims the request: the source stays
        # unrecognized and the request is rejected even with a valid
        # secret configured
        jobs_before = self._job_count()
        result = self._post_webhook({"object_kind": "push"})
        self.assertIs(result, False)
        self.assertEqual(self._job_count(), jobs_before)
