# Copyright 2020, Jarsa
# Copyright 2026 Francesco Ballerini
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

import logging

from github import Auth, Github
from github.GithubException import BadCredentialsException

from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ProjectGitAuth(models.AbstractModel):
    _inherit = "project.git.auth"

    @api.model
    def _connect_github(self):
        """Connect to github instance and return github object"""
        return Github(auth=Auth.Token(self._get_token_param("project_github.token")))

    @api.model
    def _github_api_user_error(self, err, project_url):
        """Actionable UserError for a PyGithub API error, to raise
        from it. The original traceback is logged here: the HTTP layer
        logs a UserError without it."""
        _logger.warning("GitHub API error while calling %s", project_url, exc_info=err)
        # The readable text of a GithubException lives in its data
        # payload (err.message is unset on API errors)
        error_message = (
            err.data.get("message") if isinstance(err.data, dict) else err.data
        ) or str(err)
        if isinstance(err, BadCredentialsException):
            # The token passed the demo-default guard but GitHub
            # rejects it: expired, revoked or mistyped
            return UserError(
                _(
                    "GitHub rejected the API call (%(error)s): check the "
                    "token of the project_github.token system parameter.",
                    error=error_message,
                )
            )
        if err.status == 404:
            # GitHub also answers 404 (not 403) for a private
            # repository the token cannot see, to not disclose its
            # existence
            return UserError(
                _(
                    'GitHub cannot find the repository "%(path)s" (from '
                    "the repository URL %(url)s): check how the URL is "
                    "written, and that the API token can access the "
                    "repository.",
                    path=self.env["project.project"]._git_project_path(project_url),
                    url=project_url,
                )
            )
        return UserError(_("GitHub API error: %s", error_message))
