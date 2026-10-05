<!-- (C) Copyright 2026, by Ross Richardson

SimPaths Online deployment readiness, verified local evidence and installation order.

@author ross richardson
-->

# SimPaths Online deployment readiness

**Reviewed:** 5 October 2026. **Deployment status:** local implementation and
rehearsals are recorded below; no production VM or domain has been selected.

Use this checklist to prepare and validate the first SimPaths Online installation,
covering SingleRun, UK MultiRun and the Policy Impact Visualiser connection.
The evidence table records passing local tests. The unchecked items require the
actual deployment configuration, host or external service. Older dated plans
retain the development history; this checklist supplies the current deployment order.

The reports test different recorded source revisions and images. They do not
collectively certify an unspecified future build. Record the exact selected release
and run the relevant checks against it when installing or changing the service.

The subsequent MultiRun XFS quota implementation is described in
[WORKSPACE_QUOTAS.md](multirun/WORKSPACE_QUOTAS.md). Local ABI, launcher, journal,
executor, configuration and file-restore checks pass. Its native loop-filesystem
rehearsal passed in `workspace-quota-20261005-011818`; the earlier evidence below
does not certify this new boundary.
The first two native attempts passed 74 regressions and confirmed cleanup, then
stopped at the probe's incorrect `EDQUOT` expectation before launching models.
XFS returned `ENOSPC` at 16 MiB. The corrected probe verifies kernel accounting,
free capacity and a separate successful allocation; all 71 local checks pass.
The corrected native workflow passed all 80 regressions and five filesystem
stages; all three reports confirm success and cleanup. Installation-specific
enforcement, the approved real model and reboot acceptance remain required.

## Verified local evidence

All linked reports were read for this review. Links refer to the original files on
Ross's laptop under `/home/rer/simpaths-benchmarks`; archive protected copies before
retiring that storage. For wrappers, inspect both `report.json` and the adjacent
`model-proof/report.json`: the listed wrapper runs confirm success and cleanup.
The older real-model drivers use `status: "passed"` rather than that report format.

