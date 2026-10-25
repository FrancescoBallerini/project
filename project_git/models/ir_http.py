# Copyright 2026 Francesco Ballerini
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

from werkzeug.exceptions import HTTPException

from odoo import models
from odoo.http import request

from .project_git_utils import WEBHOOK_ROUTE


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _handle_error(cls, exception):
        response = super()._handle_error(exception=exception)
        if request.httprequest.path == WEBHOOK_ROUTE and isinstance(
            response, HTTPException
        ):
            # Answer the errors of the webhook route in the format of its
            # other responses while propagating the status chosen by Odoo.

            # Preserve the headers of the HTTP error, e.g. the Allow header
            # that a 405 response MUST carry (RFC 9110, section 15.5.6).
            # The HTML content type is dropped instead, so that
            # make_json_response sets its own.

            # This hook runs after the retries on database concurrency
            # errors and after the commit, so it also covers the errors
            # raised outside the controller.
            headers = [
                (name, value)
                for name, value in response.get_headers()
                if name.lower() != "content-type"
            ]
            return request.make_json_response(
                data={"status": "error", "reason": response.name},
                headers=headers,
                status=response.code,
            )
        return response
