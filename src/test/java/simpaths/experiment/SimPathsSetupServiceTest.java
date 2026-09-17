package simpaths.experiment;

import java.nio.file.*;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.apache.commons.beanutils.ConvertUtils;
import simpaths.data.Parameters;
import simpaths.model.enums.UnionMatchingMethod;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import simpaths.model.enums.Country;

class SimPathsSetupServiceTest {
    @TempDir Path temporary;

    @Test void profileRejectsConflictingAndInvalidBuildValues() {
        var config = SimPathsStartupConfig.quickStart();
        assertEquals(50000, config.populationSize());
        assertEquals(2026, config.endYear());
        assertEquals(606L, config.seed());
        assertTrue(config.includeObserver());
        config.validateBuildParameters(Map.of("startYear", 2019.0, "endYear", "2026", "popSize", 50000));
        for (Map<String, Object> bad : java.util.List.<Map<String, Object>>of(
                Map.of("startYear", 2011), Map.of("startYear", 2019.5),
                Map.of("popSize", 0), Map.of("endYear", 2018))) {
            assertThrows(IllegalArgumentException.class, () -> config.validateBuildParameters(bad));
        }
    }

    @Test void readyDatabaseIsReusedWithoutMarkerOrPreparationSources() throws Exception {
        createReadyDatabase(temporary.resolve("input"));
        Path policy = temporary.resolve("EUROMODpolicySchedule.xlsx");
        Files.writeString(policy, "existing policy");
        byte[] before = Files.readAllBytes(temporary.resolve("input.mv.db"));
        try (var parameters = mockStatic(Parameters.class)) {
            parameters.when(Parameters::getInputDirectory).thenReturn(temporary.toString());
            SimPathsSetupService.prepareQuickStart(SimPathsStartupConfig.quickStart(), false);
            SimPathsSetupService.requirePreparedInputs();
            parameters.verify(() -> Parameters.loadTimeSeriesFactorMaps(Country.UK));
            parameters.verify(Parameters::instantiateAlignmentMaps);
        }
        assertArrayEquals(before, Files.readAllBytes(temporary.resolve("input.mv.db")));
        assertEquals("existing policy", Files.readString(policy));
        assertFalse(Files.exists(temporary.resolve(".simpaths-web-profile.properties")));
    }

    @Test void oldMarkerAndChangedSourcesDoNotInvalidateReadyDatabase() throws Exception {
        createReadyDatabase(temporary.resolve("input"));
        Path marker = temporary.resolve(".simpaths-web-profile.properties");
        Files.writeString(marker, "profile=old\nartifact=obsolete\ninputs=obsolete\n");
        byte[] before = Files.readAllBytes(temporary.resolve("input.mv.db"));
        try (var parameters = mockStatic(Parameters.class)) {
            parameters.when(Parameters::getInputDirectory).thenReturn(temporary.toString());
            for (String value : java.util.List.of("first", "changed")) {
                Files.writeString(temporary.resolve("policy.xlsx"), value);
                Files.writeString(temporary.resolve("population.csv"), value);
                Files.writeString(temporary.resolve("app.jar"), value);
                SimPathsSetupService.prepareQuickStart(SimPathsStartupConfig.quickStart(), false);
                SimPathsSetupService.requirePreparedInputs();
            }
        }
        assertArrayEquals(before, Files.readAllBytes(temporary.resolve("input.mv.db")));
        assertEquals("profile=old\nartifact=obsolete\ninputs=obsolete\n", Files.readString(marker));
    }

    @Test void missingDatabaseAtBuildIsRejectedWithoutCreatingIt() {
        try (var parameters = mockStatic(Parameters.class)) {
            parameters.when(Parameters::getInputDirectory).thenReturn(temporary.toString());
            var error = assertThrows(IllegalArgumentException.class, SimPathsSetupService::requirePreparedInputs);
            assertTrue(error.getMessage().contains("No automatic rebuild"));
            assertFalse(Files.exists(temporary.resolve("input.mv.db")));
        }
    }

