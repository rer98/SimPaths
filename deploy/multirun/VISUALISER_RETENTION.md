<!-- (C) Copyright 2026, by Ross Richardson

Independent aggregate retention, current access checks and verified recovery.
@author ross richardson
-->

# Retained Visualiser results and recovery

## Expiry does not revoke access

Migration **029** distinguishes routine raw-output expiry from an explicit
deletion request and records verified source provenance for prepared aggregates.
The service can open an existing publication, or combine retained per-seed
summaries into a new comparison, after routine expiry removes the raw files.
It still checks current owner approval, dataset/ancestor grants, the frozen
job/attempt/seed identities and the reviewed calculation/build identity.

| Event | Existing finished charts | New comparison from retained summaries |
| --- | --- | --- |
| Raw files expire under their retention policy | Available until chart expiry | Available while the required summaries remain valid |
| Dataset access or owner approval is revoked | Denied immediately on the next authorised request | Denied |
| User/operator explicitly requests deletion of the source output | Denied, including when raw output had already expired | Denied |
| Chart retention expires | Unavailable; an authorised request may prepare it again | Needs valid summaries or still-available raw output |
| Summaries expire or are missing/corrupt after raw expiry | Existing valid charts still open | Cannot regenerate from missing raw files; no simulation is restarted |

The expiry reason is written only by the trusted retention path. Existing deletion
records default to explicit deletion; migration does not guess that old missing
files expired normally. Provenance is captured from a verified catalogue when
preparation actually reads a completed configuration. Setting a longer retention
period cannot recreate a source catalogue or summary that was never retained.
Explicit source retirement prunes that job/attempt's summaries, preserving
same-named configurations in other experiments.

The browser must still reauthorise before replaying its bounded local cache.
Revocation/explicit deletion therefore blocks hosted access even when rows were
previously cached. A portable ZIP already downloaded by a user remains a local
copy; a server cannot recall it.

## Independent clocks and bounded storage

The candidate settings retain the existing seven-day defaults:

```toml
[visualiser]
retain_completed_summaries = true
summary_hours = 168
cache_hours = 168
summary_cache_mib = 8192
cache_mib = 16384
```

`summary_hours` and `cache_hours` independently accept **1–8760 hours**. The raw
retention policy is separate. Summary protection begins at simulation completion
and is capped by current owner approval. Chart expiry begins when publication
becomes ready. Reads, additions to an experiment and backup/restore do not renew
either clock. A pending pair's summary deadline starts with its first completed
member; a later partner does not renew the earlier promise.
After summary protection expires, unheld entries return to ordinary bounded LRU
eviction. They cannot support new comparisons after raw expiry once their own
summary period has passed. `summary_hours` is a protection/reuse period, rather
than a guarantee of immediate physical removal of every unheld cache file.

The summary ceiling sits inside the total Visualiser disk allowance, together
with finished publications, chart indices and worker scratch. These settings do
not reserve extra RAM or filesystem capacity. Storage pressure can defer new
preparation; the example is not a promise that every admitted simulation already
has its future chart bytes reserved. The agreed 72-hour pressure eviction, live
budget growth and 50/70/85% admin alerts remain TODO in
[EXPERIMENT_ITERATION.md](EXPERIMENT_ITERATION.md).

## Backup contents

New offline backups use `simpaths.multirun.backup.v3`; online backups use `v4`.
Both retain committed Visualiser data alongside the matching PostgreSQL snapshot:

- Verified per-seed `metrics.json` and their immutable identity/checksum receipts.
- Summary dependency holds and bounded numeric cache-usage metadata.
- Ready publications, status/expiry records, chart indices and every indexed
  section. Composite selections are included only with all required components.
- Source provenance, current permissions and explicit expiry/deletion reasons
  in the captured database.

Selection uses trusted private locks and the same database snapshot as the rest
of the backup. Work newer than that snapshot is omitted. Ready publications take
shared reader locks; a busy writer's unfinished publication is omitted. Summary
copying briefly holds the cache publication lock. Active calculations, private
scratch/raw copies, pending files, transient locks and regenerable download ZIPs
are excluded. Large retained data adds backup I/O and disk demand; measure the
cost on the selected host.

Verification checks both the whole backup inventory and the aggregate receipts,
including every indexed chart section. Restore uses the existing inactive gate
and verified activation. Aggregate identity follows the original jobs and source
hashes across a verified relocation, rather than requiring the old root inode.
Required Visualiser builds/runtime images must still be kept separately.

The updated tool continues to read old offline `v1` and online `v2` backups;
those backups contain no retained Visualiser payload. Application/schema
compatibility checks still apply, so this is not permission to open a newer
database with older application code. Follow [BACKUP.md](BACKUP.md) for encryption,
off-machine publication and recovery. Restoring an earlier snapshot restores its
then-current permissions too: reconcile any later revocations before putting an
old recovery point back into service. Give retained aggregates their own backup
expiry plan, since deleting live files does not erase existing encrypted backups.

## Validation

The focused PostgreSQL run passed **127 checks** without skips, including nine
new independent-retention cases and existing Visualiser, Results, retention,
experiment-extension and migration checks. Cleanup was confirmed in
[/tmp-codex/visualiser-independent-retention-20261017-134749/report.json](/tmp-codex/visualiser-independent-retention-20261017-134749/report.json).

The focused backup proof exercises offline/online restoration after raw expiry,
original-session chart reads without recalculation, summary integrity and denial
after a restored grant is revoked. It also runs existing native backup,
online-capture and SingleRun recovery checks:

All **47 checks passed without skips** in
[/tmp-codex/visualiser-retention-backup-retry-20261017-142708/model-proof/report.json](/tmp-codex/visualiser-retention-backup-retry-20261017-142708/model-proof/report.json).
The [driver report](/tmp-codex/visualiser-retention-backup-retry-20261017-142708/report.json)
confirms cleanup. The preserved first run passed the new Visualiser roundtrips
but lacked the Docker Python library for three existing SingleRun tests; the
proof requirements now include that pinned test dependency.

To rerun:

```bash
cd ~/git/JAS-mine/JAS-mine-web &&
TMPDIR=/tmp-codex PIP_DEFAULT_TIMEOUT=60 \
~/simpaths-browser-tests/venv/bin/python scripts/test_batch_queue.py \
  --proof-only \
  --proof-script "$HOME/git/SimPathsWeb/SimPaths/deploy/multirun/visualiser_retention_proof.py" \
  --proof-requirements "$HOME/git/SimPathsWeb/SimPaths/deploy/multirun/requirements-backup-proof.txt" \
  --output "/tmp-codex/visualiser-retention-backup-$(date +%Y%m%d-%H%M%S)"
```

These proofs use tiny fictional files, no scientific simulations and no real
emails. Local checks also exercise the pinned calculations reusing retained
summaries without opening raw files, corruption, chart-section hashes, linked
files, concurrent readers, skipped live writers and scoped invalidation. Native
host UID isolation and full-size backup/restore throughput remain deployment
acceptance checks.
