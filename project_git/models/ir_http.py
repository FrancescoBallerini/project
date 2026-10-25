# Copyright 2026 Francesco Ballerini
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

from werkzeug.exceptions import HTTPException
from werkzeug.http import HTTP_STATUS_CODES

from odoo import models
from odoo.http import request

from .project_git_utils import WEBHOOK_ROUTE


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _handle_error(cls, exception):
        """Answer the errors of the webhook route with an HTTP error
        status: the JSON-RPC error handling would answer 200, and the
        platform would record the delivery as successful. This hook
        runs after the retries on database concurrency errors and after
        the commit, so it also covers the errors raised outside the
        controller."""
        response = super()._handle_error(exception=exception)
        if request.httprequest.path != WEBHOOK_ROUTE:
            return response
        status = exception.code if isinstance(exception, HTTPException) else 500
        reason = HTTP_STATUS_CODES.get(status, "Unknown Error")
        return request.make_json_response(
            data={"status": "error", "reason": reason}, status=status
        )
