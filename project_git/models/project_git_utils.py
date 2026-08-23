# Copyright 2026 Francesco Ballerini
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

import re

import requests

from odoo import api, models

TASK_NAME_MATCH_REGEX = r"\b[A-Z][A-Z]+-\d+\b"
TASK_ID_REFERENCE_REGEX = r"\b(?:task|t)id#(?P<id>\d+)\b"

# Platform API failures worth retrying later by the job (see
# project.git.pull.request _post_message) once the platform library
# gave up its own retries: HTTP statuses of rate limits and server
# errors, and the network-level errors of the requests-based platform
# libraries (RetryError = retries exhausted by the library itself)
TRANSIENT_HTTP_CODES = (429, 500, 502, 503, 504)
TRANSIENT_REQUEST_ERRORS = (
    requests.ConnectionError,
    requests.Timeout,
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.RetryError,
)


class ProjectGitUtils(models.AbstractModel):
    _name = "project.git.utils"
    _description = "Project Git Webhook Utilities"

    @api.model
    def _get_task_name_match_regex(self):
        """Regex extracting issue keys from commit messages, branch names
        and PR/MR titles: Jira-strict keys such as "ABC-123". Extension
        hook: an override must keep the extracted keys free of LIKE
        wildcards (the task lookup prefilters candidates with ilike).
        """
        return TASK_NAME_MATCH_REGEX

    @api.model
    def _extract_task_id_references(self, text):
        """Extract the explicit task id references ("taskid#123" or
        "tid#123", case-insensitive) from a text. Every occurrence is
        considered.

        :param str text: any text carried by the event (PR/MR title,
            branch name, commit message)
        :return: list of referenced task ids
        :rtype: list(int)
        """
        if not text:
            return []
        return [
            int(task_id)
            for task_id in re.findall(TASK_ID_REFERENCE_REGEX, text, re.IGNORECASE)
        ]
