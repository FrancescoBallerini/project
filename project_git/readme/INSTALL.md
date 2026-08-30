This module requires no git platform Python library, but it does
nothing by itself: install one bridge module per platform you want to
connect (`project_github` for GitHub, `project_gitlab` for GitLab).
Each bridge installs the Python library of its platform.

Webhook processing relies on the `queue_job` module, from the
[OCA/queue](https://github.com/OCA/queue) repository: the events are
processed asynchronously by its jobrunner, which must be active — add
`queue_job` to the `server_wide_modules` of your instance (see the
`queue_job` documentation). On hostings where the standard jobrunner
cannot run (e.g. Odoo.sh), also install `queue_job_cron_jobrunner`
(same repository) to process the jobs from a cron instead.

All the jobs of the connector (event processing, PR/MR messages) run
in the `root.project_git` channel. The platform libraries wait and
retry on their own when
the API is rate limited or in error, inside the running job: with the
default jobrunner configuration (`root:1`) such a wait holds every
job of the instance. To keep the connector jobs apart, give the
channels their own capacity in the jobrunner configuration, e.g.
`ODOO_QUEUE_JOB_CHANNELS=root:2,root.project_git:1` (or
`channels = root:2,root.project_git:1` in the `[queue_job]` section
of the Odoo configuration file): the other jobs keep a free slot
while a connector job waits. A capacity of 1 on `root.project_git` is
recommended: the events are then processed one at a time, in order.
