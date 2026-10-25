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
            response.json(),
            {"status": "rejected", "reason": "unrecognized webhook source"},
        )

    def test_error_of_the_odoo_http_layer_is_json(self):
        # Errors raised before the controller keep the format of the
        # other webhook responses
        response = self.url_open(url=WEBHOOK_URL)
        self.assertEqual(response.status_code, 405)
        self.assertEqual(
            response.json(), {"status": "error", "reason": "Method Not Allowed"}
        )
        # The headers of the HTTP error are kept: a 405 response must
        # list the allowed methods. Odoo also lists OPTIONS, which it
        # adds to every route with explicit methods
        self.assertIn("POST", response.headers["Allow"].split(", "))

    def test_body_not_json_object_is_rejected(self):
        # A body that is not a JSON object (form data included) is a bad
        # request, rejected before the platform checks
        jobs_before = self._job_count()
        for body, content_type in (
            ("", "application/json"),
            ("not json", "application/json"),
            ("[1, 2]", "application/json"),
            ("null", "application/json"),
            ("payload=%7B%7D", "application/x-www-form-urlencoded"),
        ):
            with self.subTest(body=body, content_type=content_type):
                response = self._post_raw_webhook(
                    data=body,
                    headers={"Content-Type": content_type},
                    expected_status=400,
                )
                self.assertEqual(
                    response.json(),
                    {"status": "rejected", "reason": "invalid JSON body"},
                )
        self.assertEqual(self._job_count(), jobs_before)
