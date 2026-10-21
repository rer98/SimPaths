<!-- (C) Copyright 2026, by Ross Richardson

SimPaths Online SMTP provider, administrator recipients and private deployment setup.

@author ross richardson
-->

# SimPaths Online email setup

## Agreed initial setup — 21 October 2026

- Use the existing Brevo Free account for the initial deployment. Ross confirmed
  that its current 300-email daily allowance is sufficient initially; usage from
  the other websites sharing that account must also fit within the allowance.
- A separate SMTP key named **SimPaths Online** has been created in that account.
- Use **r@ross-richardson.com** as the initial sending address and administrator
  recipient. Sender credentials and recipient routing are separate settings.
- The private local configuration is saved at
  `~/.config/simpaths-online/smtp.env`, outside the Git checkouts. Local checks
  confirmed the required settings and a nonempty key, file mode **0600** and
  parent-directory mode **0700**, without displaying the key.
- SMTP authentication and real inbox delivery remain VM acceptance tasks.

This guide records non-secret decisions. The SMTP key belongs only in private
configuration or a password manager, never in Git, this guide or test reports.
The actual Brevo SMTP login is retained in the private configuration too.

## SMTP transport and address roles

The shared Python sender supports Brevo's SMTP relay with STARTTLS on port 587.
The existing application creates the email messages; Brevo delivers them.
No Brevo campaign templates or marketing automations are required for this route.

| Setting | Meaning |
| --- | --- |
| `SMTP_HOST=smtp-relay.brevo.com` | Outgoing SMTP relay |
| `SMTP_PORT=587` | SMTP submission port |
| `SMTP_USE_TLS=true` | Upgrade the connection using STARTTLS |
| `SMTP_FROM_EMAIL=r@ross-richardson.com` | Sender address shown on messages |
| `SMTP_LOGIN_EMAIL` | Brevo SMTP username from its SMTP settings |
| `SMTP_PASSWORD` | The dedicated SimPaths Online SMTP key, not a Brevo API key |
| `CONTACT_TO_EMAIL` | Contact-form recipient only; it does not select administrator recipients |
| MultiRun `[notifications].admin_email` | Administrator alerts and monitoring-export recipient |
| Backup `[alerts].recipient` | Separate recipient for backup alerts |

The sender and administrator recipient may be the same mailbox. Sign-in codes
and owner-specific job notifications still go to the relevant user's address.

## Private VM environment

Use the existing [SMTP environment example](multirun/vm/smtp.env.example).
The populated laptop configuration already contains the selected settings and
new key; transfer it privately when installing the VM rather than recreating
or publishing the credential.

The MultiRun private executor and backup unit load
`/etc/simpaths-online/smtp.env`. Install it as a root-readable **0600** file.
The public MultiRun gateway cannot read this file. The SingleRun private executor
uses its separate `/etc/simpaths-online/singlerun.env`; install any required SMTP
settings there privately as part of its own configuration. The laptop `.env`
file is not automatically loaded by these VM services.

Keep the empty example separate from the populated private file. Replacing an
existing populated file with the example would discard its key.

## Administrator notifications and monitoring exports

After SMTP and inbox verification, merge the following values into the matching
sections of the private MultiRun TOML configuration. This is a fragment, not a
complete VM configuration:

```toml
[notifications]
enabled = true
admin_email = "r@ross-richardson.com"
storage_wait_emails = true
storage_wait_hours = 6

[monitoring_exports]
enabled = true
mail_enabled = true
archive_seconds = 3600
delivery_seconds = 86400
part_mib = 2
```

This selects hourly archive packing and daily delivery of numbered monitoring
attachments, with a 2 MiB compressed part allowance. Resource collection,
export preservation and email delivery have independent controls. Enabling
notification delivery does not itself enable automatic retention deletion.

TOML export settings seed the private controls on first startup. If controls
already exist, use the live commands in
[MONITORING_EXPORTS.md](multirun/MONITORING_EXPORTS.md#configuration-and-live-controls)
instead of expecting a TOML edit or restart to overwrite them. That guide also
documents retries, outbox budgets and deduplicated offline collation.

Backup alerts are separately enabled in `backup.toml`:

```toml
[alerts]
enabled = true
recipient = "r@ross-richardson.com"
```

Sharing the SMTP environment alone does not enable backup alerts. See the
[backup installation guide](multirun/BACKUP.md) for its complete configuration.

## Remaining VM acceptance

- Confirm the sender/domain is verified and authenticated in Brevo. If SMTP IP
  restrictions are enabled, authorize the VM's outbound address.
- Check STARTTLS and authentication from the installed VM using its private
  configuration and current trust store.
- Use controlled recipient accounts to verify sign-in codes, job notifications,
  administrator warnings, monitoring attachments and backup-alert routing.
- Inspect inbox delivery and sender identity, and confirm that failed delivery
  is visible and retried through the appropriate outbox or backup mechanism.
- Check combined Brevo account usage during the initial trial. Delayed delivery
  after an exhausted allowance would also affect time-sensitive sign-in codes.
- Review sender, administrator recipients and ownership of the mail account at
  handover. These are private configuration choices, not application-code changes.

The local SMTP rehearsal and captured-mail tests establish application behaviour;
they do not establish delivery through the real Brevo account or into an inbox.

## Provider references

Provider documentation checked on 21 October 2026:

- [Configure Brevo SMTP](https://help.brevo.com/hc/en-us/articles/7924908994450-Send-transactional-emails-using-Brevo-SMTP)
- [SMTP keys](https://help.brevo.com/hc/en-us/articles/7959631848850-Create-and-manage-your-SMTP-keys)
- [SMTP port choice](https://help.brevo.com/hc/en-us/articles/10905415650322-Which-SMTP-port-should-I-use-Port-587-465-or-2525)
- [Sender-domain authentication](https://help.brevo.com/hc/en-us/articles/12163873383186-Authenticate-your-domain-with-Brevo-Brevo-code-DKIM-DMARC)
- [IP authorization](https://help.brevo.com/hc/en-us/articles/5740111683858-Authorize-and-block-IP-addresses-for-API-and-SMTP-security)
- [Free-plan allowance](https://help.brevo.com/hc/en-us/articles/208580669-FAQs-What-are-the-limits-of-the-Free-plan)
