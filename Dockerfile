# (C) Copyright 2026, by Ross Richardson
#
# Dockerfile.
#
# @author ross richardson
#

FROM eclipse-temurin:25-jre@sha256:bb036ed6cfdc57e3da7c22634d15f1b840d2caf76183861c80e81ca4b5104abb

RUN apt-get update && apt-get install -y xvfb libxtst6 libxi6 libxrender1 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY singlerun.jar /app/app.jar

COPY webserver.properties .
COPY input/ input/
COPY README.md .
COPY license.txt .

RUN mkdir -p output

EXPOSE 7070

ENV JAVA_OPTS=""

CMD ["sh", "-c", "Xvfb :99 -screen 0 1024x768x24 & export DISPLAY=:99 && exec java $JAVA_OPTS -cp app.jar simpaths.experiment.SimPathsWebBootstrap"]
