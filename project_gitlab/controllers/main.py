# Copyright 2018, Jarsa
# Copyright 2026 Francesco Ballerini
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

from odoo.http import request
from odoo.tools import consteq

from odoo.addons.project_git.controllers.main import ProjectGitWebhook


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
        """GitLab sends the webhook token verbatim in a request header."""
        gitlab_token = request.httprequest.headers.get("X-Gitlab-Token", "")
        return consteq(gitlab_token, token)

    def _parse_git_request_data_gitlab(self, event, headers=None):
        # GitLab carries its authoritative event discriminator in the
        # payload: map it onto the module-owned key (headers unused)
        event["project_git_event_type"] = event.get("object_kind")
        return event
