# Copyright 2026 Francesco Ballerini
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

from psycopg2 import IntegrityError

from odoo.tools import mute_logger

from .common import GITLAB_REPO_URL, ProjectGitCase


class TestProjectGitBranch(ProjectGitCase):
    def test_branch_url_unique(self):
        # The event flow deduplicates with search-then-create: the SQL
        # constraint guards the identifying key against concurrent jobs
        # processing the same branch
        branch_url = "https://gitlab.example.com/acme/demo-repo/-/tree/GL-100-feature"
        self.env["project.git.branch"].create(
            {"name": "GL-100-feature", "url": branch_url}
        )
        with (
            self.assertRaises(IntegrityError),
            mute_logger("odoo.sql_db"),
            self.env.cr.savepoint(),
        ):
            self.env["project.git.branch"].create(
                {"name": "GL-100-feature", "url": branch_url}
            )


class TestBranchRemoteDeletedHook(ProjectGitCase):
    """Base behavior of the _on_remote_deleted hook, driven by the
    project_git.on_branch_deletion system parameter."""

    def _set_on_deletion_param(self, value):
        # Always set explicitly: the devel DB may carry the sysparam
        # (False removes it, exercising the true default)
        self.env["ir.config_parameter"].sudo().set_param(
            "project_git.on_branch_deletion", value
        )

    def _tracked_branch(self):
        """Branch correlated to a task, a commit and a pull request."""
        git_commit = self.env["project.git.commit"].create(
            {"name": "GL-100 part one", "full_sha": "a" * 40}
        )
        git_branch = self.env["project.git.branch"].create(
            {
                "name": "GL-100-feature",
                "url": GITLAB_REPO_URL + "/-/tree/GL-100-feature",
                "task_ids": [(4, self.gl_task_100.id)],
                "git_commit_ids": [(4, git_commit.id)],
            }
        )
        git_pull_request = self.env["project.git.pull.request"].create(
            {
                "name": "GL-100 feature",
                "id_repository": 1,
                "id_request": 1,
                "source_branch": "GL-100-feature",
                "source_branch_id": git_branch.id,
            }
        )
        return git_branch, git_commit, git_pull_request

    def test_remote_deleted_preserves_by_default(self):
        self._set_on_deletion_param(False)
        git_branch, _git_commit, _git_pull_request = self._tracked_branch()

        git_branch._on_remote_deleted({})

        self.assertTrue(git_branch.exists())

    def test_remote_deleted_unknown_value_preserves(self):
        self._set_on_deletion_param("unknown_param")
        git_branch, _git_commit, _git_pull_request = self._tracked_branch()

        git_branch._on_remote_deleted({})

        self.assertTrue(git_branch.exists())

    def test_remote_deleted_delete_unlinks_branch_record_only(self):
        self._set_on_deletion_param("delete")
        git_branch, git_commit, git_pull_request = self._tracked_branch()

        git_branch._on_remote_deleted({})

        self.assertFalse(git_branch.exists())
        # The deletion never cascades: the related entities survive and
        # only lose the link rows; the pull request keeps the branch
        # name as plain text (the m2o is emptied by the ondelete rule)
        self.assertTrue(self.gl_task_100.exists())
        self.assertFalse(self.gl_task_100.git_branch_ids)
        self.assertTrue(git_commit.exists())
        self.assertFalse(git_commit.git_branch_ids)
        self.assertTrue(git_pull_request.exists())
        self.assertFalse(git_pull_request.source_branch_id)
        self.assertEqual(git_pull_request.source_branch, "GL-100-feature")
