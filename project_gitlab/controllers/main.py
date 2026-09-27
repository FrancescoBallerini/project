# Copyright 2018, Jarsa
# Copyright 2026 Francesco Ballerini
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

import time
from base64 import b64decode, b64encode
from hashlib import sha256
from hmac import HMAC

from odoo.http import request
from odoo.tools import consteq

from odoo.addons.project_git.controllers.main import ProjectGitWebhook

from ..models.project_git_auth import SIGNING_SECRET_PREFIX

# Tolerance on the signed webhook-timestamp before a delivery is
# treated as a replay: the default of the Standard Webhooks
# specification, wide enough for clock skew and delivery retries
SIGNATURE_TOLERANCE_SECONDS = 300


class ProjectGitlabWebhook(ProjectGitWebhook):
    def _detect_event_source(self, headers):
        # GitLab claims its requests by its specific event header
        if headers.get("X-Gitlab-Event"):
            return "gitlab"
        return super()._detect_event_source(headers)

    def _get_webhook_secret_gitlab(self):
        """Per-instance secret: the sending instance selects the
        project_gitlab.webhook_secret.<instance root> parameter. The
        root comes from the X-Gitlab-Instance header (GitLab >= 15.5);
        older instances fall back on the project URL carried by the
        payload. Both are attacker-controlled but harmless: they only
        select which secret the request is verified against."""
        instance_root = request.httprequest.headers.get("X-Gitlab-Instance", "")
        if instance_root:
            instance_root = instance_root.rstrip("/") + "/"
        else:
            project_url = request.get_json_data().get("project", {}).get("web_url", "")
            if project_url:
                instance_root = request.env[
                    "project.git.auth"
                ]._get_gitlab_instance_root(project_url)
        if not instance_root:
            return False
        return (
            request.env["ir.config_parameter"]
            .sudo()
            .get_param("project_gitlab.webhook_secret." + instance_root)
        )

    def _verify_webhook_token_gitlab(self, token):
        """GitLab authorizes the deliveries with one of two mechanisms,
        selected by the format of the configured secret: a signing
        token (``whsec_`` prefix, GitLab >= 19.1) signs every delivery
        and never travels, while the legacy secret token is sent
        verbatim in the X-Gitlab-Token header (the only option on
        older instances)."""
        if token.startswith(SIGNING_SECRET_PREFIX):
            return self._verify_webhook_signature_gitlab(token)
        gitlab_token = request.httprequest.headers.get("X-Gitlab-Token", "")
        return consteq(gitlab_token, token)

    def _verify_webhook_signature_gitlab(self, signing_token):
        """Verify the delivery signature per the Standard Webhooks
        specification (followed by GitLab): HMAC-SHA256 of
        ``{message id}.{timestamp}.{body}`` keyed with the
        base64-decoded signing token (``whsec_`` prefix stripped),
        matched against the ``v1,{base64}`` signatures of the
        webhook-signature header (a space-separated list, so secret
        rotation keeps the old signature valid). The timestamp is part
        of the signed payload: deliveries older than the tolerance are
        rejected as replays."""
        headers = request.httprequest.headers
        message_id = headers.get("Webhook-Id", "")
        timestamp = headers.get("Webhook-Timestamp", "")
        signature_header = headers.get("Webhook-Signature", "")
        if not (message_id and timestamp and signature_header):
            return False
        try:
            if abs(time.time() - int(timestamp)) > SIGNATURE_TOLERANCE_SECONDS:
                return False
            key = b64decode(signing_token[len(SIGNING_SECRET_PREFIX) :])
        except ValueError:
            return False
        signed_payload = (
            f"{message_id}.{timestamp}.".encode() + request.httprequest.data
        )
        expected_signature = b64encode(
            HMAC(key=key, msg=signed_payload, digestmod=sha256).digest()
        ).decode()
        return any(
            consteq(signature.split(",", 1)[1], expected_signature)
            for signature in signature_header.split()
            if signature.startswith("v1,")
        )

    def _parse_git_request_data_gitlab(self, event, headers=None):
        # GitLab carries its authoritative event discriminator in the
        # payload: map it onto the module-owned key (headers unused)
        event["project_git_event_type"] = event.get("object_kind")
        return event