    @Test void preparationRunsForAbsentDatabaseOrExplicitRebuild() throws Exception {
        for (boolean rebuild : new boolean[]{false, true}) {
            Path input = Files.createDirectory(temporary.resolve(rebuild ? "explicit" : "fresh"));
            Files.createDirectories(input.resolve("InitialPopulations/training"));
            Files.createDirectories(input.resolve("EUROMODoutput/training"));
            Files.writeString(input.resolve("InitialPopulations/training/population_initial_UK_2019.csv"), "source");
            Files.writeString(input.resolve("EUROMODoutput/training/EUROMODpolicySchedule.xlsx"), "training policy");
            Files.writeString(input.resolve("EUROMODpolicySchedule.xlsx"), "old policy");
            if (rebuild) Files.writeString(input.resolve("input.mv.db"), "existing database");
            try (var parameters = mockStatic(Parameters.class);
                 var setup = mockStatic(SimPathsSetupService.class, CALLS_REAL_METHODS)) {
                parameters.when(Parameters::getInputDirectory).thenReturn(input.toString());
                setup.when(() -> SimPathsSetupService.prepareDesktopInputs(Country.UK, 2019, false, false))
                    .thenAnswer(invocation -> {
                        assertEquals("training policy", Files.readString(input.resolve("EUROMODpolicySchedule.xlsx")));
                        Files.deleteIfExists(input.resolve("input.mv.db"));
                        createReadyDatabase(input.resolve("input"));
                        return null;
                    });
                SimPathsSetupService.prepareQuickStart(SimPathsStartupConfig.quickStart(), rebuild);
                setup.verify(() -> SimPathsSetupService.prepareDesktopInputs(Country.UK, 2019, false, false));
            }
            assertFalse(Files.exists(input.resolve(".simpaths-web-profile.properties")));
            SimPathsSetupService.verifyDatabase(input.resolve("input"));
        }
    }

    private static void createReadyDatabase(Path base) throws Exception {
        try (var connection = java.sql.DriverManager.getConnection("jdbc:h2:file:" + base, "sa", "");
             var statement = connection.createStatement()) {
            for (String table : java.util.List.of("HOUSEHOLD_UK_2019", "BENEFITUNIT_UK_2019", "PERSON_UK_2019",
                    "DONORPERSON", "DONORTAXUNIT", "DONORPERSONPOLICY", "DONORTAXUNITPOLICY")) {
                statement.execute("CREATE TABLE " + table + "(ID INT)");
                statement.execute("INSERT INTO " + table + " VALUES(1)");
            }
        }
    }

    @Test void unreadableExistingDatabaseIsNeverSilentlyRebuilt() throws Exception {
        String oldInput = Parameters.getInputDirectory();
        boolean oldTraining = Parameters.trainingFlag;
        try {
            Parameters.setInputDirectory(temporary.toString());
            Files.createDirectories(temporary.resolve("InitialPopulations/training"));
            Files.createDirectories(temporary.resolve("EUROMODoutput/training"));
            Files.writeString(temporary.resolve("InitialPopulations/training/population_initial_UK_2019.csv"), "data");
            Files.writeString(temporary.resolve("EUROMODoutput/training/EUROMODpolicySchedule.xlsx"), "template");
            Path db = temporary.resolve("input.mv.db");
            Files.writeString(db, "existing database");
            assertThrows(java.io.IOException.class, () ->
                    SimPathsSetupService.prepareQuickStart(SimPathsStartupConfig.quickStart(), false));
            assertEquals("existing database", Files.readString(db));
            assertFalse(Files.exists(temporary.resolve(".simpaths-web-profile.properties")));
        } finally {
            Parameters.setInputDirectory(oldInput);
            Parameters.setTrainingFlag(oldTraining);
        }
    }

    @Test void sharedConverterPreservesDesktopFallback() {
        SimPathsSetupService.registerConverters();
        assertEquals(UnionMatchingMethod.ParametricNoRegion,
                ConvertUtils.convert("", UnionMatchingMethod.class));
        assertEquals(UnionMatchingMethod.ParametricNoRegion,
                ConvertUtils.convert("unrecognised", UnionMatchingMethod.class));
    }

    @Test void databaseVerificationRequiresAllPopulatedTablesWithoutCreatingMissingDatabase() throws Exception {
        Path base = temporary.resolve("input");
        assertThrows(java.io.IOException.class, () -> SimPathsSetupService.verifyDatabase(base));
        assertFalse(Files.exists(temporary.resolve("input.mv.db")));
        try (var connection = java.sql.DriverManager.getConnection("jdbc:h2:file:" + base, "sa", "");
             var statement = connection.createStatement()) {
            statement.execute("CREATE TABLE HOUSEHOLD_UK_2019(ID INT)");
        }
        assertThrows(java.io.IOException.class, () -> SimPathsSetupService.verifyDatabase(base));
        try (var connection = java.sql.DriverManager.getConnection("jdbc:h2:file:" + base, "sa", "");
             var statement = connection.createStatement()) {
            for (String table : java.util.List.of("HOUSEHOLD_UK_2019", "BENEFITUNIT_UK_2019", "PERSON_UK_2019",
                    "DONORPERSON", "DONORTAXUNIT", "DONORPERSONPOLICY", "DONORTAXUNITPOLICY")) {
                statement.execute("CREATE TABLE IF NOT EXISTS " + table + "(ID INT)");
                statement.execute("INSERT INTO " + table + " VALUES(1)");
            }
        }
        SimPathsSetupService.verifyDatabase(base);
    }
}
