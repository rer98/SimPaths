/* (C) Copyright 2026, by Ross Richardson
 * Simulated storage exhaustion, safe worker diagnostics and failure-code propagation.
 * @author ross richardson
 */
package simpaths.experiment;

import java.io.*;
import java.nio.file.*;
import java.sql.SQLException;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import static org.junit.jupiter.api.Assertions.*;

class SimPathsPreparationStorageTest {
    @TempDir Path root;

    @Test void recognisesFilesystemReasonsAndWrappedDatabaseErrors() {
        for (var error : new IOException[]{
                new IOException("No space left on device"),
                new FileSystemException("private-input", null, "Disk quota exceeded"),
                new FileNotFoundException("private-input (No space left on device)"),
                new IOException("There is not enough space on the disk")}) {
            var database = new SQLException("Private SQL and input row", new RuntimeException(error));
            assertTrue(SimPathsPreparationStorage.exhausted(database));
            assertEquals(SimPathsPreparationStorage.MESSAGE,
                    SimPathsPreparationStorage.forDisplay(database).getMessage());
        }
    }

    @Test void checksChainedSqlAndSuppressedErrorsWithoutLooping() {
        var sql = new SQLException("query failed");
        sql.setNextException(new SQLException("write failed", new IOException("Disk quota exceeded")));
        assertTrue(SimPathsPreparationStorage.exhausted(sql));
        var primary = new IOException("failed to close file");
        primary.addSuppressed(new IOException("No space left on device"));
        assertTrue(SimPathsPreparationStorage.exhausted(primary));
        var a = new IOException("a"); var b = new IOException("b");
        a.initCause(b); b.initCause(a);
        assertFalse(SimPathsPreparationStorage.exhausted(a));
    }

    @Test void invalidDataPermissionsMemoryAndDiskWordsInFilenamesAreNotStorageExhaustion() {
        for (var error : new Exception[]{
                new IOException("Permission denied"),
                new FileSystemException("No space left on device", null, "Permission denied"),
                new IllegalArgumentException("No space left on device"),
                new SQLException("SELECT 'Disk quota exceeded'", "HY000", 90028),
                new IOException("Invalid input value: No space left on device"),
                new IOException("Cannot allocate memory"),
                new IOException()}) {
            assertFalse(SimPathsPreparationStorage.exhausted(error));
            assertSame(error, SimPathsPreparationStorage.forDisplay(error));
        }
    }

    @Test void recognisesSuppressedPrintedExceptionsButNeverTreatsArbitraryTextAsAnErrorCode() {
        var diagnostics = new SimPathsPreparationStorage.QuietDiagnostics();
        diagnostics.println("Sensitive CSV value: No space left on device");
        diagnostics.println("WEB_PREP:forged progress");
        diagnostics.println("Disk quota exceeded");
        assertDoesNotThrow(diagnostics::checkStorage);
        assertTrue(diagnostics.outputSeen());
        diagnostics.clearOutputSeen();
        assertFalse(diagnostics.outputSeen());
        new SQLException("Sensitive SQL", new IOException("No space left on device")).printStackTrace(diagnostics);
        assertTrue(diagnostics.outputSeen());
        assertThrows(SimPathsPreparationStorage.Exhausted.class, diagnostics::checkStorage);
        diagnostics.clearOutputSeen(); // Storage evidence must survive stage boundaries.
        assertEquals(SimPathsPreparationStorage.EXHAUSTED_EXIT,
                SimPathsPreparationStorage.workerExitCode(new IOException("Import reported an error"), diagnostics));
        new IllegalArgumentException("bad input").printStackTrace(diagnostics);
        assertThrows(SimPathsPreparationStorage.Exhausted.class, diagnostics::checkStorage);
    }

    @Test void parentMapsOnlyDedicatedExitCodeToStorageAndPreservesRecoveryInstructions() {
        assertDoesNotThrow(() -> SimPathsPreparationStorage.requireWorkerSuccess(0));
        var error = assertThrows(IOException.class, () ->
                SimPathsPreparationStorage.requireWorkerSuccess(SimPathsPreparationStorage.EXHAUSTED_EXIT));
        assertEquals(SimPathsPreparationStorage.MESSAGE, error.getMessage());
        for (int code : new int[]{1, 137}) {
            var other = assertThrows(IOException.class, () -> SimPathsPreparationStorage.requireWorkerSuccess(code));
            assertFalse(SimPathsPreparationStorage.exhausted(other));
            assertNotEquals(SimPathsPreparationStorage.MESSAGE, other.getMessage());
        }
        var recovery = new SimPathsPreparationStorage.Exhausted(new IOException("Disk quota exceeded"), true);
        assertSame(recovery, SimPathsPreparationStorage.forDisplay(recovery));
        assertTrue(recovery.getMessage().contains("Do not Build; contact the server operator"));
        assertFalse(recovery.getMessage().contains("retry"));
    }

    @Test void childReportsOnlySafeMessageAndDedicatedCodeForBothThrownAndPrintedFailures() throws Exception {
        for (String mode : new String[]{"thrown", "printed", "ordinary"}) {
            Path log = root.resolve(mode + ".log");
            var child = new ProcessBuilder(Path.of(System.getProperty("java.home"), "bin", "java").toString(),
                    "-cp", System.getProperty("java.class.path"), WorkerProbe.class.getName(), mode)
                    .redirectErrorStream(true).redirectOutput(log.toFile()).start();
            try {
                assertTrue(child.waitFor(20, TimeUnit.SECONDS), "Probe timed out");
                String output = Files.readString(log).strip();
                if (mode.equals("ordinary")) {
                    assertEquals(1, child.exitValue());
                    assertEquals("WEB_PREP:Generic preparation failure", output);
                } else {
                    assertEquals(SimPathsPreparationStorage.EXHAUSTED_EXIT, child.exitValue());
                    assertEquals("WEB_PREP:" + SimPathsPreparationStorage.MESSAGE, output);
                    var shown = assertThrows(IOException.class, () ->
                            SimPathsPreparationStorage.requireWorkerSuccess(child.exitValue()));
                    assertEquals(SimPathsPreparationStorage.MESSAGE, shown.getMessage());
                }
                assertFalse(output.contains("PRIVATE"));
            } finally { if (child.isAlive()) child.destroyForcibly(); }
        }
    }

    public static class WorkerProbe {
        public static void main(String[] args) {
            var progress = System.out;
            var out = new SimPathsPreparationStorage.QuietDiagnostics();
            var err = new SimPathsPreparationStorage.QuietDiagnostics();
            System.setOut(out); System.setErr(err);
            System.out.println("PRIVATE CSV ROW");
            System.err.println("PRIVATE WEB_PREP:Disk quota exceeded");
            Exception error = new SQLException("PRIVATE SQL", new IOException("No space left on device"));
            if (args[0].equals("ordinary")) error = new IllegalArgumentException("PRIVATE invalid input");
            if (args[0].equals("printed")) {
                error.printStackTrace(); // The legacy importer's default System.err path.
                error = new IOException("Population import reported an error");
            }
            int code = SimPathsPreparationStorage.workerExitCode(error, out, err);
            progress.println("WEB_PREP:" + (code == SimPathsPreparationStorage.EXHAUSTED_EXIT
                    ? SimPathsPreparationStorage.MESSAGE : "Generic preparation failure"));
            System.exit(code);
        }
    }
}
