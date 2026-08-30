# Copyright 2020, Jarsa
# Copyright 2026 Francesco Ballerini
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

from github import GithubException, RateLimitExceededException

from odoo import _, fields, models

from odoo.addons.project_git.models.project_git_utils import (
    TRANSIENT_HTTP_CODES,
    TRANSIENT_REQUEST_ERRORS,
)
from odoo.addons.queue_job.exception import RetryableJobError


class ProjectGitPullRequest(models.Model):
    _inherit = "project.git.pull.request"

    source = fields.Selection(selection_add=[("github", "GitHub")])

    def _is_pr_opening_github(self, event):
        return event.get("action") == "opened"

    def _assign_tags_to_task_github(self, task):
        """Align the PR state tag of a single related task (the bridge
        tracks no CI/approval/draft state yet, see ROADMAP)."""
        self.ensure_one()
        managed_tags = task.tag_ids.filtered(lambda t: t.name.startswith("PR:"))
        tags_to_add = self.env.ref("project_github.project_tags_" + self.state)
        self._replace_task_tags(task, managed_tags, tags_to_add)

    def _post_message_github(self, message, event=None):
        """Post a comment on the GitHub pull request.

        Two-level retry: PyGithub retries on the spot (default
        GithubRetry: rate limits - waiting for the reset -, server
        errors, network failures); the transient errors caught here
        are the ones left after its retries and postpone the job
        (queue_job default retry interval and max retries), covering
        a longer API unavailability without blocking the queue. Any
        other error fails the job as is.
        """
        if self:
            self.ensure_one()
            repository_id, request_id = self.id_repository, self.id_request
        else:
            repository_id = event["repository"]["id"]
            request_id = event["number"]
        github = self.env["project.git.auth"]._connect_github()
        try:
            repo = github.get_repo(repository_id)
            pull = repo.get_pull(request_id)
            pull.create_issue_comment(message)
        except TRANSIENT_REQUEST_ERRORS as error:
            raise RetryableJobError(_("GitHub API unreachable: %s", error)) from error
        except RateLimitExceededException as error:
            raise RetryableJobError(_("GitHub API rate limit exceeded")) from error
        except GithubException as error:
            if error.status not in TRANSIENT_HTTP_CODES:
                raise
            raise RetryableJobError(
                _("GitHub API temporary error (%s)", error.status)
            ) from error
        return True
