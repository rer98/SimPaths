package simpaths.experiment;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.file.*;
import java.sql.*;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import static org.junit.jupiter.api.Assertions.*;

class SimPathsQuickStartTest {
    @TempDir Path directory;
    private final ObjectMapper mapper = new ObjectMapper();

    @Test void onlyPreparedProfileHasFixedIdentity() {
        var c = SimPathsStartupConfig.quickStart();
        c.validateBuildParameters(Map.of("popSize", 2000, "endYear", 2020));
        c.validatePreparedBuildParameters(Map.of("popSize", 50000.0, "randomSeedIfFixed", "606",
                "fixRandomSeed", true, "useWeights", false));
        for (var bad : java.util.List.<Map<String, Object>>of(Map.of("popSize", 2000),
                Map.of("endYear", 2020), Map.of("randomSeedIfFixed", 607),
                Map.of("fixRandomSeed", false), Map.of("useWeights", true),
                Map.of("ignoreTargetsAtPopulationLoad", true), Map.of("country", "IT")))
            assertThrows(IllegalArgumentException.class, () -> c.validatePreparedBuildParameters(bad));
        c.validatePreparedBuildParameters(Map.of("interestRateInnov", 0.01));
    }

    private Path database() throws Exception {
        Path base = directory.resolve("input");
        try (var c = DriverManager.getConnection("jdbc:h2:file:" + base, "sa", "");
             var q = c.createStatement()) {
            q.execute("CREATE TABLE PROCESSED(ID INT, COUNTRY VARCHAR, START_YEAR INT, POP_SIZE INT, NO_TARGETS BOOLEAN)");
            q.execute("INSERT INTO PROCESSED VALUES(1,'UK',2019,50000,FALSE)");
            for (String table : new String[]{"HOUSEHOLD", "BENEFITUNIT", "PERSON"}) {
                q.execute("CREATE TABLE " + table + "(PRID INT)");
                q.execute("INSERT INTO " + table + " VALUES(1)");
            }
        }
        return base;
    }

    @Test void closedVerificationIsReadOnlyAndRequiresMatchingNonemptyCounts() throws Exception {
        Path base = database();
        var counts = mapper.readTree("{\"person\":1,\"household\":1,\"benefitunit\":1}");
        byte[] before = Files.readAllBytes(directory.resolve("input.mv.db"));
        SimPathsQuickStart.verifyProcessed(base, counts, false);
        assertArrayEquals(before, Files.readAllBytes(directory.resolve("input.mv.db")));
        assertThrows(java.io.IOException.class, () -> SimPathsQuickStart.verifyProcessed(base,
                mapper.readTree("{\"person\":0}"), false));
        assertThrows(java.io.IOException.class, () -> SimPathsQuickStart.verifyProcessed(directory.resolve("absent"), counts, false));
        assertFalse(Files.exists(directory.resolve("absent.mv.db")));
    }

    @Test void duplicatesAndMissingProcessedRecordsFail() throws Exception {
        Path base = database();
        var counts = mapper.readTree("{\"person\":1,\"household\":1,\"benefitunit\":1}");
        for (String sql : new String[]{"INSERT INTO PROCESSED VALUES(2,'UK',2019,50000,FALSE)", "DELETE FROM PROCESSED"}) {
            try (var c = DriverManager.getConnection("jdbc:h2:file:" + base, "sa", ""); var q = c.createStatement()) { q.execute(sql); }
            assertThrows(java.io.IOException.class, () -> SimPathsQuickStart.verifyProcessed(base, counts, false));
        }
    }

    @Test void receiptMustDescribeTheFixedTrainingProfile() throws Exception {
        Path receipt = directory.resolve("profile.json");
        Files.writeString(receipt, "");
        assertThrows(java.io.IOException.class, () -> SimPathsQuickStart.readReceipt(receipt));
        Files.writeString(receipt, "{}");
        assertThrows(java.io.IOException.class, () -> SimPathsQuickStart.readReceipt(receipt));
        assertThrows(java.io.IOException.class, () -> SimPathsQuickStart.readReceipt(directory.resolve("missing.json")));
    }
}
