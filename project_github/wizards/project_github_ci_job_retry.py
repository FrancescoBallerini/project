# Copyright 2026 Francesco Ballerini
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

from datetime import timezone
from itertools import islice

from github import GithubException, UnknownObjectException

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError

# Job conclusions the wizard lists: the finished ones, which GitHub
# can rerun once the workflow run is completed. Skipped jobs are
# excluded on purpose: there is no run of them to retry.
FINISHED_JOB_CONCLUSIONS = ["success", "failure", "cancelled"]


class ProjectGithubCiJobRetry(models.TransientModel):
    _name = "project.github.ci.job.retry"
    _description = "Retry a CI job of the project GitHub repository"

    project_id = fields.Many2one(
        comodel_name="project.project", required=True, readonly=True
    )
    git_project_url_type = fields.Selection(
        selection="_selection_git_project_url_type",
        string="Git Project URL",
        required=True,
        default="production",
        help="Which repository URL of the project to fetch the jobs from",
    )
    run_count = fields.Integer(
        string="Workflow Runs",
        default=5,
        required=True,
        help="How many workflow runs to fetch the jobs from, most recent first",
    )
    line_ids = fields.One2many(
        comodel_name="project.github.ci.job.retry.line",
        inverse_name="wizard_id",
        string="CI Jobs",
    )

    @api.model
    def _selection_git_project_url_type(self):
        """Radio options labeled with the repository URLs of the
        project, read from the wizard action context (plain fallback
        labels without it, e.g. at module load)."""
        return [
            (
                "production",
                self.env.context.get("git_project_url_view_ref") or _("Production"),
            ),
            (
                "development",
                self.env.context.get("git_dev_project_url_view_ref")
                or _("Development"),
            ),
        ]

    @api.onchange("git_project_url_type")
    def _onchange_git_project_url_type(self):
        # The rows belong to the previously selected repository: clear
        # them so a stale row cannot be retried on the wrong repository
        self.line_ids = [Command.clear()]

    def action_load_jobs(self):
        """(Re)load the finished CI jobs of the last workflow runs and
        reopen the wizard on them."""
        self.ensure_one()
        if self.run_count < 1:
            raise UserError(_("Fetch at least one workflow run."))
        self.line_ids.unlink()
        try:
            lines_vals = self._fetch_ci_jobs_github()
        except GithubException as err:
            raise self.env["project.git.auth"]._github_api_user_error(
                err, self._get_repository_url()
            ) from err
        self.env["project.github.ci.job.retry.line"].create(lines_vals)
        return {
            "type": "ir.actions.act_window",
            "name": _("Retry CI Job"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
            "context": {
                # The job rows need width: extra-large dialog
                "dialog_size": "extra-large",
                # URLs shown as the labels of the repository radio; the
                # _view_ref suffix lets them through the context filter
                # of the web client view loader
                "git_project_url_view_ref": self.project_id.git_project_url,
                "git_dev_project_url_view_ref": (self.project_id.git_dev_project_url),
            },
        }

    def _get_repository_url(self):
        """URL of the repository selected in the wizard, after checking
        it is hosted on GitHub (same rule that shows the project form
        button)."""
        self.ensure_one()
        project_url = (
            self.project_id.git_project_url
            if self.git_project_url_type == "production"
            else self.project_id.git_dev_project_url
        )
        if not self.project_id._is_github_repository_url(project_url):
            raise UserError(_("The selected repository URL is not hosted on GitHub."))
        return project_url

    def _get_github_repo(self):
        """GitHub repository object of the wizard selected repository."""
        project_url = self._get_repository_url()
        github_client = self.env["project.git.auth"]._connect_github()
        return github_client.get_repo(self.project_id._git_project_path(project_url))

    def _fetch_ci_jobs_github(self):
        """Line values of the finished jobs of the last workflow runs,
        most recent run first."""
        lines_vals = []
        github_repo = self._get_github_repo()
        # islice, not a slice: slicing the PaginatedList raises an
        # IndexError when the repository has fewer runs than run_count
        for run in islice(github_repo.get_workflow_runs(), self.run_count):
            for job in run.jobs():
                if job.conclusion not in FINISHED_JOB_CONCLUSIONS:
                    continue
                lines_vals.append(
                    {
                        "wizard_id": self.id,
                        "id_job": str(job.id),
                        "name": job.name,
                        "workflow": job.workflow_name,
                        "status": job.conclusion,
                        "run_number": run.run_number,
                        "run_ref": run.head_branch,
                        "run_status": run.conclusion or run.status,
                        "date": self._parse_job_date(job),
                        "web_url": job.html_url,
                        "run_url": run.html_url,
                    }
                )
        return lines_vals

    def _retry_ci_job_github(self, id_job):
        """Retry the given job on GitHub. The rerun endpoint replies
        with an empty payload and GitHub also restarts the jobs
        depending on the retried one: the outcome is the updated
        workflow run, not a single new job. PyGithub does not wrap the
        job-level rerun, so the request goes through its requester."""
        github_repo = self._get_github_repo()
        try:
            github_repo.requester.requestJsonAndCheck(
                "POST", f"{github_repo.url}/actions/jobs/{id_job}/rerun"
            )
        except UnknownObjectException as err:
            # The repository was just found: a 404 here means the job is
            # gone, e.g. its workflow run was deleted after the load
            raise UserError(
                _("This job no longer exists on GitHub: reload the jobs.")
            ) from err

    @api.model
    def _parse_job_date(self, job):
        """Job end date as a naive UTC datetime (PyGithub returns
        timezone-aware datetimes; a cancelled job may have never
        completed: fall back to the start date)."""
        job_date = job.completed_at or job.started_at
        if not job_date:
            return False
        if job_date.tzinfo:
            job_date = job_date.astimezone(timezone.utc).replace(tzinfo=None)
        return job_date


class ProjectGithubCiJobRetryLine(models.TransientModel):
    _name = "project.github.ci.job.retry.line"
    _description = "CI job listed by the GitHub CI job retry wizard"

    wizard_id = fields.Many2one(
        comodel_name="project.github.ci.job.retry", required=True, ondelete="cascade"
    )
    # Char on purpose: the job ids of github.com exceed the 32-bit
    # range of an Integer field, and the job id is only interpolated
    # in the API URL anyway
    id_job = fields.Char(string="Job ID", required=True, readonly=True)
    name = fields.Char(string="Job", readonly=True)
    workflow = fields.Char(readonly=True)
    status = fields.Char(readonly=True)
    run_number = fields.Integer(string="Run", readonly=True)
    run_ref = fields.Char(string="Ref", readonly=True)
    run_status = fields.Char(readonly=True)
    date = fields.Datetime(string="Finished On", readonly=True)
    web_url = fields.Char(string="Job URL", readonly=True)
    run_url = fields.Char(string="Run URL", readonly=True)

    def action_open_job(self):
        """Open the job page on GitHub."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": self.web_url,
            "target": "new",
        }

    def action_retry(self):
        """Retry this job on GitHub and notify the outcome with a link
        to the workflow run."""
        self.ensure_one()
        try:
            self.wizard_id._retry_ci_job_github(self.id_job)
        except GithubException as err:
            raise self.env["project.git.auth"]._github_api_user_error(
                err, self.wizard_id._get_repository_url()
            ) from err
        # The rerun reply carries no new job: the notification links
        # the workflow run page, which shows the new attempt
        if self.run_url:
            # The %s placeholder is rendered by the notification as the
            # link of the matching entry of the links param
            message = _("Job %s has been restarted, with the jobs depending on it.")
            links = [{"label": self.name, "url": self.run_url}]
        else:
            message = _(
                "Job %s has been restarted, with the jobs depending on it.", self.name
            )
            links = []
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("CI job retried"),
                "message": message,
                "links": links,
                "type": "success",
                # Sticky: the notification holds the only link to the
                # restarted run, do not let it fade away
                "sticky": True,
            },
        }
