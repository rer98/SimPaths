# (C) Copyright 2026, by Ross Richardson
# Disposable OpenSSH SFTP service for the local encrypted-backup rehearsal.
# @author ross richardson
FROM python:3.12-slim
ARG REHEARSAL_UID=1000
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends openssh-server \
    && rm -rf /var/lib/apt/lists/* /etc/ssh/ssh_host_*
RUN useradd --uid "$REHEARSAL_UID" --user-group --no-create-home \
       --home-dir /backups --shell /bin/sh --password '*' simpaths_backup_test \
    && mkdir -p /srv/backups /run/sshd /run/keys /config
COPY entrypoint.sh /usr/local/bin/rehearsal-sftp
RUN chmod 755 /usr/local/bin/rehearsal-sftp
ENTRYPOINT ["/usr/local/bin/rehearsal-sftp"]