| What passed | Evidence | Scope |
| --- | --- | --- |
| MultiRun review/submission, draft persistence, YAML, compact sweeps, configuration caps, runtime budgets and owner isolation | [Workflow report](/home/rer/simpaths-benchmarks/multirun-browser-20261001-104331/report.json) | Backend and browser checks with fictional data |
| PostgreSQL SingleRun controls, concurrent owners, downloads and independent Reset/Leave | [Two-session report](/home/rer/simpaths-benchmarks/two-session-postgres-cutover-20261002-193428/report.json) | Two real 20,000-person models |
| Redis/PostgreSQL responsiveness comparison supporting the completed PostgreSQL migration | [Performance report](/home/rer/simpaths-benchmarks/singlerun-state-performance-20261002-093839/report.json) | Three paired full 50,000-person trials, 2019–2026; historical comparison |
| Shared SingleRun/MultiRun admission, polling under load, restart adoption and original-seed completion | [Mixed-load report](/home/rer/simpaths-benchmarks/mixed-load-20261003-000304/report.json) | One real 20,000-person and one real 50,000-person model |
| Retained model versions preserve datasets, accepted reviews, queued jobs, resources and retries | [Release-transition report](/home/rer/simpaths-benchmarks/postgres-queue-20261003-112048/report.json) | Fictional releases; scientific compatibility is assessed separately |
| Repetition-scaled working storage and removal of repeated run inputs while preserving options and verified output | [Storage report](/home/rer/simpaths-benchmarks/multirun-storage-20261003-124138/report.json) | Full real 50,000-person runs, including three repetitions |
| Hard MultiRun workspace quotas, inherited ioctl denial, broker/worker restart, independent restore and saved-result preservation | [Quota report](/home/rer/simpaths-benchmarks/workspace-quota-20261005-011818/report.json) and [filesystem proof](/home/rer/simpaths-benchmarks/workspace-quota-20261005-011818/postgres-proof/model-proof/report.json) | Two disposable XFS filesystems, real kernel limits and fictional Docker models; not installed-host acceptance |
| Read-only operator inventory of jobs, reservations, storage, cleanup, notices and releases | [Operator report](/home/rer/simpaths-benchmarks/postgres-queue-20261003-142214/report.json) | Disposable PostgreSQL and fictional files |
| Maintained Visualiser page, configuration names, separate alternatives, comparison ZIPs, aggregate-only VM responses and owner/source guards | [Visualiser report](/home/rer/simpaths-benchmarks/vm-visualiser-20261003-220301/report.json) | Real browser and aggregation code with fictional CSVs |
| MultiRun TLS/cookies, private-file denial, large ZIP preparation, slow transfer, restart/resume, deletion and revocation | [MultiRun HTTPS report](/home/rer/simpaths-benchmarks/https-rehearsal-20261004-003741/report.json) | Real local Nginx/TLS and fictional output |
| SingleRun TLS/ownership, controls/charts, long ZIP transfer, disconnect cleanup and restart access | [SingleRun HTTPS report](/home/rer/simpaths-benchmarks/singlerun-https-20261004-071322/report.json) | Real local Nginx/TLS and fictional models |
| STARTTLS sign-in/completion mail, durable delivery retries, expiry warnings, actual file deletion and reader protection | [Mail and retention report](/home/rer/simpaths-benchmarks/mail-retention-20261004-080335/report.json) | Local SMTP server and disposable files; not external inbox delivery |
| Native online backup/restore, shared SingleRun saved exports, ownership and recovery gates | [Online backup report](/home/rer/simpaths-benchmarks/postgres-queue-20261003-191224/report.json) | Disposable native PostgreSQL and fictional files |
| Scheduled live capture, encryption, pinned SFTP, interruption/retry, retrieval and native isolated restore | [Scheduled backup report](/home/rer/simpaths-benchmarks/backup-rehearsal-20261004-183630/report.json) | Real local systemd/OpenSSH/GnuPG; fictional output |
| Automatic application restart, original-container adoption, retained retry budgets and bounded restart attempts | [Service recovery report](/home/rer/simpaths-benchmarks/service-recovery-20261004-101159/report.json) | Real transient user systemd units and fictional Docker models |
| Restricted database-role permissions, orderly PostgreSQL stop and crash, safe denials and reconciliation | [Database recovery report](/home/rer/simpaths-benchmarks/postgres-recovery-20261004-213531/report.json) | Real disposable PostgreSQL and fictional models |
| Compatible application update/rollback, retained ownership/work/results, rejection of newer schemas and verified recovery | [Application update report](/home/rer/simpaths-benchmarks/application-update-20261004-231729/report.json) | Real committed code, native services/restore and fictional models |

The current hosted Visualiser integration is a pinned **development levels
preview** for owner-supplied inputs and explicitly verified public training data.
Confidential provider derivatives are ineligible. Multiple alternatives are supplied
separately, with the current charts displaying one alternative alongside the baseline.
Paired policy impacts/uncertainty, simultaneous charts and local-folder comparison
sets depend on maintained Visualiser support. See [the connection scope](multirun/VISUALISER.md).

The Visualiser receives only aggregate results from the VM. Separate authenticated
downloads of eligible owner-supplied output are intentional; provider-derived raw
downloads remain denied. Users may select their own locally saved raw files in the
Visualiser. Those permissions must remain distinct during deployment acceptance.

## Remaining deployment checks

Leave these items unchecked until their evidence comes from the selected installation.
The runbooks describe the existing commands and configuration; this checklist does
not install services, choose providers or change security settings.

### Release and service scope

- [ ] Record exact SimPaths, JAS-mine-core and JAS-mine-web commits, JAR hashes,
  immutable image IDs, retained release IDs, input receipts, catalogue, dependencies
  and Visualiser build/source manifest. Retain the previous usable builds/images.
- [ ] Verify licences, notices and permitted redistribution for the selected
  model, dependencies, Visualiser and training inputs before publishing artifacts.
- [ ] Set the initial data/profile scope and account approval rules. Retain the
  current provider restrictions; agree applicable Visualiser release/output rules
  with its maintainers before expanding the preview. Scientific calculations and
  disclosure decisions remain with the model/Visualiser maintainers.
