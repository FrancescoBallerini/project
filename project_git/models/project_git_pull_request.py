# Copyright 2020, Jarsa
# Copyright 2026 Francesco Ballerini
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class ProjectGitPullRequest(models.Model):
    _name = "project.git.pull.request"
    _description = "Git Pull/Merge Request"

    name = fields.Char(string="Title")
    description = fields.Text()
    url = fields.Char(string="PR/MR URL")

    id_request = fields.Integer(
        string="Request ID", help="Technical field used to track the merge request id"
    )
    id_project = fields.Integer(
        string="Project ID",
        help="Technical field used to track the project id on the platform",
    )
    # Each platform bridge adds its own value with selection_add
    source = fields.Selection([], string="Source Platform")

    source_branch = fields.Char()
    target_branch = fields.Char()
    source_branch_id = fields.Many2one(
        comodel_name="project.git.branch",
        string="Source Branch Record",
        help="The tracked branch record of the source branch, when "
        "the branch is tracked in Odoo (target branches are never "
        "tracked, so they stay as plain names).",
    )

    state = fields.Selection(
        [
            ("opened", "Opened"),
            ("merged", "Merged"),
            ("closed", "Closed"),
            ("locked", "Locked"),
        ]
    )
    ci_status = fields.Selection(
        [
            ("pending", "Pending"),
            ("running", "Running"),
            ("success", "Success"),
            ("failed", "Failed"),
            ("skipped", "Skipped"),
            ("canceled", "Canceled"),
            ("unknown", "Unknown"),
        ],
        string="CI Status",
    )

    wip = fields.Boolean(string="WIP")
    approved = fields.Boolean()

    last_commit = fields.Char()
    user_id = fields.Many2one("res.users", string="Created by User")

    task_ids = fields.Many2many(
        comodel_name="project.task",
        relation="project_git_pull_request_task_rel",
        column1="pull_request_id",
        column2="task_id",
        string="Related Tasks",
    )
    notified_task_ids = fields.Many2many(
        comodel_name="project.task",
        relation="project_git_pull_request_notified_task_rel",
        column1="pull_request_id",
        column2="task_id",
        string="Notified Tasks",
        help="Tasks whose link has already been posted as a message on the "
        "PR/MR, used to avoid posting the same task link twice.",
    )

    git_commit_ids = fields.Many2many(
        comodel_name="project.git.commit",
        relation="project_git_pull_request_commit_rel",
        column1="pull_request_id",
        column2="commit_id",
        string="Commits",
    )

    _sql_constraints = [
        (
            "source_project_request_unique",
            "unique(source, id_project, id_request)",
            "A pull request with the same identifiers is already tracked"
            " for this platform.",
        )
    ]

    def open_merge_request(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": self.url,
        }

    @api.model_create_multi
    def create(self, vals_list):
        rec = super().create(vals_list)
        rec.assign_tags()
        return rec

    def write(self, vals):
        res = super().write(vals)
        self.assign_tags()
        return res

    def _post_task_link_messages(self, event):
        """Post a message on the PR/MR with the link to each related task.

        Each message is posted by a dedicated queue job (see
        _post_message): the notified tasks are flagged in
        notified_task_ids in the same transaction that enqueues the
        jobs, so that each task link is posted only once per pull
        request (avoids message spam, since PR/MR events fire on every
        update) and a failed post can be retried without duplicates.

        :param dict event: The webhook event (passed down to the
            per-source _post_message implementations, e.g. for the
            platform connection)
        :return: recordset of the tasks notified by this call
        """
        self.ensure_one()
        git_pull_request = self.sudo()
        tasks_to_notify = git_pull_request.task_ids - git_pull_request.notified_task_ids
        # The job descriptions locate the PR/MR by its platform
        # coordinates (unique, unlike the title)
        platform_label = dict(
            self._fields["source"].get_description(self.env)["selection"]
        )[git_pull_request.source]
        for task in tasks_to_notify:
            url = task._notify_get_action_link("view")
            message = _(
                "Linked to Odoo task [#%(id)s](%(url)s)",
                id=task.id,
                url=url,
            )
            git_pull_request.with_delay(
                description=_(
                    "%(platform)s: Post task #%(task_id)s link on "
                    "Request ID=%(id_request)s (Repo ID=%(id_project)s)",
                    platform=platform_label,
                    task_id=task.id,
                    id_request=git_pull_request.id_request,
                    id_project=git_pull_request.id_project,
                ),
                identity_key=f"project_git.task_link:{git_pull_request.id}:{task.id}",
            )._post_message(message, event)
        if tasks_to_notify:
            git_pull_request.notified_task_ids = [
                (4, task.id) for task in tasks_to_notify
            ]
        return tasks_to_notify

    @api.model
    def _is_pr_opening_or_title_change(self, event):
        """Return True when the PR/MR is being opened or its title edited.

        These are the only moments when title-based feedback is
        actionable: PR/MR events fire on every update, so negative-result
        messages must not be reposted each time.

        The title change check (changes.title) is common to every
        platform; the opening detection is per-source.
        """
        is_opening = False
        if hasattr(self, f"_is_pr_opening_{event.get('source')}"):
            is_opening = getattr(self, f"_is_pr_opening_{event.get('source')}")(event)
        title_changed = bool(event.get("changes", {}).get("title"))
        return is_opening or title_changed

    @api.model
    def _post_negative_match_messages(
        self,
        event,
        matching_tasks=None,
        title_task_references=None,
        repository_projects=None,
    ):
        """Warn on the PR/MR about broken or missing task references.

        - explicit "taskid#<id>" title reference(s) to tasks that do not
          exist;
        - no task reference at all (only for repositories related to an
          Odoo project, to avoid commenting unrelated repositories).
        Posted only on PR opening or title change (anti-spam), by a
        dedicated queue job per message (see _post_message). Model
        method: in these cases the PR/MR is usually not tracked in Odoo,
        so the message posting relies on the event for identification.

        Every input but the event is optional: the event processor
        passes the values it already computed, while a bare call derives
        them from the event (None default: an empty value is legitimate).

        :param dict event: The webhook event
        :param matching_tasks: project.task recordset matched by the
            PR/MR (see project.git.event._find_pr_matching_tasks)
        :param list(int) title_task_references: task ids referenced in
            the PR/MR title (see
            project.git.utils._extract_task_id_references)
        :param repository_projects: project.project recordset related
            to the event repository
        """
        if not self._is_pr_opening_or_title_change(event):
            return
        git_event = self.env["project.git.event"]
        if repository_projects is None:
            repository_projects = git_event._get_related_projects_by_url(event=event)
        if matching_tasks is None:
            matching_tasks, _commit_matches = git_event._find_pr_matching_tasks(
                event, repository_projects=repository_projects
            )
        if title_task_references is None:
            pr_title = git_event._extract_pr_title_from_event(event)
            title_task_references = self.env[
                "project.git.utils"
            ]._extract_task_id_references(pr_title)
        referenced_tasks = (
            self.env["project.task"].sudo().browse(title_task_references).exists()
        )
        missing_task_ids = [
            task_id
            for task_id in title_task_references
            if task_id not in referenced_tasks.ids
        ]
        # The PR/MR is identified by its platform ids (no record to
        # rely on: the PR/MR is usually not tracked)
        id_project, id_request = git_event._dispatch_by_source(
            event, "_extract_pr_identifiers"
        )
        platform_label = dict(
            self._fields["source"].get_description(self.env)["selection"]
        )[event.get("source")]
        if missing_task_ids:
            # Broken explicit reference(s): "taskid#<id>" in the title
            # pointing to tasks that do not exist (prevails on the other warning)
            message = _(
                "The task id(s) %(ids)s cannot be found in Odoo.",
                ids=", ".join(f"#{task_id}" for task_id in missing_task_ids),
            )
            job_description = _(
                "%(platform)s: Post missing tasks warning on "
                "Request ID=%(id_request)s (Repo ID=%(id_project)s)",
                platform=platform_label,
                id_request=id_request,
                id_project=id_project,
            )
            warning_kind = "missing_tasks"
        elif not matching_tasks and repository_projects:
            # No task matched at all, on a repository linked to an Odoo
            # project (unrelated repositories are left alone)
            message = self.env["ir.qweb"]._render(
                "project_git.no_task_reference_in_title"
            )
            job_description = _(
                "%(platform)s: Post no reference warning on "
                "Request ID=%(id_request)s (Repo ID=%(id_project)s)",
                platform=platform_label,
                id_request=id_request,
                id_project=id_project,
            )
            warning_kind = "no_reference"
        else:
            # Nothing to warn: some task matched, or unrelated repository
            return
        self.with_delay(
            description=job_description,
            identity_key=f"project_git.{warning_kind}:{event.get('source')}:"
            f"{id_project}:{id_request}",
        )._post_message(message, event)

    def _post_message(self, message, event=None):
        """Post a message on the PR/MR on its source platform.

        Works either on a single record (its fields identify the PR/MR)
        or on an empty recordset with the event as identification
        fallback (e.g. warnings for PRs not tracked in Odoo). The
        per-source implementations live in the platform bridges.

        Queue job method: the posting is the only side effect of the
        job, so a failed job can be safely retried. The bridges raise
        RetryableJobError on transient API errors (network failures,
        rate limits, 5xx) to retry automatically; any other error
        leaves the job failed in the queue, to be inspected and
        requeued by hand.
        """
        if self:
            self.ensure_one()
            source = self.source
        else:
            source = (event or {}).get("source")
        if hasattr(self, f"_post_message_{source}"):
            return getattr(self, f"_post_message_{source}")(message, event)
        _logger.warning("No _post_message implementation for source %r", source)
        return False

    def assign_tags(self):
        """Align the state tags of the related tasks.

        The whole tagging process is per-source (each bridge owns its
        master-data, its tag namespace and the fields it populates):
        the bridges implement _assign_tags_to_task_<source>. Records
        without an implementation (e.g. created by hand without a
        source) are skipped.
        """
        for git_pull_request in self:
            if not git_pull_request.state:
                continue
            method_name = f"_assign_tags_to_task_{git_pull_request.source}"
            if not hasattr(git_pull_request, method_name):
                _logger.warning(
                    "No _assign_tags_to_task implementation for source %r",
                    git_pull_request.source,
                )
                continue
            for task in git_pull_request.task_ids:
                getattr(git_pull_request, method_name)(task)

    def _replace_task_tags(self, task, tags_to_remove, tags_to_add):
        """Replace a set of tags on a task in a single write (removals
        first, then additions)."""
        task.write(
            {
                "tag_ids": [(3, tag.id) for tag in tags_to_remove]
                + [(4, tag.id) for tag in tags_to_add],
            }
        )
