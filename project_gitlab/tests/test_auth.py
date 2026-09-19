# Copyright 2026 Francesco Ballerini
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

from unittest.mock import patch

from odoo.addons.project_gitlab.models import project_git_auth

from .common import GITLAB_INSTANCE_ROOT, GITLAB_REPO_URL, ProjectGitlabCase


class TestGitlabAuth(ProjectGitlabCase):
    """GitLab client construction from a project URL."""

    def test_connect_gitlab_builds_client_for_instance(self):
        # The instance root derived from the project URL selects the
        # per-instance token sysparam and becomes the client URL; the
        # client retries the transient API errors on its own
        self.env["ir.config_parameter"].sudo().set_param(
            "project_gitlab.token." + GITLAB_INSTANCE_ROOT, "s3cr3t-t0k3n"
        )
        with patch.object(project_git_auth.gitlab, "Gitlab") as gitlab_client:
            self.env["project.git.auth"]._connect_gitlab(url=GITLAB_REPO_URL)
        gitlab_client.assert_called_once_with(
            GITLAB_INSTANCE_ROOT,
            private_token="s3cr3t-t0k3n",
            retry_transient_errors=True,
        )
