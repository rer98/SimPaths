/* (C) Copyright 2026, by Ross Richardson
 * Operator preparation heap bounds and rejection before any workspace writes.
 * @author ross richardson
 */
package simpaths.experiment;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import static org.junit.jupiter.api.Assertions.*;

class SimPathsPreparationHeapTest {
    @TempDir Path root;

    @Test void defaultRemainsThreeGiBAndOverridesUseIntegerMiB() {
        assertEquals("-Xmx3072m", SimPathsUserDataPreparation.preparationHeapOption(null));
        assertEquals("-Xmx3072m", SimPathsUserDataPreparation.preparationHeapOption("3072"));
        assertEquals("-Xmx4096m", SimPathsUserDataPreparation.preparationHeapOption("4096"));
        assertEquals("-Xmx512m", SimPathsUserDataPreparation.preparationHeapOption("512"));
    }

    @Test void rejectsOutOfRangeValuesNonIntegerSyntaxAndArgumentInjection() {
        for (String configured : List.of("", "0", "511", "4097", "9999", "2147483648",
                "-512", "+512", "0512", "3072.0", "3g", " 3072", "3072 ", "3072\n",
                "٣٠٧٢", "3072 -javaagent:private.jar", "3072m;touch /tmp/private")) {
            var error = assertThrows(IllegalArgumentException.class,
                    () -> SimPathsUserDataPreparation.preparationHeapOption(configured));
            assertEquals("SIMPATHS_PREPARATION_HEAP_MIB must be an integer from 512 to 4096",
                    error.getMessage());
        }
    }

    @Test void invalidOperatorEnvironmentStopsBeforeCreatingPreparationFiles() throws Exception {
        Path workspace = root.resolve("workspace"), input = root.resolve("input"), log = root.resolve("probe.log");
        var builder = new ProcessBuilder(Path.of(System.getProperty("java.home"), "bin", "java").toString(),
                "-cp", System.getProperty("java.class.path"), InvalidEnvironmentProbe.class.getName(),
                input.toString(), workspace.toString()).redirectErrorStream(true).redirectOutput(log.toFile());
        builder.environment().put("SIMPATHS_PREPARATION_HEAP_MIB", "4097");
        var child = builder.start();
        try {
            assertTrue(child.waitFor(20, TimeUnit.SECONDS), "Invalid-environment probe timed out");
            assertEquals(0, child.exitValue(), Files.readString(log));
            assertFalse(Files.exists(workspace));
            assertFalse(Files.exists(input));
        } finally {
            if (child.isAlive()) child.destroyForcibly();
        }
    }

    public static class InvalidEnvironmentProbe {
        public static void main(String[] args) throws Exception {
            try {
                SimPathsUserDataPreparation.run(Path.of(args[0]), Path.of(args[1]), Map.of(), List.of(),
                        2019, ignored -> { throw new AssertionError("Preparation should not start"); });
                throw new AssertionError("Invalid worker heap was accepted");
            } catch (IllegalArgumentException expected) {
                if (!expected.getMessage().equals("SIMPATHS_PREPARATION_HEAP_MIB must be an integer from 512 to 4096"))
                    throw expected;
            }
        }
    }
}
