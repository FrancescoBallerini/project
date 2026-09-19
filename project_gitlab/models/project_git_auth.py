# Copyright 2020, Jarsa
# Copyright 2026 Francesco Ballerini
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

from urllib.parse import urljoin

import gitlab  # pylint: disable=W7935

from odoo import api, models


class ProjectGitAuth(models.AbstractModel):
    _inherit = "project.git.auth"

    @api.model
    def _get_gitlab_instance_root(self, url):
        """Instance root (with a trailing slash) of a GitLab
        project-level URL: the key suffix of the per-instance sysparams
        (project_gitlab.token.<instance root>,
        project_gitlab.webhook_secret.<instance root>)."""
        return urljoin(url, "../..")

    @api.model
    def _connect_gitlab(self, url):
        """Connect to the gitlab instance hosting the given project URL
        and return the gitlab client object.

        retry_transient_errors makes the client retry on the spot the
        server errors, network failures and timeouts, on top of the
        rate limits it retries by default (waiting for the Retry-After
        delay).

        :param str url: a project-level URL (e.g. project web_url); the
            instance root is derived from it, and selects the per-instance
            token sysparam (project_gitlab.token.<instance root>)
        """
        url = self._get_gitlab_instance_root(url)
        token = self._get_token_param("project_gitlab.token." + url)
        return gitlab.Gitlab(url, private_token=token, retry_transient_errors=True)
