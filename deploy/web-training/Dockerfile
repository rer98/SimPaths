# (C) Copyright 2026, by Ross Richardson
# Prepare a base UK training database and launch configurable web SingleRun.
# @author ross richardson
FROM eclipse-temurin:25-jre@sha256:bb036ed6cfdc57e3da7c22634d15f1b840d2caf76183861c80e81ca4b5104abb
RUN apt-get update && apt-get install -y xvfb libxtst6 libxi6 libxrender1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY singlerun.jar app.jar
COPY webserver.properties README.md license.txt COPYRIGHT.md ./
COPY --chown=10001:10001 input/ input/
# Fresh context contains no input database or saved processed population.
# Keep database creation and account setup in one layer to avoid storing two copies.
RUN java -Xmx3g -Djava.awt.headless=true -cp app.jar simpaths.experiment.SimPathsWebBootstrap --prepare-only \
    && java -cp app.jar microsim.web.server.DatabaseQueryAccess input/input query-access.json \
    && find input -user root -exec chown 10001:10001 {} +
RUN mkdir -p output .simpaths-startup && chown 10001:10001 output .simpaths-startup \
    && chmod 0755 /app \
    && chmod 0444 app.jar webserver.properties README.md license.txt COPYRIGHT.md query-access.json
LABEL org.jasmine.security="session-v1"
USER 10001:10001
EXPOSE 7070
ENV JAVA_OPTS=""
CMD ["sh", "-c", "Xvfb :99 -screen 0 1024x768x24 & export DISPLAY=:99 && exec java $JAVA_OPTS -XshowSettings:vm -cp app.jar simpaths.experiment.SimPathsTrainingStartup"]
