# Web Quick Start (UK/2019 training profile)

The web entry point is `simpaths.experiment.SimPathsWebBootstrap`. The normal
`java -jar singlerun.jar` desktop/CLI entry point remains `SimPathsStart`.
Use Java 25 and the matching web-enabled `5.2.0-web-SNAPSHOT` core.

Run in a **private writable workspace** containing `input/`, `webserver.properties`
and the shaded JAR. The initial profile selects UK/2019 training data, 50,000
people, end year 2026, fixed seed 606 and the normal observer/chart defaults.

```bash
# Prepare once before launching the HTTP server (no display needed).
java -Djava.awt.headless=true -cp singlerun.jar simpaths.experiment.SimPathsWebBootstrap --prepare-only

# Start the HTTP server with validated prepared inputs; Docker supplies Xvfb.
java -cp singlerun.jar simpaths.experiment.SimPathsWebBootstrap
```

For a fresh workspace without `input/input.mv.db`, either command prepares it
first. An existing database is reused if it passes basic readiness checks; no
preparation marker is required. A failed check stops startup without rebuilding
or replacing the database. Resolve access problems first; failure does not
necessarily mean that regeneration is needed.

To deliberately rebuild a private workspace, add `--rebuild-inputs` (optionally
with `--prepare-only`). There is no automatic backup. Preparation uses the same
non-visual operations as the desktop CLI: training selection, country/year record,
time-series and alignment maps, population and tax-donor database construction,
and converter setup. It replaces the effective policy schedule with the training
template. Normal reuse does not replace that schedule. Training preparation CSV
and template checks run only when preparation is requested or the database is
absent; ordinary runtime inputs must still be available.

Startup and web Build check the input database using read-only data access with
`IFEXISTS=TRUE`. Each of HOUSEHOLD_UK_2019, BENEFITUNIT_UK_2019, PERSON_UK_2019,
DONORPERSON, DONORTAXUNIT, DONORPERSONPOLICY and DONORTAXUNITPOLICY must contain
at least one row. Build rejects a failed check before changing engine state and
never prepares inputs automatically. Merely inspecting parameters does neither.

There is no JAR/input-tree hashing or automatic invalidation on file changes.
Old `.simpaths-web-profile.properties` files are ignored and left in place; new
markers are not written. Repackaging the JAR does not force input preparation.

These checks establish basic structural readiness, not training-data provenance,
source-to-database freshness, full scientific consistency or compatibility with
every possible code change. Operators must explicitly request preparation when
they intend to regenerate database contents from changed sources. Runtime input
changes can affect a build directly; changing generation sources does not update
the database automatically. Dependency-aware detection remains deferred.

The start year is fixed to 2019. Build-time population size must be positive;
end year must be 2019–2026. Generic parameter conversion still validates other
submitted values. Initial values are defaults, not a prohibition on all scenario
parameters. Population size, start/end year and random-seed settings are
marked nonmodifiable during execution because their effects occur at build.

The Docker image now invokes the bootstrap. On-demand first preparation can
take longer than an orchestrator's health timeout: provision prepared workspaces
before normal launch, or measure and configure readiness timeouts. This change
does not implement workspace provisioning or the production profile store.

Each build keeps its own native timestamped input/CSV directory. The output
**database is shared by every run in the same JVM session**, with separate
experiment IDs so SQL can compare runs. Reset preserves that output database;
final server shutdown closes it. The collector's existing database-export
setting still controls which model data is written (the default is CSV export).

Starting-population and processed-population factories are retained separately
and reused while their respective database paths stay unchanged. A changed path
closes and replaces the corresponding factory; normal JVM shutdown closes both.
Each load/save closes its own entity manager, including on failure. Default
processed-population persistence retains the first build's database path; an
explicitly configured MultiRun persistence path is retained. These changes do not
alter the core's shared output-database lifetime. JVM shutdown cleanup assumes
one simulation session per JVM; stopping an embedded server without exiting its
JVM does not close these retained input factories.

Validation must cover the web build/run/reset lifecycle separately from CLI
execution. Duplicate chart titles and complete optional-path shutdown auditing
remain follow-ups; a successful first build does not prove full lifecycle correctness.
