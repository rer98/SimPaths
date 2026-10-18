<!-- (C) Copyright 2026, by Ross Richardson

Encrypted recovery of pending and running Visualiser preparation handovers.
@author ross richardson
-->

# Encrypted handover recovery rehearsal

## Scope

The rehearsal combines the real PostgreSQL dump/restore, public-key encryption,
retained-summary backup and preparation admission paths. It uses tiny fictional
inputs and aggregate values. It runs no scientific models or model containers,
sends no real emails and contacts no external storage. An ephemeral encryption
keyring is created for this proof and removed afterwards; the user's keyring and
project encryption keys are never used.

Two native cases exercise a completed baseline and alternative:

1. Both simulations have finished and their resource transfers are pending.
2. One transfer has been adopted by a preparation worker, with the other still
   pending. The worker has committed one configuration's intermediate summary
   and is running a tiny waiting helper when the online snapshot is taken.

The fictional calculation uses the real summary cache, dependency holds,
publication/index helpers, source authority and processing ledger. It does not
test the numerical calculation or its full-size resource requirements. The tiny
helper uses production command supervision and inherited locks; kernel cgroup
enforcement remains covered by the separate delegated-scope proof.

## Assertions

- Completion creates real pending transfers in the same transaction that releases
  model capacity. A competing queued configuration cannot use those reservations.
- Offline backup refuses unsettled preparation. Online backup captures its ledger
  without cancelling the worker or releasing the source's reservations.
- Ready charts and committed intermediate summaries are included. Partial
  publication/scratch and active helper files are omitted.
- GnuPG encrypts the verified backup for the pinned ephemeral recipient. Decryption
  checks the trusted ciphertext checksum and verifies the complete recovered
  backup before publishing it for restoration.
- Restoring into a separate database/state clears old processor reservations.
  Completed model attempts, seeds and settings remain unchanged; preparation can
  resume without running those models again.
- The real preparation coordinator rediscovers omitted queued/partial work on
  startup, even when the source already acknowledged its completion events.
- An interrupted restore resumes safely. The application remains inactive until
  verification and activation. Online activation requires explicit source
  isolation and refuses a source state still held by a launcher.
- The original ownership cookie can read restored charts. Budget approvals,
  source provenance and reusable summaries survive. New required chart files
  are generated; committed summaries are reused instead of parsed again.
- Preparation releases its reservations after completion. A waiting configuration
  can then claim capacity once, without an old reservation being counted twice.
- Another owner cannot read the charts. Routine raw-output expiry preserves
  authorised chart access; a current dataset revocation denies it.

The source's waiting helper is released and joined before the recovered service
is activated. Source completed-job records are checked again to establish that
restoration did not mutate them. This local isolation check does not stop a
different host: actual disaster recovery still needs the operator to isolate the
old machine and its workers before activation.

## Run on the laptop

Networking is needed to install the isolated proof dependencies unless already
cached. Docker, GnuPG, GNU timeout and the existing browser-test Python environment
must be available. No overnight scientific benchmark is needed.

```bash
cd ~/git/JAS-mine/JAS-mine-web &&
TMPDIR=/tmp-codex PIP_DEFAULT_TIMEOUT=60 \
~/simpaths-browser-tests/venv/bin/python scripts/test_batch_queue.py \
  --proof-only \
  --proof-script "$HOME/git/SimPathsWeb/SimPaths/deploy/multirun/backup_handover_proof.py" \
  --proof-requirements "$HOME/git/SimPathsWeb/SimPaths/deploy/multirun/requirements-backup-proof.txt" \
  --output "/tmp-codex/handover-encrypted-recovery-$(date +%Y%m%d-%H%M%S)"
```

The focused proof also repeats the existing encrypted-transport safety and retained
Visualiser backup tests. A passing `model-proof/report.json` must record no
failures, errors or skips; the parent `report.json` must confirm disposable
database cleanup. `--proof-only` intentionally omits the broad queue suite.
The SFTP protocol cases use captured transfers. This does not replace a real
offsite-provider transfer and recovery rehearsal.

## Verification status

**Passed on 18 October 2026: all 30 checks, with no failures, errors or skips.**
The parent report confirms disposable database cleanup. The proof includes:

- Two combined PostgreSQL/GnuPG handover recovery cases: pending transfers and
  a running preparation with a second transfer pending.
- Three local fixture checks for independently executable configurations,
  private storage construction and role-independent summary reuse after raw
  removal.
- Ten encrypted-transport checks, including native GnuPG integrity/key-pinning
  failures and captured SFTP retry/no-overwrite behaviour.
- Fifteen retained-Visualiser backup checks, including offline/online recovery,
  eviction suppression, approved limits, ownership and revocation.

Evidence:

- `/tmp-codex/handover-encrypted-recovery-final-20261018-221156/report.json`
- `/tmp-codex/handover-encrypted-recovery-final-20261018-221156/model-proof/report.json`

The new rehearsal required corrections to its fictional multi-configuration
fixture and inclusion of the production coordinator's startup discovery step.
Production backup, encryption, restoration and admission code required no changes.
The earlier failed reports are preserved in the separate initial/retry evidence
directories. Full-size processing, installed-host isolation and an actual
offsite transfer/restore remain separate deployment acceptance checks.
