GitHub bridge for the `project_git` connector.

It recognizes and authorizes GitHub webhook requests (push and pull
request events, classified by the `X-GitHub-Event` header and signed
with the shared secret), maps GitHub users to Odoo users, deploys the
webhook on the repository from the Odoo project form and can retry
the GitHub Actions jobs of the recent workflow runs, picked from a
wizard on the project form.

Only `github.com` repositories are supported (no Enterprise base URL).
