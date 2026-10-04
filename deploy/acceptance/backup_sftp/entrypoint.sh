#!/bin/sh
# (C) Copyright 2026, by Ross Richardson
# Install only disposable SSH server credentials inside the read-only container.
# @author ross richardson
set -eu
mkdir -p /run/sshd /run/keys
install -m 600 /config/host_key /run/keys/host_key
install -m 644 /config/authorized_keys /run/keys/authorized_keys
exec /usr/sbin/sshd -D -e -f /config/sshd_config
