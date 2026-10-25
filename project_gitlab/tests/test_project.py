# Copyright 2026 Francesco Ballerini
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

from unittest.mock import MagicMock, patch

from gitlab.exceptions import GitlabAuthenticationError, GitlabError

from odoo.exceptions import UserError

from ..models.project_git_auth import ProjectGitAuth
from .common import (
    GITLAB_INSTANCE_ROOT,
    GITLAB_REPO_URL,
    TEST_SIGNING_SECRET,
    ProjectGitlabCase,
)

ODOO_BASE_URL = "https://odoo.example.com"
WEBHOOK_TOKEN = "webhook-secret-token"


class TestProjectWebhookDeploy(ProjectGitlabCase):
    """Webhook deployment on the GitLab repo, triggered from the Odoo
    project form."""

    def setUp(self):
        super().setUp()
        config = self.env["ir.config_parameter"].sudo()
        config.set_param("web.base.url", ODOO_BASE_URL)
        # The deploy reads the per-instance secret of the repo instance root
        config.set_param(
            f"project_gitlab.webhook_secret.{GITLAB_INSTANCE_ROOT}", WEBHOOK_TOKEN
        )
        self.expected_hook_url = f"{ODOO_BASE_URL}/project_git/webhook/"

    def _mock_gitlab_for_project(self, hooks=()):
        """Return (patcher, gitlab client mock) with hook fixtures
        installed on the mocked GitLab project."""
        client = MagicMock(name="gitlab_client")
        gitlab_project = client.projects.get.return_value
        gitlab_project.hooks.list.return_value = list(hooks)
        patcher = patch.object(ProjectGitAuth, "_connect_gitlab", return_value=client)
        return patcher, client

    @staticmethod
    def _hook_stub(url):
        hook = MagicMock(name="gitlab_hook")
        hook.url = url
        return hook

    def test_create_webhook_creates_hook_with_events(self):
        patcher, client = self._mock_gitlab_for_project()
        with patcher as connect_gitlab:
            self.gitlab_project.create_project_webhook()
        connect_gitlab.assert_called_once_with(url=f"{GITLAB_REPO_URL}.git")
        # Trailing .git is stripped from the API project path
        client.projects.get.assert_called_once_with("acme/demo-repo")
        client.projects.get.return_value.hooks.create.assert_called_once_with(
            {
                "url": self.expected_hook_url,
                "push_events": True,
                "merge_requests_events": True,
                "pipeline_events": True,
                "enable_ssl_verification": True,
                "token": WEBHOOK_TOKEN,
            }
        )

    def test_create_webhook_with_signing_secret(self):
        # A whsec_ secret is installed as the signing_token attribute
        # of the hook instead of the legacy plain token
        self.env["ir.config_parameter"].sudo().set_param(
            f"project_gitlab.webhook_secret.{GITLAB_INSTANCE_ROOT}",
            TEST_SIGNING_SECRET,
        )
        patcher, client = self._mock_gitlab_for_project()
        with patcher:
            self.gitlab_project.create_project_webhook()
        hook_vals = client.projects.get.return_value.hooks.create.call_args.args[0]
        self.assertEqual(hook_vals["signing_token"], TEST_SIGNING_SECRET)
        self.assertNotIn("token", hook_vals)

    def test_create_webhook_replaces_existing_hook(self):
        stale_hook = self._hook_stub(self.expected_hook_url)
        foreign_hook = self._hook_stub("https://ci.example.com/hook")
        patcher, client = self._mock_gitlab_for_project(
            hooks=[stale_hook, foreign_hook]
        )
        with patcher:
            self.gitlab_project.create_project_webhook()
        # Every page of hooks is scanned, not only the first one
        client.projects.get.return_value.hooks.list.assert_called_once_with(
            get_all=True
        )
        stale_hook.delete.assert_called_once_with()
        foreign_hook.delete.assert_not_called()
        client.projects.get.return_value.hooks.create.assert_called_once()

    def test_create_webhook_on_both_project_urls(self):
        self.gitlab_project.git_dev_project_url = f"{GITLAB_REPO_URL}-dev"
        patcher, client = self._mock_gitlab_for_project()
        with patcher:
            self.gitlab_project.create_project_webhook()
        self.assertEqual(
            [call.args for call in client.projects.get.call_args_list],
            [("acme/demo-repo",), ("acme/demo-repo-dev",)],
        )
        self.assertEqual(client.projects.get.return_value.hooks.create.call_count, 2)

    def test_create_webhook_without_url_does_nothing(self):
        self.gitlab_project.git_project_url = False
        patcher, _client = self._mock_gitlab_for_project()
        with patcher as connect_gitlab:
            self.gitlab_project.create_project_webhook()
        connect_gitlab.assert_not_called()

    def test_create_webhook_auth_error_raises_clean_error(self):
        # A token the instance rejects surfaces as a UserError pointing
        # at the token sysparam, not as a raw traceback
        patcher, client = self._mock_gitlab_for_project()
        client.projects.get.side_effect = GitlabAuthenticationError(
            "401 Unauthorized", response_code=401
        )
        with patcher, self.assertRaisesRegex(
            UserError, "401 Unauthorized.*project_gitlab.token"
        ):
            self.gitlab_project.create_project_webhook()

    def test_create_webhook_not_found_names_the_project(self):
        # The 404 names the exact project path we looked up
        patcher, client = self._mock_gitlab_for_project()
        client.projects.get.side_effect = GitlabError(
            "404 Project Not Found", response_code=404
        )
        with patcher, self.assertRaisesRegex(
            UserError, 'cannot find the project "acme/demo-repo"'
        ):
            self.gitlab_project.create_project_webhook()

    def test_create_webhook_api_error_raises_clean_error(self):
        # Any other GitLab API error surfaces as a UserError carrying
        # the platform message verbatim
        patcher, client = self._mock_gitlab_for_project()
        client.projects.get.side_effect = GitlabError(
            "403 Forbidden", response_code=403
        )
        with patcher, self.assertRaisesRegex(
            UserError, "GitLab API error: 403 Forbidden"
        ):
            self.gitlab_project.create_project_webhook()
