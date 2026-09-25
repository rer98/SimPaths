#!/bin/sh
# (C) Copyright 2026, by Ross Richardson
# Verify prepared public inputs, copy private writable state and run native MultiRun.
# Invoked by the platform container deadline wrapper; never runs host commands.
# @author ross richardson
set -eu
umask 077
cd /work
sha256sum --check --status /request/inputs.sha256
cp -R /inputs/input ./input
chmod -R u+rwX input
mkdir config tmp
cp /request/run.yml config/run.yml
exec /opt/java/openjdk/bin/java -Xmx2g -XX:ActiveProcessorCount=2 \
  -XX:+ExitOnOutOfMemoryError -Djava.awt.headless=true -Djava.io.tmpdir=/work/tmp \
  -cp /inputs/model.jar simpaths.experiment.SimPathsMultiRun -config run.yml -P root