- [ ] Confirm normal browser use and guidance for the selected release, including
  narrow layout, preparation, sweeps, retries, downloads and Online/local Visualiser use.

### Host storage and capacity

- [ ] Choose the host, operating system, private volume and domain when that
  decision is resumed. Install reviewed artifacts with a dedicated service account
  and operator-controlled code/configuration; keep host/Docker access private.
- [ ] Mount and verify persistent private storage outside all static roots. Budget
  the OS, Docker/PostgreSQL data, retained results, caches, logs and separate backup
  storage independently, including temporary copies and free-space reserves.
- [ ] Install and test physical workspace limits on the selected filesystem and
  executor: SingleRun's Docker writable-layer verifier and MultiRun's XFS project
  broker/static launcher. Check excess writes, restart, restore and unaffected
  neighbouring work. Queue reservations and polling do not establish those limits.
- [ ] Verify storage-pressure behaviour, confirmed cleanup and administrator alerts
  on the actual volumes without sacrificing retained inputs, results or history.
- [ ] Measure the intended 50,000-person horizon, repetitions, preparation,
  aggregation and retained-data growth. Increase concurrency only within measured
  CPU/RAM/disk headroom; include interactive slots and backup activity. Laptop
  measurements do not establish capacity for five or ten researchers.
- [ ] Set pool capacity/holdback, runtime/storage policies, upload/queue/cache caps
  and retention from those measurements. Use isolated staging pools for calibration;
  set production capacity before admitting real users. Existing pool capacity cannot
  be silently rewritten, and accepted jobs must keep their frozen limits.

### Database security and supervised services

- [ ] Verify private PostgreSQL connectivity and the restricted application role's
  actual grants. Migrate the MultiRun pool before joining SingleRun to the same
  database/pool with its separate registry schema, or partition budgets explicitly.
  A fresh installation uses PostgreSQL directly; no Redis-session cutover is needed.
- [ ] Install and protect the intended persistent signing/reset keys, private DSN
  and SMTP configuration. Verify restart and recovery with the existing cookies;
  keep credentials out of Git, evidence bodies and browser responses. Follow the
  hosted key instructions; fresh local-test key generation is a different workflow.
- [ ] Validate installed systemd units, paths, permissions and hardening. Test boot
  and reboot as well as process failure, database outage and uncertain Docker
  inspection. Verify readiness, original-attempt recovery and capacity reconciliation.
  Transient laptop units do not test the installed host's boot or privilege policy.
- [ ] Configure operator monitoring for external readiness, disk/RAM headroom,
  blocked jobs, cleanup, restart failures, mail outboxes and backup freshness.

### HTTPS and data access

- [ ] Verify public certificates, hostname/DNS and renewal, actual Nginx configuration,
  forwarded headers and protected-cookie settings for each exposed application.
- [ ] Test host/firewall rules for IPv4 and IPv6. Keep application, PostgreSQL,
  Docker and model ports inaccessible externally; restrict operator access.
- [ ] Run fictional-data probes through the actual external URL as anonymous,
  owner and second owner. Deny direct private-file/traversal requests and restricted
  provider/mixed raw downloads, including HEAD/range/resume and saved links.
- [ ] Inspect the deployed Visualiser's browser network/dev tools: aggregate-only
  VM payloads, approved assets and no raw CSV/database/default-data/private-path
  responses. Check deletion/revocation and local-folder viewing without uploads.
- [ ] Interrupt and resume a large download through the installed proxy, then
  verify the full ZIP/checksums and owner denials after restart and source removal.

### Real email retention and backups

- [ ] Test sign-in and job/administrator notices with the real SMTP account and
  recipient inboxes, including sender identity, TLS/authentication and failures.
  Local SMTP acceptance alone does not prove inbox arrival.
- [ ] Test warning deadlines, retries and deletion with disposable staging files.
  Enable automatic deletion for real users only after notice/recovery acceptance.
  Warnings start when recorded; an SMTP outage does not extend deletion deadlines.
- [ ] Configure separate protected backup storage and the real pinned SFTP host.
  Verify encryption, independent key recovery, readback, remote retention and alerts.
  Protect the private decryption key separately from the service VM.
