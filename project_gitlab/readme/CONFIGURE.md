Connect your GitLab repositories, step by step:

1. **Configure the base module first**: webhook endpoint and project
   mapping are documented in `project_git`.

2. **Set one `project_gitlab.webhook_secret.<instance-url>/` system
   parameter per GitLab instance** to the secret shared with the
   webhooks of that instance; in Odoo, with the developer mode
   activated, set the parameter from **Settings > Technical >
   Parameters > System Parameters**. The key is the instance root URL
   **with a trailing slash**, e.g.
   `project_gitlab.webhook_secret.https://gitlab.com/`. The sending
   instance (`X-Gitlab-Instance` header; on GitLab older than 15.5,
   the project URL in the payload) selects the secret the requests
   are verified against: requests are rejected when the parameter of
   their instance is missing or still set to the demo default.

   The format of the value selects the verification mechanism:

   - a **signing token** (GitLab 19.1 and later, recommended): a
     `whsec_<base64>` value encoding a 32-byte key, like the ones
     GitLab generates in the webhook form. The secret never travels:
     GitLab signs every delivery with it (HMAC-SHA256 per the
     Standard Webhooks specification) and deliveries with a stale
     signed timestamp are rejected (replay protection);
   - a **plain secret token** (any GitLab version): any other value.
     GitLab sends it verbatim in the `X-Gitlab-Token` header of every
     delivery, and it is compared with the parameter.

3. **Set one `project_gitlab.token.<instance-url>/` system parameter
   per GitLab instance** to a GitLab access token. Create the token on
   GitLab from your avatar menu, **Edit profile > Access tokens**; in
   Odoo, with the developer mode activated, set the parameter from
   **Settings > Technical > Parameters > System Parameters**. The key
   is the instance root URL **with a trailing slash**, e.g.
   `project_gitlab.token.https://gitlab.com/`. This token lets Odoo
   call the GitLab API: fetch the MR commits, post messages on the MR,
   deploy the webhook, retry CI jobs. On a self-hosted instance whose
   host name does not carry "gitlab", this parameter is also what
   makes the **Retry CI Job (GitLab)** button appear on the projects
   of its repositories.

   A missing token — or the demo default shipped by the module — makes
   the API calls fail with an explicit error: replace it with a real
   token.

   When the MR commits cannot be fetched via API (e.g. missing token),
   the head commit carried by the MR payload is used as fallback.

4. **Deploy the webhook** with the **Create Webhooks** button on the
   project form (**Project > Configuration > Projects**, open the
   project; to configure it by hand instead, use the project page on
   GitLab, **Settings > Webhooks**). The webhook is subscribed to
   push, merge request and pipeline events and uses the
   `project_gitlab.webhook_secret.<instance-url>/` parameter of the
   repository instance as secret: a `whsec_` value is installed on
   the hook as its signing token, any other value as its plain
   secret token.

5. **Optionally, map GitLab users to Odoo users**: go to **Settings >
   Users & Companies > Users**, open the user and fill the **Gitlab
   Username** field with their GitLab username. The author of each
   merge request is matched against this field and shown on the MR
   record as **Created by User** (left empty when no user matches).
