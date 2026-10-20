# (C) Copyright 2026, by Ross Richardson
# Launch UK user-data SingleRun; bundled training files remain inactive examples.
# @author ross richardson
FROM eclipse-temurin:25-jre@sha256:bb036ed6cfdc57e3da7c22634d15f1b840d2caf76183861c80e81ca4b5104abb
# Security floors from the 15 October 2026 audit; allow newer distro fixes.
RUN apt-get update && apt-get install -y xvfb libxtst6 libxi6 libxrender1 libexpat1 libxml2-16 \
    libfreetype6 libpng16-16t64 libssl3t64 openssl openssl-provider-legacy \
    libx11-6 libx11-data libx11-xcb1 libxpm4 \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libexpat1)" ge 2.7.4-1ubuntu0.1 \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libxml2-16)" ge 2.15.2+dfsg-0.1ubuntu0.2 \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libfreetype6)" ge 2.14.2+dfsg-1ubuntu0.2 \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libpng16-16t64)" ge 1.6.57-1ubuntu0.1 \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libssl3t64)" ge 3.5.5-1ubuntu3.7 \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' openssl)" ge 3.5.5-1ubuntu3.7 \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' openssl-provider-legacy)" ge 3.5.5-1ubuntu3.7 \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libx11-6)" ge 2:1.8.13-1ubuntu0.1 \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libx11-data)" ge 2:1.8.13-1ubuntu0.1 \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libx11-xcb1)" ge 2:1.8.13-1ubuntu0.1 \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libxpm4)" ge 1:3.5.17-1ubuntu0.26.04.2 \
    && rm -rf /var/lib/apt/lists/*
# Begin audited Pebble exclusion (20 October 2026).
# The pinned Temurin entrypoint and inherited, unowned executable were inspected.
RUN set -eu; \
    printf '%s\n' '4f41c720f4050a7389a1b6a3b69c322d417d8134338cc07e1be86cf82d650a63  /__cacert_entrypoint.sh' | sha256sum -c -; \
    if [ -e /usr/bin/pebble ] || [ -L /usr/bin/pebble ]; then \
        test -f /usr/bin/pebble; test ! -L /usr/bin/pebble; \
        printf '%s\n' '5e33a1304f207bb275b1b4cc3dc4c05fdd88755f5c705e2fd569924ec72dc061  /usr/bin/pebble' | sha256sum -c -; \
        if dpkg-query -S /usr/bin/pebble >/dev/null 2>&1; then exit 1; else test "$?" -eq 1; fi; \
        rm -- /usr/bin/pebble; \
    fi; \
    test ! -e /usr/bin/pebble; test ! -L /usr/bin/pebble
# End audited Pebble exclusion.
WORKDIR /app
COPY singlerun.jar app.jar
COPY webserver.properties README.md license.txt COPYRIGHT.md ./
COPY --chown=10001:10001 input/ input/
# Fresh context contains no input database or saved processed population.
RUN mkdir -p output .simpaths-startup && chown 10001:10001 output .simpaths-startup \
    && chmod 0755 /app \
    && chmod 0444 app.jar webserver.properties README.md license.txt COPYRIGHT.md
LABEL org.jasmine.security="session-v1"
USER 10001:10001
EXPOSE 7070
ENV JAVA_OPTS=""
CMD ["sh", "-c", "Xvfb :99 -screen 0 1024x768x24 & export DISPLAY=:99 && exec java $JAVA_OPTS -XshowSettings:vm -cp app.jar simpaths.experiment.SimPathsUserDataStartup"]
