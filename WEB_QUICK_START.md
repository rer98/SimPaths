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

For a fresh workspace without a database, either command prepares it first.
If a database already exists without a matching preparation record, the
bootstrap refuses to silently replace it. To deliberately rebuild a private
workspace, add `--rebuild-inputs` (optionally with `--prepare-only`). Never do
this in a shared input directory or against the only copy of research inputs.

Preparation reuses the same non-visual operations as the desktop CLI:
training selection, country/year record, time-series and alignment maps,
initial population and tax-donor database construction, and converter setup.
The training policy template is copied to the effective top-level schedule.
Population and donor tables must be present and nonempty before completion.

The private `input/.simpaths-web-profile.properties` record contains the profile
version, model-artifact SHA-256 and combined input SHA-256. It is a local reuse
guard, not yet the production scientific manifest or a signed data artefact.
Hashing large inputs costs I/O, but does not rebuild the database. Parameter
inspection does neither. The web build validates the inputs again before it
changes engine state. Repackaging/changing the model JAR invalidates reuse.

For this first implementation, **all non-hidden input changes invalidate
preparation**, including Excel replacements. Uploads remain allowed under the
existing policy, but Build rejects stale inputs with a preparation message.
Fine-grained workbook dependency classification and an online reprepare action
remain future work. Rebuilding restores the approved training policy template.

The start year is fixed to 2019. Build-time population size must be positive;
end year must be 2019–2026. Generic parameter conversion still validates other
submitted values. Initial values are defaults, not a prohibition on all scenario
parameters. Population size, start/end year and random-seed settings are
marked nonmodifiable during execution because their effects occur at build.

The Docker image now invokes the bootstrap. On-demand first preparation can
take longer than an orchestrator's health timeout: provision prepared workspaces
before normal launch, or measure and configure readiness timeouts. This change
does not implement workspace provisioning or the production profile store.

Validation must cover the web build/run/reset lifecycle separately from CLI
execution. In particular, SimPaths cached input factories across rebuilds and
duplicate chart titles are separate known follow-ups; a successful first build
does not prove repeated-build correctness.
