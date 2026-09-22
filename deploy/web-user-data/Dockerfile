# (C) Copyright 2026, by Ross Richardson
# Launch UK user-data SingleRun; bundled training files remain inactive examples.
# @author ross richardson
FROM eclipse-temurin:25-jre
RUN apt-get update && apt-get install -y xvfb libxtst6 libxi6 libxrender1 && rm -rf /var/lib/apt/lists/*
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
