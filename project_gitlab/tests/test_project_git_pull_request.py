# Copyright 2026 Francesco Ballerini
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

import requests
from gitlab.exceptions import GitlabCreateError
from psycopg2 import IntegrityError

from odoo.tools import mute_logger

from odoo.addons.queue_job.exception import RetryableJobError

from .common import GITLAB_REPO_URL, ProjectGitlabCase


class TestGitlabPullRequestModel(ProjectGitlabCase):
    def test_mr_identifiers_unique_within_platform_instance(self):
        # SQL constraint guarding the search-then-create dedup of the
        # event flow against concurrent jobs on the same MR. The platform
        # ids are scoped by the instance: the same ids on another GitLab
        # instance identify a different MR and get their own record.
        pull_request_vals = {
            "name": "GitLab MR",
            "source": "gitlab",
            "instance_url": "https://gitlab.example.com/",
            "id_repository": 1001,
            "id_request": 1,
        }
        self.env["project.git.pull.request"].create(pull_request_vals)
        with (
            self.assertRaises(IntegrityError),
            mute_logger("odoo.sql_db"),
            self.env.cr.savepoint(),
        ):
            self.env["project.git.pull.request"].create(
                dict(pull_request_vals, name="GitLab MR duplicate")
            )
        self.env["project.git.pull.request"].create(
            dict(
                pull_request_vals,
                name="Same ids on another instance",
                instance_url="https://gitlab-other.example.com/",
            )
        )
        self.env.flush_all()

    def test_post_message_without_event_connects_via_record_instance_url(self):
        # Without an event the GitLab connection URL falls back to the
        # record instance URL; the event, when present, stays the
        # preferred source.
        pull_request = self.env["project.git.pull.request"].create(
            {
                "name": "GL-100 fallback",
                "source": "gitlab",
                "url": f"{GITLAB_REPO_URL}/-/merge_requests/7",
                "instance_url": "https://gitlab.example.com/",
                "id_request": 7,
                "id_repository": 1001,
                "state": "opened",
            }
        )
        patcher, merge_request = self._mock_gitlab_client()
        with patcher as connect_gitlab:
            pull_request._post_message("fallback message")

        self.assertEqual(
            connect_gitlab.call_args.kwargs.get("url"), "https://gitlab.example.com/"
        )
        merge_request.discussions.create.assert_called_once_with(
            {"body": "fallback message"}
        )

    def test_post_message_transient_api_errors_are_retryable(self):
        # Network failures, rate limits and server errors of the GitLab
        # API postpone the posting job (RetryableJobError); any other
        # API error fails the job as is
        pull_request = self.env["project.git.pull.request"].create(
            {
                "name": "GL-100 retry",
                "source": "gitlab",
                "url": f"{GITLAB_REPO_URL}/-/merge_requests/8",
                "instance_url": "https://gitlab.example.com/",
                "id_request": 8,
                "id_repository": 1001,
                "state": "opened",
            }
        )
        patcher, merge_request = self._mock_gitlab_client()
        with patcher:
            for transient_error in (
                requests.ConnectionError("connection refused"),
                GitlabCreateError("rate limited", response_code=429),
                GitlabCreateError("bad gateway", response_code=502),
            ):
                merge_request.discussions.create.side_effect = transient_error
                with self.assertRaises(RetryableJobError):
                    pull_request._post_message("message")
            merge_request.discussions.create.side_effect = GitlabCreateError(
                "forbidden", response_code=403
            )
            with self.assertRaises(GitlabCreateError):
                pull_request._post_message("message")
