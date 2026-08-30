# Copyright 2026 Francesco Ballerini
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl.html).

from .common import ProjectGitCase


class TestQueueJobConfig(ProjectGitCase):
    """The jobs of the connector run in the root.project_git channel
    (the event handlers are checked on real jobs in the controller tests
    of the bridges)."""

    CHANNEL = "root.project_git"

    def test_channel_record(self):
        channel = self.env.ref("project_git.channel_project_git")
        self.assertEqual(channel.complete_name, self.CHANNEL)
        self.assertEqual(
            self.env["project.git.utils"]._get_project_git_queue_job_channel(),
            channel,
        )
