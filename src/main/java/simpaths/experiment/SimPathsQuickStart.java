package simpaths.experiment;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.nio.file.*;
import java.sql.*;
import java.util.Map;
import microsim.data.db.DatabaseUtils;
import microsim.engine.SimulationEngine;
import microsim.gui.shell.MicrosimShell;
import microsim.web.SimulationServer;
import simpaths.data.Parameters;
import simpaths.model.SimPathsModel;
import simpaths.model.enums.Country;

/** Explicit prepared training profile; ordinary SingleRun/MultiRun are unaffected. */
public final class SimPathsQuickStart {
    private static boolean enabled;
    private static SimPathsStartupConfig selectedProfile = SimPathsStartupConfig.quickStart();
    private SimPathsQuickStart() {}

    static boolean isEnabled() { return enabled; }

    public static void configure() {
        try {
            selectedProfile = profileConfiguration(readValidatedReceipt(Path.of("profile.json")));
        } catch (IOException e) {
            throw new IllegalArgumentException("Quick Start requires a valid profile.json", e);
        }
        requireReady(false);
        Parameters.setTrainingFlag(true);
        SimPathsSetupService.registerConverters();
        Parameters.loadTimeSeriesFactorMaps(Country.UK);
        Parameters.instantiateAlignmentMaps();
        Parameters.setTaxDonorInputFileName("tax_donor_population_UK");
        SimPathsStart.configureWeb(selectedProfile);
        SimPathsModel.setPersistPopulation(true);
        enabled = true;
    }

    static void validateRequest(Map<String, Object> parameters) {
        selectedProfile.validatePreparedBuildParameters(parameters);
        requireReady(false);
    }

    static void attach(SimPathsModel model) {
        if (enabled) model.setQuickStartBuildValidation(() -> {
            selectedProfile.validatePreparedBuildParameters(Map.of(
                    "country", model.getCountry(), "startYear", model.getStartYear(),
                    "endYear", model.getEndYear(), "popSize", model.getPopSize(),
                    "fixRandomSeed", model.getFixRandomSeed(),
                    "randomSeedIfFixed", model.getRandomSeedIfFixed(),
                    "useWeights", model.isUseWeights(),
                    "ignoreTargetsAtPopulationLoad", model.isIgnoreTargetsAtPopulationLoad()));
            requireReady(true);
        });
    }

    static JsonNode readReceipt(Path file) throws IOException {
        return readValidatedReceipt(file).path("actual_counts");
    }

    static SimPathsStartupConfig profileConfiguration(JsonNode receipt) throws IOException {
        try {
            return SimPathsStartupConfig.quickStart(receipt.path("profile").path("requested_population").asInt(-1));
        } catch (IllegalArgumentException e) {
            throw new IOException("Unsupported Quick Start population", e);
        }
    }

    static JsonNode readValidatedReceipt(Path file) throws IOException {
        JsonNode receipt = new ObjectMapper().readTree(file.toFile());
        if (receipt == null) throw new IOException("Quick Start profile receipt is empty");
        JsonNode profile = receipt.path("profile");
        int population = profileConfiguration(receipt).populationSize();
        if (receipt.path("format_version").asInt(-1) != 1
                || !receipt.path("profile_id").asText().equals("uk-2019-training-" + population + "-seed606")
                || !profile.path("country").asText().equals("UK")
                || profile.path("start_year").asInt(-1) != 2019
                || profile.path("end_year").asInt(-1) != 2026
                || !profile.path("requested_population").isIntegralNumber()
                || !profile.path("requested_population").canConvertToInt()
                || profile.path("seed").asLong(-1) != 606
                || !profile.path("training_data").asBoolean(false)
                || !profile.path("include_observer").asBoolean(false)
                || !profile.path("use_weights").isBoolean() || profile.path("use_weights").asBoolean()
                || !profile.path("ignore_population_targets").isBoolean()
                || profile.path("ignore_population_targets").asBoolean())
            throw new IOException("Missing or incompatible Quick Start profile receipt");
        return receipt;
    }

