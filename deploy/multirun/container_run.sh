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
mkdir config tmp
if [ -n "$(find input -mindepth 1 ! -type f ! -type d -print -quit)" ]; then
  echo "Prepared copy contains a link or special file" >&2
  exit 1
fi
find input -type f -printf '%P\n' | LC_ALL=C sort > tmp/input-files.txt
cmp /request/input-files.txt tmp/input-files.txt
# Verify the private bytes too: a source change during the copy cannot silently
# turn a valid receipt into a different run input. Keep the pinned JAR check.
sed 's@  /inputs/input/@  /work/input/@' /request/inputs.sha256 | sha256sum --check --status
chmod -R u+rwX input
cp /request/run.yml config/run.yml
exec /opt/java/openjdk/bin/java -Xmx"${1:-2g}" -XX:ActiveProcessorCount=2 \
  -XX:+ExitOnOutOfMemoryError -Djava.awt.headless=true -Djava.io.tmpdir=/work/tmp \
  -cp /inputs/model.jar simpaths.experiment.SimPathsMultiRun -config run.yml -P root
