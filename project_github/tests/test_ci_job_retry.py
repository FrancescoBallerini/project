# Copyright 2026 Francesco Ballerini
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from github import GithubException
from github.GithubException import BadCredentialsException, UnknownObjectException

from odoo.exceptions import UserError

from ..models.project_git_auth import ProjectGitAuth
from .common import ProjectGithubCase

GITHUB_COM_REPO_URL = "https://github.com/acme/demo-repo"
GITHUB_API_REPO_URL = "https://api.github.com/repos/acme/demo-repo"


class TestCiJobRetry(ProjectGithubCase):
    """CI job retry wizard: listing of the finished jobs of the last
    workflow runs and per-row retry, with the PyGithub client mocked."""

    def setUp(self):
        super().setUp()
        self.githubcom_project = self.env["project.project"].create(
            {"name": "GitHub.com Repo", "git_project_url": GITHUB_COM_REPO_URL}
        )

    @staticmethod
    def _run_stub(ref, conclusion, jobs=(), run_number=42, triggering_actor=True):
        run = MagicMock(name="github_workflow_run")
        run.run_number = run_number
        run.run_attempt = 1
        run.display_title = "GH-15 Fix the deploy"
        run.head_sha = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
        run.event = "push"
        run.triggering_actor = (
            SimpleNamespace(login="github-dev") if triggering_actor else None
        )
        run.head_branch = ref
        run.conclusion = conclusion
        run.status = "completed"
        run.html_url = f"{GITHUB_COM_REPO_URL}/actions/runs/18400000000{run_number}"
        run.jobs.return_value = list(jobs)
        return run

    # Job stubs are SimpleNamespace like the commit stubs of common.py:
    # a permissive mock would hide missing fields from the line mapping.
    # The job ids are realistic github.com ones, beyond the 32-bit
    # integer range: storing them in an Integer field must fail.

    @staticmethod
    def _job_stub(id_job, name, conclusion, **extra):
        return SimpleNamespace(
            id=id_job,
            name=name,
            conclusion=conclusion,
            workflow_name=extra.get("workflow_name", "CI"),
            started_at=extra.get(
                "started_at", datetime(2026, 1, 2, 7, 0, 0, tzinfo=timezone.utc)
            ),
            completed_at=extra.get(
                "completed_at",
                datetime(2026, 1, 2, 10, 0, 0, tzinfo=timezone(timedelta(hours=2))),
            ),
            html_url=f"{GITHUB_COM_REPO_URL}/actions/runs/18400000000/job/{id_job}",
        )

    def _mock_github_runs(self, runs=()):
        """Return (patcher, github repo mock) with the workflow run
        fixtures installed on the mocked GitHub repository."""
        client = MagicMock(name="github_client")
        github_repo = client.get_repo.return_value
        github_repo.url = GITHUB_API_REPO_URL
        github_repo.get_workflow_runs.return_value = list(runs)
        patcher = patch.object(ProjectGitAuth, "_connect_github", return_value=client)
        return patcher, github_repo

    def _open_wizard(self, runs=()):
        """Open the wizard from the project form button and return
        (wizard, github repo mock)."""
        patcher, github_repo = self._mock_github_runs(runs)
        with patcher:
            action = self.githubcom_project.action_open_ci_job_retry_github()
        wizard = self.env["project.github.ci.job.retry"].browse(action["res_id"])
        return wizard, github_repo

    def test_open_wizard_lists_finished_jobs(self):
        runs = [
            self._run_stub(
                "main",
                "failure",
                jobs=[
                    self._job_stub(41800000011, "tests", "failure"),
                    self._job_stub(41800000012, "build", "success"),
                    # Skipped jobs have no run to retry: left out
                    self._job_stub(41800000013, "deploy", "skipped"),
                ],
                run_number=57,
            ),
            self._run_stub("17.0-dev", "success", jobs=[]),
        ]
        wizard, github_repo = self._open_wizard(runs)
        github_repo.get_workflow_runs.assert_called_once_with()
        for run in runs:
            run.jobs.assert_called_once_with()
        self.assertEqual(len(wizard.line_ids), 2)
        tests_line = wizard.line_ids[0]
        self.assertRecordValues(
            tests_line,
            [
                {
                    "id_job": "41800000011",
                    "name": "tests",
                    "workflow": "CI",
                    "status": "failure",
                    "run_title": "GH-15 Fix the deploy",
                    "commit_sha": "a1b2c3d",
                    "triggered_by": "github-dev",
                    "run_number": 57,
                    "run_attempt": 1,
                    "run_ref": "main",
                    "run_status": "failure",
                    "run_event": "push",
                    "web_url": (
                        f"{GITHUB_COM_REPO_URL}/actions/runs/18400000000"
                        "/job/41800000011"
                    ),
                    "run_url": f"{GITHUB_COM_REPO_URL}/actions/runs/1840000000057",
                }
            ],
        )
        # The +02:00 completed_at is normalized to naive UTC
        self.assertEqual(tests_line.date, datetime(2026, 1, 2, 8, 0, 0))

    def test_job_date_falls_back_to_started_at(self):
        # A job cancelled before finishing may have no completed_at
        never_completed_job = self._job_stub(
            41800000014, "tests", "cancelled", completed_at=None
        )
        wizard, _github_repo = self._open_wizard(
            [self._run_stub("main", "cancelled", jobs=[never_completed_job])]
        )
        self.assertEqual(wizard.line_ids.date, datetime(2026, 1, 2, 7, 0, 0))

    def test_run_without_triggering_actor(self):
        # GitHub may return no triggering actor (e.g. a deleted user)
        wizard, _github_repo = self._open_wizard(
            [
                self._run_stub(
                    "main",
                    "failure",
                    jobs=[self._job_stub(41800000015, "tests", "failure")],
                    triggering_actor=False,
                )
            ]
        )
        self.assertFalse(wizard.line_ids.triggered_by)

    def test_reload_respects_run_count(self):
        wizard, _github_repo = self._open_wizard(
            [self._run_stub("main", "failure", jobs=[])]
        )
        wizard.run_count = 2
        extra_runs = [
            self._run_stub(
                "main",
                "failure",
                jobs=[self._job_stub(41800000011, "tests", "failure")],
                run_number=number,
            )
            for number in (58, 57, 56)
        ]
        patcher, _github_repo = self._mock_github_runs(extra_runs)
        with patcher:
            action = wizard.action_load_jobs()
        # The rows are replaced, not stacked, the run list is cut to
        # run_count, and the wizard reopens itself
        self.assertEqual(len(wizard.line_ids), 2)
        self.assertEqual(wizard.line_ids.mapped("run_number"), [58, 57])
        self.assertEqual(action["res_model"], wizard._name)
        self.assertEqual(action["res_id"], wizard.id)

    def test_reload_without_runs_raises(self):
        wizard, _github_repo = self._open_wizard()
        wizard.run_count = 0
        with self.assertRaisesRegex(UserError, "at least one workflow run"):
            wizard.action_load_jobs()

    def test_open_job_url(self):
        wizard, _github_repo = self._open_wizard(
            [
                self._run_stub(
                    "main",
                    "failure",
                    jobs=[self._job_stub(41800000011, "tests", "failure")],
                )
            ]
        )
        action = wizard.line_ids.action_open_job()
        self.assertEqual(action["type"], "ir.actions.act_url")
        self.assertEqual(
            action["url"],
            f"{GITHUB_COM_REPO_URL}/actions/runs/18400000000/job/41800000011",
        )
        self.assertEqual(action["target"], "new")

    def test_retry_restarts_job_and_notifies(self):
        wizard, _github_repo = self._open_wizard(
            [
                self._run_stub(
                    "main",
                    "failure",
                    jobs=[self._job_stub(41800000011, "tests", "failure")],
                    run_number=57,
                )
            ]
        )
        patcher, github_repo = self._mock_github_runs()
        with patcher:
            action = wizard.line_ids.action_retry()
        github_repo.requester.requestJsonAndCheck.assert_called_once_with(
            "POST", f"{GITHUB_API_REPO_URL}/actions/jobs/41800000011/rerun"
        )
        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual(action["params"]["type"], "success")
        self.assertTrue(action["params"]["sticky"])
        # The rerun reply carries no new job: the link is the run page
        self.assertEqual(
            action["params"]["links"],
            [
                {
                    "label": "tests",
                    "url": f"{GITHUB_COM_REPO_URL}/actions/runs/1840000000057",
                }
            ],
        )

    def test_retry_error_raises_clean_error(self):
        # GitHub refuses the rerun e.g. on a job older than 30 days:
        # the platform message surfaces as a UserError
        wizard, _github_repo = self._open_wizard(
            [
                self._run_stub(
                    "main",
                    "failure",
                    jobs=[self._job_stub(41800000011, "tests", "failure")],
                )
            ]
        )
        patcher, github_repo = self._mock_github_runs()
        github_repo.requester.requestJsonAndCheck.side_effect = GithubException(
            403, {"message": "Unable to re-run this workflow run"}, None
        )
        with patcher, self.assertRaisesRegex(
            UserError, "GitHub API error: Unable to re-run this workflow run"
        ):
            wizard.line_ids.action_retry()

    def test_retry_missing_job_raises_clean_error(self):
        # A 404 on the rerun is about the job, not the repository: the
        # repository was found just before
        wizard, _github_repo = self._open_wizard(
            [
                self._run_stub(
                    "main",
                    "failure",
                    jobs=[self._job_stub(41800000011, "tests", "failure")],
                )
            ]
        )
        patcher, github_repo = self._mock_github_runs()
        github_repo.requester.requestJsonAndCheck.side_effect = UnknownObjectException(
            404, {"message": "Not Found"}, None
        )
        with patcher, self.assertRaisesRegex(
            UserError, "no longer exists on GitHub: reload the jobs"
        ):
            wizard.line_ids.action_retry()

    def test_load_jobs_auth_error_raises_clean_error(self):
        patcher, github_repo = self._mock_github_runs()
        github_repo.get_workflow_runs.side_effect = BadCredentialsException(
            401, {"message": "Bad credentials"}, None
        )
        with patcher, self.assertRaisesRegex(
            UserError, "Bad credentials.*project_github.token"
        ):
            self.githubcom_project.action_open_ci_job_retry_github()

    def test_open_wizard_non_github_url_raises(self):
        # This CI job retry is a GitHub-only feature: same rule that
        # shows the project form button (the shared github_project
        # fixture points to a non-github.com host on purpose)
        self.assertFalse(self.github_project.github_ci_available)
        with self.assertRaisesRegex(UserError, "GitHub"):
            self.github_project.action_open_ci_job_retry_github()

    def test_github_mention_in_repo_name_is_not_github(self):
        # Only the host is matched: a repository merely named after
        # GitHub on another forge does not enable the feature
        self.githubcom_project.git_project_url = (
            "https://forge.example.com/acme/github-mirror"
        )
        self.assertFalse(self.githubcom_project.github_ci_available)
        with self.assertRaisesRegex(UserError, "GitHub"):
            self.githubcom_project.action_open_ci_job_retry_github()

    def test_open_wizard_without_url_raises(self):
        self.githubcom_project.git_project_url = False
        with self.assertRaisesRegex(UserError, "GitHub"):
            self.githubcom_project.action_open_ci_job_retry_github()

    def test_open_wizard_on_dev_repository_url(self):
        # The wizard falls back to the dev repository URL when the
        # production one is not hosted on GitHub
        self.githubcom_project.git_project_url = False
        self.githubcom_project.git_dev_project_url = f"{GITHUB_COM_REPO_URL}-dev"
        wizard, github_repo = self._open_wizard()
        github_repo.get_workflow_runs.assert_called_once_with()
        self.assertEqual(wizard.git_project_url_type, "development")

    def test_reload_on_non_github_repository_raises(self):
        # Selecting a repository whose URL is unset (or not on GitHub)
        # blocks the reload
        wizard, _github_repo = self._open_wizard()
        self.assertEqual(wizard.git_project_url_type, "production")
        wizard.git_project_url_type = "development"
        with self.assertRaisesRegex(UserError, "not hosted on GitHub"):
            wizard.action_load_jobs()

    def test_onchange_repository_clears_lines(self):
        # Switching repository clears the rows of the previous one, so
        # a stale row cannot be retried on the wrong repository
        wizard, _github_repo = self._open_wizard(
            [
                self._run_stub(
                    "main",
                    "failure",
                    jobs=[self._job_stub(41800000011, "tests", "failure")],
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
        self.githubcom_project.git_dev_project_url = f"{GITHUB_COM_REPO_URL}-dev"
        wizard, _github_repo = self._open_wizard()
        patcher, _github_repo = self._mock_github_runs()
        with patcher:
            action = wizard.action_load_jobs()
        labels = dict(
            wizard.with_context(**action["context"])._selection_git_project_url_type()
        )
        self.assertEqual(labels["production"], GITHUB_COM_REPO_URL)
        self.assertEqual(labels["development"], f"{GITHUB_COM_REPO_URL}-dev")
        # Without the context keys the labels fall back to the types
        fallback_labels = dict(wizard._selection_git_project_url_type())
        self.assertEqual(fallback_labels["production"], "Production")
        self.assertEqual(fallback_labels["development"], "Development")
