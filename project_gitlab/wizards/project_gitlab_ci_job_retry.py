# Copyright 2026 Francesco Ballerini
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

from datetime import timezone

from dateutil.parser import isoparse
from gitlab.exceptions import GitlabError, GitlabJobRetryError

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError

# Job statuses the wizard lists: the finished ones, which GitLab can
# retry. Manual jobs are excluded on purpose: triggering them is a
# play, not a retry.
FINISHED_JOB_SCOPES = ["success", "failed", "canceled"]


class ProjectGitlabCiJobRetry(models.TransientModel):
    _name = "project.gitlab.ci.job.retry"
    _description = "Retry a CI job of the project GitLab repository"

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
    pipeline_count = fields.Integer(
        string="Pipelines",
        default=5,
        required=True,
        help="How many pipelines to fetch the jobs from, most recent first",
    )
    line_ids = fields.One2many(
        comodel_name="project.gitlab.ci.job.retry.line",
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
        """(Re)load the finished CI jobs of the last pipelines and
        reopen the wizard on them."""
        self.ensure_one()
        if self.pipeline_count < 1:
            raise UserError(_("Fetch at least one pipeline."))
        self.line_ids.unlink()
        try:
            lines_vals = self._fetch_ci_jobs_gitlab()
        except GitlabError as err:
            raise self.env["project.git.auth"]._gitlab_api_user_error(
                err, self._get_repository_url()
            ) from err
        self.env["project.gitlab.ci.job.retry.line"].create(lines_vals)
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
        it is hosted on GitLab (same rule that shows the project form
        button)."""
        self.ensure_one()
        project_url = (
            self.project_id.git_project_url
            if self.git_project_url_type == "production"
            else self.project_id.git_dev_project_url
        )
        if not self.project_id._is_gitlab_repository_url(project_url):
            raise UserError(_("The selected repository URL is not hosted on GitLab."))
        return project_url

    def _get_gitlab_project(self):
        """GitLab project object of the wizard selected repository."""
        project_url = self._get_repository_url()
        gl = self.env["project.git.auth"]._connect_gitlab(url=project_url)
        return gl.projects.get(self.project_id._git_project_path(project_url))

    def _fetch_ci_jobs_gitlab(self):
        """Line values of the finished jobs of the last pipelines,
        most recent pipeline first."""
        lines_vals = []
        gitlab_project = self._get_gitlab_project()
        pipelines = gitlab_project.pipelines.list(
            get_all=False, per_page=self.pipeline_count
        )
        for pipeline in pipelines:
            for job in pipeline.jobs.list(scope=FINISHED_JOB_SCOPES, get_all=True):
                lines_vals.append(
                    {
                        "wizard_id": self.id,
                        "id_job": str(job.id),
                        "name": job.name,
                        "stage": job.stage,
                        "status": job.status,
                        "id_pipeline": str(pipeline.id),
                        "pipeline_ref": pipeline.ref,
                        "pipeline_status": pipeline.status,
                        "date": self._parse_job_date(job),
                        "web_url": job.web_url,
                    }
                )
        return lines_vals

    def _retry_ci_job_gitlab(self, id_job):
        """Retry the given job on GitLab and return the attributes of
        the new job created by the retry."""
        return self._get_gitlab_project().jobs.get(id_job, lazy=True).retry()

    @api.model
    def _parse_job_date(self, job):
        """Job end date as a naive UTC datetime (canceled jobs may
        have never run: fall back to the creation date)."""
        date_str = job.finished_at or job.created_at
        if not date_str:
            return False
        job_date = isoparse(date_str)
        if job_date.tzinfo:
            job_date = job_date.astimezone(timezone.utc).replace(tzinfo=None)
        return job_date


class ProjectGitlabCiJobRetryLine(models.TransientModel):
    _name = "project.gitlab.ci.job.retry.line"
    _description = "CI job listed by the GitLab CI job retry wizard"

    wizard_id = fields.Many2one(
        comodel_name="project.gitlab.ci.job.retry", required=True, ondelete="cascade"
    )
    # Char on purpose: the job and pipeline ids of gitlab.com exceed
    # the 32-bit range of an Integer field, and the job id is only
    # interpolated in the API URL anyway
    id_job = fields.Char(string="Job ID", required=True, readonly=True)
    name = fields.Char(string="Job", readonly=True)
    stage = fields.Char(readonly=True)
    status = fields.Char(readonly=True)
    id_pipeline = fields.Char(string="Pipeline", readonly=True)
    pipeline_ref = fields.Char(string="Ref", readonly=True)
    pipeline_status = fields.Char(readonly=True)
    date = fields.Datetime(string="Finished On", readonly=True)
    web_url = fields.Char(string="Job URL", readonly=True)

    def action_open_job(self):
        """Open the job page on GitLab."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": self.web_url,
            "target": "new",
        }

    def action_retry(self):
        """Retry this job on GitLab and notify the outcome with a link
        to the new job run."""
        self.ensure_one()
        try:
            new_job = self.wizard_id._retry_ci_job_gitlab(self.id_job)
        except GitlabJobRetryError as err:
            raise UserError(_("Job %s cannot be retried.", self.name)) from err
        except GitlabError as err:
            raise self.env["project.git.auth"]._gitlab_api_user_error(
                err, self.wizard_id._get_repository_url()
            ) from err
        new_job_url = new_job.get("web_url")
        if new_job_url:
            # The %s placeholder is rendered by the notification as the
            # link of the matching entry of the links param
            message = _("A new run of job %s has been created.")
            links = [{"label": self.name, "url": new_job_url}]
        else:
            message = _("A new run of job %s has been created.", self.name)
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
                # new job run, do not let it fade away
                "sticky": True,
            },
        }
