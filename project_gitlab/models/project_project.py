# Copyright 2018, Jarsa
# Copyright 2026 Francesco Ballerini
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

from urllib.parse import urlparse

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .project_git_auth import SIGNING_SECRET_PREFIX


class ProjectProject(models.Model):
    _inherit = "project.project"

    gitlab_ci_available = fields.Boolean(
        compute="_compute_gitlab_ci_available",
        help="Technical field showing the CI job retry button: whether "
        "one of the repository URLs of the project is hosted on GitLab",
    )

    @api.depends("git_project_url", "git_dev_project_url")
    def _compute_gitlab_ci_available(self):
        for project in self:
            project.gitlab_ci_available = any(
                project._is_gitlab_repository_url(project_url)
                for project_url in (
                    project.git_project_url,
                    project.git_dev_project_url,
                )
            )

    @api.model
    def _is_gitlab_repository_url(self, project_url):
        """Whether the repository URL is hosted on GitLab: its host
        carries "gitlab" in the name, or a GitLab API token system
        parameter is configured for its instance (the way a self-hosted
        instance on a custom domain is recognized)."""
        if not project_url:
            return False
        if "gitlab" in urlparse(project_url).netloc:
            return True
        instance_root = self.env["project.git.auth"]._get_gitlab_instance_root(
            project_url
        )
        return bool(
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("project_gitlab.token." + instance_root)
        )

    @api.model
    def _get_url_platform(self, project_url):
        # Catch-all claim: any host no other platform recognizes is
        # treated as a GitLab instance (claim after super())
        return super()._get_url_platform(project_url) or "gitlab"

    def _create_project_webhook_gitlab(self, project_url):
        git_auth = self.env["project.git.auth"]
        gl = git_auth._connect_gitlab(url=project_url)
        gitlab_project = gl.projects.get(self._git_project_path(project_url))
        odoo_url = self._get_webhook_url()
        for hook in gitlab_project.hooks.list():
            if hook.url == odoo_url:
                hook.delete()
        instance_root = git_auth._get_gitlab_instance_root(project_url)
        webhook_secret = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("project_gitlab.webhook_secret." + instance_root)
        )
        # The secret format selects the hook attribute: a signing
        # token (whsec_, GitLab >= 19.1) or the legacy plain secret
        secret_attribute = (
            "signing_token"
            if webhook_secret and webhook_secret.startswith(SIGNING_SECRET_PREFIX)
            else "token"
        )
        gitlab_project.hooks.create(
            {
                "url": odoo_url,
                "push_events": True,
                "merge_requests_events": True,
                "pipeline_events": True,
                "enable_ssl_verification": True,
                secret_attribute: webhook_secret,
            }
        )

    def action_open_ci_job_retry(self):
        """Open the CI job retry wizard on the first project repository
        hosted on GitLab (production first, same rule that shows the
        button)."""
        self.ensure_one()
        repository = False
        for candidate, project_url in (
            ("production", self.git_project_url),
            ("development", self.git_dev_project_url),
        ):
            if self._is_gitlab_repository_url(project_url):
                repository = candidate
                break
        # The CI job retry is a GitLab-only feature
        if not repository:
            raise UserError(
                _("The CI job retry is available on GitLab repositories only.")
            )
        wizard = self.env["project.gitlab.ci.job.retry"].create(
            {"project_id": self.id, "git_project_url_type": repository}
        )
        return wizard.action_load_jobs()
