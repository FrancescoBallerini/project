# Copyright 2020, Jarsa
# Copyright 2026 Francesco Ballerini
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

from gitlab.exceptions import GitlabError

from odoo import _, fields, models

from odoo.addons.project_git.models.project_git_utils import (
    TRANSIENT_HTTP_CODES,
    TRANSIENT_REQUEST_ERRORS,
)
from odoo.addons.queue_job.exception import RetryableJobError


class ProjectGitPullRequest(models.Model):
    _inherit = "project.git.pull.request"

    source = fields.Selection(selection_add=[("gitlab", "GitLab")])

    def _is_pr_opening_gitlab(self, event):
        return event.get("object_attributes", {}).get("action") == "open"

    def _assign_tags_to_task_gitlab(self, task):
        """Align the MR/CI state tags of a single related task."""
        self.ensure_one()
        managed_tags = task.tag_ids.filtered(
            lambda t: t.name.startswith(("MR:", "CI:")) or t.name in ("Approved", "WIP")
        )
        prefix = "project_gitlab.project_tags_"
        tags_to_add = self.env.ref(prefix + self.state)
        if self.ci_status:
            tags_to_add |= self.env.ref(prefix + self.ci_status)
        if self.approved:
            tags_to_add |= self.env.ref(prefix + "approved")
        if self.wip:
            tags_to_add |= self.env.ref(prefix + "wip")
        self._replace_task_tags(task, managed_tags, tags_to_add)

    def _post_message_gitlab(self, message, event=None):
        """Post a comment (discussion) on the GitLab merge request.

        Two-level retry: python-gitlab retries on the spot (rate limits
        - honouring Retry-After -, server errors and network failures,
        see _connect_gitlab); the transient errors caught here are the
        ones left after its retries and postpone the job (queue_job
        default retry interval and max retries), covering a longer
        API unavailability without blocking the queue. Any other
        error fails the job as is.

        The event, when available, is the preferred source for the GitLab
        instance base URL (project.web_url is authoritative on any GitLab
        version). Without an event the URL falls back to the record
        instance URL, always set by the event flow.
        """
        if self:
            self.ensure_one()
            project_id, request_id = self.id_repository, self.id_request
        else:
            project_id = event["project"]["id"]
            request_id = event["object_attributes"]["iid"]
        web_url = event["project"]["web_url"] if event else self.instance_url
        gitlab_client = self.env["project.git.auth"]._connect_gitlab(url=web_url)
        try:
            project = gitlab_client.projects.get(project_id)
            merge_request = project.mergerequests.get(request_id)
            merge_request.discussions.create({"body": message})
        except TRANSIENT_REQUEST_ERRORS as error:
            raise RetryableJobError(_("GitLab API unreachable: %s", error)) from error
        except GitlabError as error:
            if error.response_code not in TRANSIENT_HTTP_CODES:
                raise
            raise RetryableJobError(
                _("GitLab API temporary error (%s)", error.response_code)
            ) from error
        return True
