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
        """Answer the errors of the webhook route in the format of its
        other responses, keeping the status chosen by Odoo. This hook
        runs after the retries on database concurrency errors and after
        the commit, so it also covers the errors raised outside the
        controller."""
        response = super()._handle_error(exception=exception)
        if request.httprequest.path == WEBHOOK_ROUTE and isinstance(
            response, HTTPException
        ):
            return request.make_json_response(
                data={"status": "error", "reason": response.name}, status=response.code
            )
        return response
