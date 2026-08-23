# Copyright 2026 Francesco Ballerini
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

import requests
from github import GithubException, RateLimitExceededException
from psycopg2 import IntegrityError

from odoo.tools import mute_logger

from odoo.addons.queue_job.exception import RetryableJobError

from .common import GITHUB_REPO_URL, ProjectGithubCase


class TestGithubPullRequestModel(ProjectGithubCase):
    def test_pr_identifiers_unique_within_platform(self):
        # SQL constraint guarding the search-then-create dedup of the
        # event flow against concurrent jobs on the same PR
        pull_request_vals = {
            "name": "GitHub PR",
            "source": "github",
            "id_project": 2002,
            "id_request": 2,
        }
        self.env["project.git.pull.request"].create(pull_request_vals)
        with (
            self.assertRaises(IntegrityError),
            mute_logger("odoo.sql_db"),
            self.env.cr.savepoint(),
        ):
            self.env["project.git.pull.request"].create(
                dict(pull_request_vals, name="GitHub PR duplicate")
            )

    def test_post_message_transient_api_errors_are_retryable(self):
        # Network failures, rate limits and server errors of the GitHub
        # API (left after the PyGithub retries) postpone the posting job
        # (RetryableJobError); any other API error fails the job as is
        pull_request = self.env["project.git.pull.request"].create(
            {
                "name": "GH-100 retry",
                "source": "github",
                "url": f"{GITHUB_REPO_URL}/pull/8",
                "id_request": 8,
                "id_project": 2002,
                "state": "opened",
            }
        )
        patcher, pull = self._mock_github_client()
        with patcher:
            for transient_error in (
                requests.ConnectionError("connection refused"),
                requests.exceptions.RetryError("retries exhausted"),
                RateLimitExceededException(403, "rate limited"),
                GithubException(502, "bad gateway"),
            ):
                pull.create_issue_comment.side_effect = transient_error
                with self.assertRaises(RetryableJobError):
                    pull_request._post_message("message")
            pull.create_issue_comment.side_effect = GithubException(404, "not found")
            with self.assertRaises(GithubException):
                pull_request._post_message("message")
