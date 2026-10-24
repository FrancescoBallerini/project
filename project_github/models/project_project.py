# Copyright 2018, Jarsa
# Copyright 2026 Francesco Ballerini
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

from urllib.parse import urlparse

from github import GithubException

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ProjectProject(models.Model):
    _inherit = "project.project"

    github_ci_available = fields.Boolean(
        compute="_compute_github_ci_available",
        help="Technical field showing the CI job retry button: whether "
        "one of the repository URLs of the project is hosted on GitHub",
    )

    @api.depends("git_project_url", "git_dev_project_url")
    def _compute_github_ci_available(self):
        for project in self:
            project.github_ci_available = any(
                project._is_github_repository_url(project_url)
                for project_url in (
                    project.git_project_url,
                    project.git_dev_project_url,
                )
            )

    @api.model
    def _is_github_repository_url(self, project_url):
        """Whether the repository URL is hosted on GitHub: github.com
        only, like the GitHub connection (no Enterprise base URL)."""
        if not project_url:
            return False
        return urlparse(project_url).netloc in ("github.com", "www.github.com")

    @api.model
    def _get_url_platform(self, project_url):
        # Specific claim: the GitHub connection supports github.com
        # only, no Enterprise base URL (claim before super())
        if self._is_github_repository_url(project_url):
            return "github"
        return super()._get_url_platform(project_url)

    def action_open_ci_job_retry_github(self):
        """Open the CI job retry wizard on the first project repository
        hosted on GitHub (production first, same rule that shows the
        button)."""
        self.ensure_one()
        repository = False
        for candidate, project_url in (
            ("production", self.git_project_url),
            ("development", self.git_dev_project_url),
        ):
            if self._is_github_repository_url(project_url):
                repository = candidate
                break
        # This CI job retry is a GitHub-only feature
        if not repository:
            raise UserError(
                _("The CI job retry is available on GitHub repositories only.")
            )
        wizard = self.env["project.github.ci.job.retry"].create(
            {"project_id": self.id, "git_project_url_type": repository}
        )
        return wizard.action_load_jobs()

    def _create_project_webhook_github(self, project_url):
        git_auth = self.env["project.git.auth"]
        github_client = git_auth._connect_github()
        odoo_url = self._get_webhook_url()
        try:
            github_repo = github_client.get_repo(self._git_project_path(project_url))
            for hook in github_repo.get_hooks():
                if hook.config.get("url") == odoo_url:
                    hook.delete()
            github_repo.create_hook(
                # The secret signs the payload (X-Hub-Signature-256), which
                # is how the controller authorizes and recognizes GitHub
                # events
                name="web",
                config={
                    "url": odoo_url,
                    "content_type": "json",
                    "secret": self.env["ir.config_parameter"]
                    .sudo()
                    .get_param("project_github.webhook_secret"),
                    "insecure_ssl": "0",
                },
                events=["push", "pull_request"],
                active=True,
            )
        except GithubException as err:
            raise git_auth._github_api_user_error(err, project_url) from err
