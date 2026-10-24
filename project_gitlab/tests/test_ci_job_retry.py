# Copyright 2026 Francesco Ballerini
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from gitlab.exceptions import (
    GitlabAuthenticationError,
    GitlabError,
    GitlabJobRetryError,
)

from odoo.exceptions import UserError

from ..models.project_git_auth import ProjectGitAuth
from ..wizards.project_gitlab_ci_job_retry import FINISHED_JOB_SCOPES
from .common import GITLAB_REPO_URL, ProjectGitlabCase


class TestCiJobRetry(ProjectGitlabCase):
    """CI job retry wizard: listing of the finished jobs of the last
    pipelines and per-row retry, with the python-gitlab client mocked."""

    @staticmethod
    def _pipeline_stub(ref, status, jobs=(), id_pipeline=2980000001):
        pipeline = MagicMock(name="gitlab_pipeline")
        pipeline.id = id_pipeline
        pipeline.ref = ref
        pipeline.status = status
        pipeline.source = "push"
        pipeline.jobs.list.return_value = list(jobs)
        return pipeline

    # Job stubs are SimpleNamespace like the commit stubs of common.py:
    # a permissive mock would hide missing fields from the line mapping.
    # The job and pipeline ids are realistic gitlab.com ones, beyond the
    # 32-bit integer range: storing them in an Integer field must fail.

    @staticmethod
    def _job_stub(id_job, name, status, **extra):
        return SimpleNamespace(
            id=id_job,
            name=name,
            status=status,
            stage=extra.get("stage", "deploy"),
            commit=extra.get(
                "commit", {"title": "GL-15 Fix the deploy", "short_id": "a1b2c3d4"}
            ),
            user=extra.get("user", {"username": "gitlab-dev"}),
            finished_at=extra.get("finished_at", "2026-01-02T10:00:00.000+02:00"),
            created_at=extra.get("created_at", "2026-01-02T07:00:00.000Z"),
            web_url=f"{GITLAB_REPO_URL}/-/jobs/{id_job}",
        )

    def _mock_gitlab_pipelines(self, pipelines=()):
        """Return (patcher, gitlab project mock) with the pipeline
        fixtures installed on the mocked GitLab project."""
        client = MagicMock(name="gitlab_client")
        gitlab_project = client.projects.get.return_value
        gitlab_project.pipelines.list.return_value = list(pipelines)
        patcher = patch.object(ProjectGitAuth, "_connect_gitlab", return_value=client)
        return patcher, gitlab_project

    def _open_wizard(self, pipelines=()):
        """Open the wizard from the project form button and return
        (wizard, gitlab project mock)."""
        patcher, gitlab_project = self._mock_gitlab_pipelines(pipelines)
        with patcher:
            action = self.gitlab_project.action_open_ci_job_retry_gitlab()
        wizard = self.env["project.gitlab.ci.job.retry"].browse(action["res_id"])
        return wizard, gitlab_project

    def test_open_wizard_lists_finished_jobs(self):
        pipelines = [
            self._pipeline_stub(
                "main",
                "failed",
                jobs=[
                    self._job_stub(17400000011, "tests", "failed"),
                    self._job_stub(17400000012, "build", "success", stage="build"),
                ],
                id_pipeline=2980000002,
            ),
            self._pipeline_stub("17.0-dev", "success", jobs=[]),
        ]
        wizard, gitlab_project = self._open_wizard(pipelines)
        gitlab_project.pipelines.list.assert_called_once_with(get_all=False, per_page=5)
        for pipeline in pipelines:
            pipeline.jobs.list.assert_called_once_with(
                scope=FINISHED_JOB_SCOPES, get_all=True
            )
        self.assertEqual(len(wizard.line_ids), 2)
        tests_line = wizard.line_ids[0]
        self.assertRecordValues(
            tests_line,
            [
                {
                    "id_job": "17400000011",
                    "name": "tests",
                    "stage": "deploy",
                    "status": "failed",
                    "commit_title": "GL-15 Fix the deploy",
                    "commit_sha": "a1b2c3d4",
                    "triggered_by": "gitlab-dev",
                    "id_pipeline": "2980000002",
                    "pipeline_ref": "main",
                    "pipeline_status": "failed",
                    "pipeline_source": "push",
                    "web_url": f"{GITLAB_REPO_URL}/-/jobs/17400000011",
                }
            ],
        )
        # The +02:00 finished_at is normalized to naive UTC
        self.assertEqual(tests_line.date, datetime(2026, 1, 2, 8, 0, 0))

    def test_job_date_falls_back_to_created_at(self):
        # A job canceled before running has no finished_at
        never_run_job = self._job_stub(
            17400000013, "tests", "canceled", finished_at=None
        )
        wizard, _gitlab_project = self._open_wizard(
            [self._pipeline_stub("main", "canceled", jobs=[never_run_job])]
        )
        self.assertEqual(wizard.line_ids.date, datetime(2026, 1, 2, 7, 0, 0))

    def test_job_without_commit_and_user(self):
        # The nested commit and user payloads may be missing or null
        job = self._job_stub(17400000014, "tests", "failed", commit=None, user=None)
        wizard, _gitlab_project = self._open_wizard(
            [self._pipeline_stub("main", "failed", jobs=[job])]
        )
        self.assertRecordValues(
            wizard.line_ids,
            [{"commit_title": False, "commit_sha": False, "triggered_by": False}],
        )

    def test_reload_respects_pipeline_count(self):
        wizard, _gitlab_project = self._open_wizard(
            [self._pipeline_stub("main", "failed", jobs=[])]
        )
        wizard.pipeline_count = 2
        patcher, gitlab_project = self._mock_gitlab_pipelines(
            [
                self._pipeline_stub(
                    "main",
                    "failed",
                    jobs=[self._job_stub(17400000011, "tests", "failed")],
                )
            ]
        )
        with patcher:
            action = wizard.action_load_jobs()
        gitlab_project.pipelines.list.assert_called_once_with(get_all=False, per_page=2)
        # The rows are replaced, not stacked, and the wizard reopens itself
        self.assertEqual(len(wizard.line_ids), 1)
        self.assertEqual(action["res_model"], wizard._name)
        self.assertEqual(action["res_id"], wizard.id)

    def test_reload_without_pipelines_raises(self):
        wizard, _gitlab_project = self._open_wizard()
        wizard.pipeline_count = 0
        with self.assertRaisesRegex(UserError, "at least one pipeline"):
            wizard.action_load_jobs()

    def test_open_job_url(self):
        wizard, _gitlab_project = self._open_wizard(
            [
                self._pipeline_stub(
                    "main",
                    "failed",
                    jobs=[self._job_stub(17400000011, "tests", "failed")],
                )
            ]
        )
        action = wizard.line_ids.action_open_job()
        self.assertEqual(action["type"], "ir.actions.act_url")
        self.assertEqual(action["url"], f"{GITLAB_REPO_URL}/-/jobs/17400000011")
        self.assertEqual(action["target"], "new")

    def test_retry_creates_new_job_and_notifies(self):
        wizard, _gitlab_project = self._open_wizard(
            [
                self._pipeline_stub(
                    "main",
                    "failed",
                    jobs=[self._job_stub(17400000011, "tests", "failed")],
                )
            ]
        )
        patcher, gitlab_project = self._mock_gitlab_pipelines()
        new_job_url = f"{GITLAB_REPO_URL}/-/jobs/99"
        gitlab_project.jobs.get.return_value.retry.return_value = {
            "id": 99,
            "web_url": new_job_url,
        }
        with patcher:
            action = wizard.line_ids.action_retry()
        gitlab_project.jobs.get.assert_called_once_with("17400000011", lazy=True)
        gitlab_project.jobs.get.return_value.retry.assert_called_once_with()
        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual(action["params"]["type"], "success")
        self.assertTrue(action["params"]["sticky"])
        self.assertEqual(
            action["params"]["links"], [{"label": "tests", "url": new_job_url}]
        )

    def test_retry_error_raises(self):
        wizard, _gitlab_project = self._open_wizard(
            [
                self._pipeline_stub(
                    "main",
                    "failed",
                    jobs=[self._job_stub(17400000011, "tests", "failed")],
                )
            ]
        )
        patcher, gitlab_project = self._mock_gitlab_pipelines()
        gitlab_project.jobs.get.return_value.retry.side_effect = GitlabJobRetryError()
        with patcher, self.assertRaisesRegex(UserError, "cannot be retried"):
            wizard.line_ids.action_retry()

    def test_load_jobs_auth_error_raises_clean_error(self):
        # A token the instance rejects (expired, revoked, mistyped)
        # surfaces as a UserError pointing at the token sysparam, not
        # as a raw traceback
        patcher, gitlab_project = self._mock_gitlab_pipelines()
        gitlab_project.pipelines.list.side_effect = GitlabAuthenticationError(
            "401 Unauthorized", response_code=401
        )
        expected_error = (
            "401 Unauthorized.*project_gitlab.token.https://gitlab.example.com/"
        )
        with patcher, self.assertRaisesRegex(UserError, expected_error):
            self.gitlab_project.action_open_ci_job_retry_gitlab()

    def test_load_jobs_not_found_names_the_project(self):
        # The 404 names the exact project path we looked up: unlike
        # GitLab's own message, it tells the user what to recheck
        patcher, gitlab_project = self._mock_gitlab_pipelines()
        gitlab_project.pipelines.list.side_effect = GitlabError(
            "404 Project Not Found", response_code=404
        )
        with patcher, self.assertRaisesRegex(
            UserError, 'cannot find the project "acme/demo-repo"'
        ):
            self.gitlab_project.action_open_ci_job_retry_gitlab()

    def test_load_jobs_api_error_raises_clean_error(self):
        # Any other GitLab API error surfaces as a UserError carrying
        # the platform message verbatim
        patcher, gitlab_project = self._mock_gitlab_pipelines()
        gitlab_project.pipelines.list.side_effect = GitlabError(
            "403 Forbidden", response_code=403
        )
        with patcher, self.assertRaisesRegex(
            UserError, "GitLab API error: 403 Forbidden"
        ):
            self.gitlab_project.action_open_ci_job_retry_gitlab()

    def test_retry_auth_error_raises_clean_error(self):
        wizard, _gitlab_project = self._open_wizard(
            [
                self._pipeline_stub(
                    "main",
                    "failed",
                    jobs=[self._job_stub(17400000011, "tests", "failed")],
                )
            ]
        )
        patcher, gitlab_project = self._mock_gitlab_pipelines()
        gitlab_project.jobs.get.return_value.retry.side_effect = (
            GitlabAuthenticationError("401 Unauthorized", response_code=401)
        )
        with patcher, self.assertRaisesRegex(UserError, "rejected the API call"):
            wizard.line_ids.action_retry()

    def test_open_wizard_non_gitlab_url_raises(self):
        # The CI job retry is GitLab-only: same rule that shows the
        # project form button
        self.gitlab_project.git_project_url = "https://forge.example.com/acme/repo"
        self.assertFalse(self.gitlab_project.gitlab_ci_available)
        with self.assertRaisesRegex(UserError, "GitLab"):
            self.gitlab_project.action_open_ci_job_retry_gitlab()

    def test_gitlab_mention_in_repo_name_is_not_gitlab(self):
        # Only the host is matched: a repository merely named after
        # GitLab on another forge does not enable the feature
        self.gitlab_project.git_project_url = (
            "https://forge.example.com/acme/gitlab-mirror"
        )
        self.assertFalse(self.gitlab_project.gitlab_ci_available)
        with self.assertRaisesRegex(UserError, "GitLab"):
            self.gitlab_project.action_open_ci_job_retry_gitlab()

    def test_custom_domain_with_token_is_gitlab(self):
        # A self-hosted instance on a host without "gitlab" in the name
        # is recognized through its configured API token parameter
        self.gitlab_project.git_project_url = "https://git.example.com/acme/repo"
        self.assertFalse(self.gitlab_project.gitlab_ci_available)
        self.env["ir.config_parameter"].sudo().set_param(
            "project_gitlab.token.https://git.example.com/", "real-token"
        )
        self.gitlab_project.invalidate_recordset(["gitlab_ci_available"])
        self.assertTrue(self.gitlab_project.gitlab_ci_available)
        patcher, _gitlab_project = self._mock_gitlab_pipelines()
        with patcher as connect_gitlab:
            self.gitlab_project.action_open_ci_job_retry_gitlab()
        connect_gitlab.assert_called_once_with(url="https://git.example.com/acme/repo")

    def test_open_wizard_without_url_raises(self):
        self.gitlab_project.git_project_url = False
        with self.assertRaisesRegex(UserError, "GitLab"):
            self.gitlab_project.action_open_ci_job_retry_gitlab()

    def test_open_wizard_on_dev_repository_url(self):
        # The wizard falls back to the dev repository URL when the
        # production one is not hosted on GitLab
        self.gitlab_project.git_project_url = False
        self.gitlab_project.git_dev_project_url = f"{GITLAB_REPO_URL}-dev"
        patcher, _gitlab_project = self._mock_gitlab_pipelines()
        with patcher as connect_gitlab:
            action = self.gitlab_project.action_open_ci_job_retry_gitlab()
        connect_gitlab.assert_called_once_with(url=f"{GITLAB_REPO_URL}-dev")
        wizard = self.env["project.gitlab.ci.job.retry"].browse(action["res_id"])
        self.assertEqual(wizard.git_project_url_type, "development")

    def test_reload_on_non_gitlab_repository_raises(self):
        # Selecting a repository whose URL is unset (or not on GitLab)
        # blocks the reload
        wizard, _gitlab_project = self._open_wizard()
        self.assertEqual(wizard.git_project_url_type, "production")
        wizard.git_project_url_type = "development"
        with self.assertRaisesRegex(UserError, "not hosted on GitLab"):
            wizard.action_load_jobs()

    def test_onchange_repository_clears_lines(self):
        # Switching repository clears the rows of the previous one, so
        # a stale row cannot be retried on the wrong repository
        wizard, _gitlab_project = self._open_wizard(
            [
                self._pipeline_stub(
                    "main",
                    "failed",
                    jobs=[self._job_stub(17400000011, "tests", "failed")],
                )
            ]
        )
        self.assertTrue(wizard.line_ids)
        wizard.git_project_url_type = "development"
        wizard._onchange_git_project_url_type()
        self.assertFalse(wizard.line_ids)

    def test_repository_radio_labels_show_urls(self):
        # The wizard action carries the two URLs in _view_ref-suffixed
        # context keys (the only custom keys the web client keeps when
        # loading the views): they become the labels of the radio
        self.gitlab_project.git_dev_project_url = f"{GITLAB_REPO_URL}-dev"
        wizard, _gitlab_project = self._open_wizard()
        patcher, _gitlab_project = self._mock_gitlab_pipelines()
        with patcher:
            action = wizard.action_load_jobs()
        labels = dict(
            wizard.with_context(**action["context"])._selection_git_project_url_type()
        )
        self.assertEqual(labels["production"], f"{GITLAB_REPO_URL}.git")
        self.assertEqual(labels["development"], f"{GITLAB_REPO_URL}-dev")
        # Without the context keys the labels fall back to the types
        fallback_labels = dict(wizard._selection_git_project_url_type())
        self.assertEqual(fallback_labels["production"], "Production")
        self.assertEqual(fallback_labels["development"], "Development")