    static void verifyProcessed(Path base, JsonNode counts, boolean live) throws IOException {
        verifyProcessed(base, counts, live, 50000);
    }

    static void verifyProcessed(Path base, JsonNode counts, boolean live, int population) throws IOException {
        SimPathsStartupConfig.quickStart(population);
        // A retained factory is already connected using AUTO_SERVER. Reuse its
        // connection settings for SELECTs, then roll back; do not close that factory.
        // Closed packaged files are opened in physical read-only mode instead.
        String options = live ? ";AUTO_SERVER=TRUE;TRACE_LEVEL_FILE=0;TRACE_LEVEL_SYSTEM_OUT=0"
                              : ";ACCESS_MODE_DATA=r";
        try (var c = DriverManager.getConnection("jdbc:h2:file:" + base.toAbsolutePath()
                + ";IFEXISTS=TRUE" + options, "sa", "")) {
            c.setReadOnly(true);
            c.setAutoCommit(false);
            try (var q = c.createStatement()) {
                long id;
                try (var r = q.executeQuery("SELECT ID FROM PROCESSED WHERE COUNTRY='UK'"
                        + " AND START_YEAR=2019 AND POP_SIZE=" + population + " AND NO_TARGETS=FALSE")) {
                    if (!r.next()) throw new IOException("Prepared population is missing");
                    id = r.getLong(1);
                    if (r.next()) throw new IOException("Prepared population is ambiguous");
                }
                for (String table : new String[]{"HOUSEHOLD", "BENEFITUNIT", "PERSON"}) {
                    try (var r = q.executeQuery("SELECT COUNT(*) FROM " + table + " WHERE PRID=" + id)) {
                        r.next();
                        long expected = counts.path(table.toLowerCase(java.util.Locale.ROOT)).asLong(-1);
                        if (expected <= 0 || r.getLong(1) != expected)
                            throw new IOException("Prepared " + table + " count does not match the profile receipt");
                    }
                }
            } finally {
                c.rollback();
            }
        } catch (SQLException e) {
            throw new IOException("Cannot read prepared population: " + e.getMessage(), e);
        }
    }

    static void requireReady(boolean atModelBuild) {
        try {
            JsonNode receipt = readValidatedReceipt(Path.of("profile.json"));
            if (!profileConfiguration(receipt).equals(selectedProfile))
                throw new IOException("Prepared profile changed during the session; start a separate session");
            JsonNode counts = receipt.path("actual_counts");
            Path root = Path.of(Parameters.getInputDirectory(), "input");
            SimPathsSetupService.verifyDatabase(root);
            verifyProcessed(root, counts, false, selectedProfile.populationSize());
            String retained = SimPathsModel.getPersistDatabasePath();
            String effective = retained != null ? retained
                    : atModelBuild ? DatabaseUtils.databaseInputUrl : null;
            if (effective != null && !Path.of(effective).toAbsolutePath().normalize()
                    .equals(root.toAbsolutePath().normalize()))
                verifyProcessed(Path.of(effective), counts, retained != null, selectedProfile.populationSize());
        } catch (IOException e) {
            throw new IllegalArgumentException("Quick Start needs a verified prepared package: "
                    + e.getMessage() + ". Provision it with prepare_quick_start_profile.py. "
                    + "No automatic preparation or overwrite was attempted.", e);
        }
    }

    public static void main(String[] args) {
        if (args.length > 1 || (args.length == 1 && !args[0].equals("--desktop")
                && !args[0].equals("--web")))
            throw new IllegalArgumentException("Usage: SimPathsQuickStart [--desktop|--web]");
        configure();
        if (args.length == 1 && args[0].equals("--web")) {
            SimulationServer.main(new String[0]);
        } else {
            var engine = SimulationEngine.getInstance();
            var shell = new MicrosimShell(engine);
            shell.setVisible(true);
            engine.setExperimentBuilder(new SimPathsStart());
            engine.setup();
        }
    }
}
