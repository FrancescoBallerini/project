# Copyright 2026 Francesco Ballerini
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

from .common import ProjectGitCase


class TestDispatchBySource(ProjectGitCase):
    """Tests for the per-source dispatch contract of project.git.event."""

    def test_missing_mandatory_implementation_raises(self):
        # A missing mandatory per-source method is a bridge bug: the
        # dispatch fails loudly instead of degrading the flow
        with self.assertRaises(NotImplementedError):
            self.git_event._get_pr_title_from_event({"source": "unknown_platform"})

    def test_missing_optional_hook_is_skipped(self):
        # Optional hooks (mandatory=False) are silently skipped: the
        # wrapper falls back to its default value
        self.assertEqual(
            self.git_event._get_pr_fallback_commits({"source": "unknown_platform"}), []
        )
