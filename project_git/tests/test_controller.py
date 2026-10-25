# Copyright 2026 Francesco Ballerini
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

from odoo.tests import tagged

from .common import WEBHOOK_URL, ProjectGitControllerCase


@tagged("post_install", "-at_install")
class TestWebhookController(ProjectGitControllerCase):
    """Platform-agnostic authorization layer: requests that no platform
    claims. The platform-specific layers (secret lookup and
    token/signature verification) are covered by the bridge test
    suites."""

    def test_request_without_auth_headers_is_rejected(self):
        # No platform header claims the request: the source stays
        # unrecognized and the request is rejected before any secret
        # lookup
        jobs_before = self._job_count()
        response = self._post_webhook(
            payload={"object_kind": "push"}, expected_status=400
        )
        self.assertEqual(self._job_count(), jobs_before)
        # The body (shown in the delivery log of the platform) tells why
        self.assertEqual(
            self._response_data(response=response),
            {"status": "rejected", "reason": "unrecognized webhook source"},
        )

    def test_error_of_the_odoo_http_layer_is_json(self):
        # A request that is not JSON is refused before the controller:
        # the error keeps the format of the other webhook responses
        response = self.url_open(url=WEBHOOK_URL)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"status": "error", "reason": "Bad Request"})
