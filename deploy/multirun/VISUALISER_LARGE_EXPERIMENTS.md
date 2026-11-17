<!-- (C) Copyright 2026, by Ross Richardson

Bounded configuration selection, summary preservation and large-experiment capacity.
@author ross richardson
-->

# Large experiments in the Visualiser

The automatic preparation cutoff is **nine configurations, including the
baseline**. Experiments at or below that size keep their direct chart workflow:
completion prepares standalone charts and comparisons against the chosen
baseline. Larger experiments open a configuration chooser first. It shows fifty
configurations per page, searches names, numbers and saved settings, filters a
varying parameter by value, and can show only completed available configurations.
Up to nine configurations can be selected, including an optional baseline.

The browser remembers only configuration identifiers and the selected baseline.
Every return checks current ownership and dataset permission before displaying
names, settings or charts. Changing this view does not change the experiment's
saved baseline, frozen scientific settings or any other browser's view. The
Visualiser offers **Change configurations** to return to the chooser. Chart
downloads contain only the selected configurations. A queued selection is allowed;
its charts become available as the selected simulations finish.

## Preparation and scientific results

For large experiments, completion prepares and protects per-seed scientific
summaries for all available configurations. Detailed standalone charts and
baseline comparisons are prepared when requested, then reused from the existing
cache. First-time charts can therefore take time; the chooser states this before
opening. Small experiments keep automatic chart preparation.

Summary-only preparation uses the same bounded worker, original deadline,
shutdown checkpoints and current source authorisation. It releases processing
capacity after protecting the summaries. A chart request arriving during summary
preparation is recorded separately so worker progress cannot overwrite it.
Summary receipts have a separate limit from detailed publications. New background
discovery queues at most 64 configurations in one pass and avoids repeatedly
assembling a full experiment for each configuration.

The calculation and runner bundles are unchanged. Compression affects storage
only: new summaries use gzip when smaller, retaining checksums for both the
original JSON and stored bytes. Readers verify both while restoring bounded JSON
for the calculation. Existing uncompressed summaries remain readable and are not
rewritten. Reads, compression, selection and regeneration do not renew any source
or chart retention deadline.

## Capacity and deployment

The VM configuration now accepts `summary_cache_entries` (default 16,384, maximum
32,768) and `per_owner_summary_configurations` (default 2,048, maximum 8,192).
The former counts **seeds**, the latter configurations. They are separate from
the detailed publication and per-owner chart limits. A 1,024-configuration
experiment with ten repetitions needs 10,240 seed slots.

Two previously measured 52-year per-seed summary files are about 32 MB each.
Gzip at level six reduces each to about 2.59 MB. Extrapolating those two files to
10,240 seeds gives approximately **24.7 GiB**, before receipts and other retained
experiments. This is a sizing estimate from two summaries, not a measurement of
1,024 completed configurations. A provisional 32 GiB summary ceiling inside a
48 GiB total Visualiser budget would cover that estimate and chart working space;
it is not enabled by these source changes.

The 16 November read-only host report still shows an 8 GiB summary budget, a
16 GiB total budget and 256 seed slots. Merely lifting the entry limit does not
make the byte budget adequate. Although about 140.6 GiB is free, the growth check
protects the full model pool allowance (about 127.9 GiB) plus a 12 GiB service
reserve. Deployment must reconcile the proposed Visualiser allocation with that
pool allowance or add capacity. It must preserve current model reservations and
existing protected summaries. No pool reduction or larger live cache budget is
authorised or performed by running the tests.

## Recovery and validation

Backup selection and verification understand both summary encodings, copy the
compressed payload where present, and verify the uncompressed checksum too.
Recovery retains canonical source identities, grants and original deadlines.
Transient summary-preparation receipts can be rediscovered from the database and
retained cache. Install compatible backup/readers before compressed writes resume;
an older reader cannot interpret the new summary receipt format.

The focused qualification covers nine versus ten configurations, all 21 chooser
pages for a 1,025-configuration fixture, selected chart and ZIP isolation, late
requests during summary preparation, fresh revocation, unchanged calculations,
10,240 synthetic cache entries, fixed deadlines, and compressed backup recovery
after raw-only deletion. Browser checks use fictional local pages, with a separate
authenticated small-experiment workflow through the private service transport.
These tests run no scientific simulations. Native database and browser completion
is recorded separately from local unit checks and host deployment acceptance.
