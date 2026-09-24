package simpaths.experiment;

import java.nio.file.*;
import java.sql.*;
import java.util.*;
import microsim.engine.SimulationEngine;
import microsim.gui.GuiUtils;
import simpaths.model.SimPathsModel;

/* (C) Copyright 2026, by Ross Richardson
 *
 * Offline administrator helper compiled against the supplied SimPaths JAR.
 *
 * @author ross richardson
 *
 */
public final class PrepareQuickStart {
    private static int population = 50000;
    private static long count(Connection c, String sql) throws SQLException {
        try (var q = c.createStatement(); var r = q.executeQuery(sql)) {
            r.next(); return r.getLong(1);
        }
    }

    private static void verify() throws Exception {
        Path base = Path.of("input/input").toAbsolutePath();
        SimPathsSetupService.verifyDatabase(base);
        Properties result = new Properties();
        try (var c = DriverManager.getConnection("jdbc:h2:file:" + base
                + ";IFEXISTS=TRUE;ACCESS_MODE_DATA=r", "sa", "")) {
            if (count(c, "SELECT COUNT(*) FROM PROCESSED") != 1)
                throw new IllegalStateException("Expected exactly one processed population");
            long id;
            try (var q = c.createStatement(); var r = q.executeQuery(
                    "SELECT ID FROM PROCESSED WHERE COUNTRY='UK' AND START_YEAR=2019"
                    + " AND POP_SIZE=" + population + " AND NO_TARGETS=FALSE")) {
                if (!r.next()) throw new IllegalStateException("Prepared profile does not match");
                id = r.getLong(1);
            }
            for (String table : List.of("HOUSEHOLD", "BENEFITUNIT", "PERSON")) {
                long n = count(c, "SELECT COUNT(*) FROM " + table + " WHERE PRID=" + id);
                if (n == 0) throw new IllegalStateException("Empty processed " + table);
                result.setProperty(table.toLowerCase(Locale.ROOT), Long.toString(n));
            }
        }
        try (var out = Files.newOutputStream(Path.of("verified.properties"))) {
            result.store(out, "Read-only processed population verification");
        }
    }

    private static void build(boolean prepare) throws Exception {
        if (prepare && Files.exists(Path.of("input/input.mv.db")))
            throw new IllegalStateException("Preparation requires inputs without an existing database");
        if (!prepare) verify();
        GuiUtils.setWebMode(true);
        var config = SimPathsStartupConfig.quickStart(population);
        SimPathsSetupService.prepareQuickStart(config, false);
        SimPathsStart.configureWeb(config);
        SimPathsModel.setPersistPopulation(true);
        // Only administrative preparation targets the packaged root database.
        // The loading check deliberately uses the ordinary first-run-copy default.
        if (prepare) SimPathsModel.setPersistDatabasePath(Path.of("input/input").toAbsolutePath().toString());
        var engine = SimulationEngine.getInstance();
        long started = System.nanoTime();
        engine.reset();
        engine.setExperimentBuilder(new SimPathsStart());
        engine.setup();
        engine.buildModels();
        var model = (SimPathsModel) engine.getManager(SimPathsModel.class.getCanonicalName());
        Properties result = new Properties();
        result.setProperty("person", Integer.toString(model.getPersons().size()));
        result.setProperty("household", Integer.toString(model.getHouseholds().size()));
        result.setProperty("benefitunit", Integer.toString(model.getBenefitUnits().size()));
        result.setProperty("buildSeconds", Double.toString((System.nanoTime() - started) / 1e9));
        result.setProperty("persistencePath", SimPathsModel.getPersistDatabasePath());
        if (!prepare && Path.of(SimPathsModel.getPersistDatabasePath()).toAbsolutePath().normalize()
                .equals(Path.of("input/input").toAbsolutePath().normalize()))
            throw new IllegalStateException("Loading check unexpectedly retained the root database");
        try (var out = Files.newOutputStream(Path.of("build.properties"))) {
            result.store(out, "Build only: no simulation events executed");
        }
        // Normal JVM shutdown runs the existing database-factory cleanup hooks.
        engine.quit();
    }

    public static void main(String[] args) {
        try {
            if (args.length < 1 || args.length > 2) throw new IllegalArgumentException("Expected mode [population]");
            population = args.length == 2 ? Integer.parseInt(args[1]) : 50000;
            SimPathsStartupConfig.quickStart(population);
            switch (args[0]) {
                case "prepare" -> build(true);
                case "verify" -> verify();
                case "load" -> build(false);
                default -> throw new IllegalArgumentException("Expected prepare, verify or load");
            }
        } catch (Throwable failure) {
            failure.printStackTrace();
            System.exit(1);
        }
    }
}