- [ ] Restore a downloaded recovery point on an independent inactive target with
  required images and signing/configuration artifacts. Verify original ownership,
  files, seeds, budgets and provider denials; isolate the source before activation.
  Test capture responsiveness under representative load before enabling the timer.

## Installation and validation order

1. **Prepare the release record.** Keep the local evidence and record the selected
   artifacts, data scope and outstanding checks. Provider/domain selection remains
   deferred until requested; the preparation itself needs neither.
2. **Provision private staging.** Establish the host/service account, finite private
   storage, workspace enforcement, separate backup storage and host access rules.
   Fix paths and bounded staging budgets before accepting test work that binds to them.
3. **Install immutable artifacts and PostgreSQL.** Verify images/receipts, configure
   the restricted database role, retain hosted signing keys and run configuration
   checks/preflight. Start the MultiRun pool before shared SingleRun admission.
4. **Start restricted services and HTTPS.** Install and validate the actual systemd
   units/proxy/certificates. Configure real sign-in SMTP; initially use only staging
   users and disposable files, with automatic real-user deletion disabled.
5. **Validate data access and ordinary workflows.** Test the external privacy boundary,
   eligible/denied downloads, Visualiser delivery, controls and browser workflow.
   Then measure real workload/storage and choose the supported concurrency limits.
6. **Validate operations.** Exercise installed service/database/Docker failures and
   reboot, real mail/retention, scheduled remote backup, independent restore and
   application update/rollback. Verify preserved ownership and settled capacity.
7. **Review and open access.** Record the outcomes below. Enable only the tested data
   scope, notices, deletion/timer settings and workload limits; approve the intended
   research accounts. Record any intentionally disabled optional component.

An online recovery point preserves capture-time database state and settled files;
it does not checkpoint a running JVM or retain live incomplete output. Recovery
cannot undo user work or emails after capture. Use the existing inactive restore
and source-isolation procedure before allowing the recovered target to dispatch.

## Deployment acceptance record

Complete this section for the actual release/host. Store detailed reports and
private configuration separately from public assets.

| Record | Value |
| --- | --- |
| Acceptance date and operator | Pending |
| Installed source commits and artifacts | Pending |
| Host specification and private storage/workspace enforcement | Pending |
| HTTPS origins and installed service/configuration identities | Pending; record fingerprints/references, not credentials |
| Supported profiles, data scope and Visualiser mode | Pending |
| Measured capacity and frozen resource-policy settings | Pending |
| External privacy and browser/download evidence | Pending |
| Boot/outage/update recovery evidence | Pending |
| Real mail/retention evidence | Pending |
| Remote backup retrieval and independent restore evidence | Pending |
| Remaining issues or deliberately disabled features | Pending |
| Access opened by and date | Pending |

## Runbooks and deferred development

- [Native MultiRun installation, shared admission, privacy and measurements](multirun/VM.md).
- [Browser workflow and user guidance](multirun/LOCAL_WEB.md).
- [Visualiser build, eligibility and aggregate interface](multirun/VISUALISER.md).
- [Model release registration and resource policy](multirun/RELEASES.md).
- [Operator status and interpretation](multirun/OPERATOR.md).
- [Backup, encryption, scheduling and isolated restoration](multirun/BACKUP.md).
- [Real-model browser and mixed-load acceptance](acceptance/README.md).
- [SingleRun image build and release workflow](MAINTAINER_GUIDE.md).

SingleRun configuration, hosted signing keys, state and access controls are
maintained in JAS-mine-web's `deploy/simpaths/README.md`, `deploy/SESSION_SECURITY.md`,
`docs/vm-postgresql.md` and `docs/vm-deployment.md`; retain those documents with the
selected frontend release.

Later development remains separate: Reese's simultaneous-scenario charts and local
set-folder support, the reviewed updated policy-impact calculations, optional
prefilled experiment examples and the documentation-aware assistant. Mixed
Online/local comparisons remain a notes-only idea with statistical comparability
concerns. These are not completed capabilities or extra tasks to repeat from the
older plans. Expanding data/chart scope requires its own integration validation.
