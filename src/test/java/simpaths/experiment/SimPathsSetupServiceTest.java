package simpaths.experiment;

import java.nio.file.*;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.apache.commons.beanutils.ConvertUtils;
import simpaths.data.Parameters;
import simpaths.model.enums.UnionMatchingMethod;
import static org.junit.jupiter.api.Assertions.*;

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

    @Test void fingerprintDetectsChangedContentAndPathsButIgnoresMarker() throws Exception {
        Path file = temporary.resolve("policy.xlsx");
        Files.writeString(file, "first");
        String first = SimPathsSetupService.fingerprint(temporary);
        Files.writeString(temporary.resolve(".simpaths-web-profile.properties"), "marker");
        assertEquals(first, SimPathsSetupService.fingerprint(temporary));
        Files.writeString(file, "other");
        assertNotEquals(first, SimPathsSetupService.fingerprint(temporary));
        Files.writeString(file, "first");
        Files.move(file, temporary.resolve("renamed.xlsx"));
        assertNotEquals(first, SimPathsSetupService.fingerprint(temporary));
    }

    @Test void unvalidatedDatabaseIsNeverSilentlyRebuilt() throws Exception {
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
