# Copyright 2018, Jarsa
# Copyright 2026 Francesco Ballerini
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

import functools
import logging

from werkzeug.exceptions import abort

from odoo import http
from odoo.http import request

from ..models.project_git_auth import BANNED_TOKENS
from ..models.project_git_utils import WEBHOOK_ROUTE

_logger = logging.getLogger(__name__)


def abort_webhook(reason, http_status):
    """Answer the webhook request with an HTTP error status.

    A JSON-RPC route always answers with the 200 status: Odoo puts any
    error in the response body, which the platforms do not read. An
    abort with a ready-made response bypasses that error handling (the
    Odoo JSON-RPC dispatcher uses the same trick for its own 400
    errors), so the platform records the delivery as failed. The JSON
    body, shown in the delivery log of the platform, gives the reason.
    """
    abort(
        status=request.make_json_response(
            data={"status": "rejected", "reason": reason}, status=http_status
        )
    )


def token_authorization(function):
    """Decorator for controllers with token authorization.

    The event source is recognized from the request headers
    (_detect_event_source), the webhook secret of that platform is read
    by the source-specific _get_webhook_secret_<source> hook, then the
    request is authorized against it by the source-specific
    _verify_webhook_token_<source> method.

    Rejected requests get an HTTP error status, so the platform records
    the delivery as failed (and it can be redelivered once the
    configuration is fixed): 400 for unrecognized sources, 503 when the
    secret of the platform is not configured on Odoo, 401 for an
    invalid token or signature.
    """

    @functools.wraps(function)
    def wrapper(self, *args, **kw):
        source = self._detect_event_source(request.httprequest.headers)
        if not source:
            _logger.warning(
                "Unrecognized webhook source (no platform header claims the request)"
            )
            abort_webhook(reason="unrecognized webhook source", http_status=400)
        secret = False
        if hasattr(self, f"_get_webhook_secret_{source}"):
            secret = getattr(self, f"_get_webhook_secret_{source}")()
        if not secret or secret in BANNED_TOKENS:
            _logger.warning(
                "The webhook secret for source %r is not configured "
                "(or still set to an insecure default)",
                source,
            )
            abort_webhook(reason="webhook secret not configured", http_status=503)
        authorization = False
        if hasattr(self, f"_verify_webhook_token_{source}"):
            authorization = getattr(self, f"_verify_webhook_token_{source}")(secret)
        if not authorization:
            _logger.warning("Token is not the expected for source %r", source)
            abort_webhook(reason="invalid webhook token", http_status=401)
        kw["source"] = source
        return function(self, *args, **kw)

    return wrapper


class ProjectGitWebhook(http.Controller):
    @http.route(route=WEBHOOK_ROUTE, type="json", auth="public", csrf=False)
    @token_authorization
    def _process_webhook(self, **kw):
        """Receive the request from the git platform and invoke the
        per-source handler named after the normalized
        'project_git_event_type' key and the event source:
        _process_<project_git_event_type>_<source>. Each platform
        bridge explicitly binds every event it handles.

        The JSON-RPC result, shown in the delivery log of the platform,
        gives the outcome ("queued" or "skipped"), plus the reason or
        the uuid of the queue job handling the event. An exception
        raised while handling the request answers with the 500 status
        (ir.http _handle_error), so the delivery is recorded as failed
        too."""
        event = request.get_json_data()
        event["source"] = kw.get("source", "")
        event = self._parse_git_request_data(
            event=event, headers=request.httprequest.headers
        )
        git_event = request.env["project.git.event"]
        event_type = event.get("project_git_event_type")
        if not event_type:
            return {"status": "skipped", "reason": "no event type"}
        method_name = f"_process_{event_type}_{event['source']}"
        if not hasattr(git_event, method_name):
            # Event kinds the source bridge binds no handler for
            # (e.g. note events) are skipped silently.
            return {"status": "skipped", "reason": "no handler"}
        channel = request.env["project.git.utils"]._get_project_git_queue_job_channel()
        job = getattr(git_event.with_delay(channel=channel.complete_name), method_name)(
            event=event
        )
        # The job uuid links the delivery log of the platform to the
        # queue job in Odoo
        return {"status": "queued", "job_uuid": job.uuid}

    def _detect_event_source(self, headers):
        """Recognize the source platform of a request from its headers.

        Each platform bridge claims its own specific header (claim first,
        then super()); requests that no platform claims map to an empty
        source (and are then rejected by the token authorization).
        """
        return ""

    def _parse_git_request_data(self, event, headers=None):
        """The structure of a git request differ among different sources
        (e.g. github vs gitlab). This method dispatches to the
        source-specific parser, which normalizes the request-level
        data (source, event type) into the module-owned 'common keys'
        of the event; the payload data is then read by the per-source
        hooks of project.git.event.

        The event type lands in the module-owned
        'project_git_event_type' key: each parser maps the
        authoritative discriminator of its platform onto it (some
        platforms carry it in the payload, others in a request header
        — hence the headers argument). Push events are then refined into their
        concrete type (branch_creation, branch_deletion, commit_push),
        so that project_git_event_type, together with the source,
        always names the project.git.event handler to invoke."""

        if not hasattr(self, f"_parse_git_request_data_{event.get('source')}"):
            _logger.warning(
                "No request parser implementation for source %r", event.get("source")
            )
            return event
        event = getattr(self, f"_parse_git_request_data_{event.get('source')}")(
            event=event, headers=headers
        )
        if event.get("project_git_event_type") == "push":
            event["project_git_event_type"] = self._classify_push_event(event)
        return event

    def _classify_push_event(self, event):
        """
        Classify push event type based on before/after SHA values.
        Returns: 'branch_creation', 'branch_deletion', 'commit_push', or
        an empty string for degenerate pushes (both SHAs null), which
        are then dropped by the explicit no-type guard of the dispatch.

        The null-SHA convention on before/after is git's own push
        schema, not a platform convention: platforms usually carry it
        verbatim in their push payloads, so the classification is
        shared by every source.
        """
        NULL_SHA = "0000000000000000000000000000000000000000"
        before = event.get("before", "")
        after = event.get("after", "")

        if before == NULL_SHA and after != NULL_SHA:
            return "branch_creation"
        elif before != NULL_SHA and after == NULL_SHA:
            return "branch_deletion"
        elif before != NULL_SHA and after != NULL_SHA:
            return "commit_push"
        return ""
