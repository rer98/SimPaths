/* (C) Copyright 2026, by Ross Richardson
 * Recognise preparation storage failures without publishing input data or raw exceptions.
 * @author ross richardson
 */
package simpaths.experiment;

import java.io.IOException;
import java.io.OutputStream;
import java.io.PrintStream;
import java.nio.file.FileSystemException;
import java.sql.SQLException;
import java.util.ArrayDeque;
import java.util.Collections;
import java.util.IdentityHashMap;
import java.util.Locale;
import java.util.Set;

final class SimPathsPreparationStorage {
    static final int EXHAUSTED_EXIT = 73;
    static final String MESSAGE = "Preparation failed because storage space or a disk quota was exhausted. "
            + "Free some space, then review your selections and retry.";
    private static final Set<String> REASONS = Set.of("no space left on device", "disk quota exceeded",
            "not enough space on the disk", "there is not enough space on the disk", "the disk is full");

    private SimPathsPreparationStorage() {}

    static boolean exhausted(Throwable failure) {
        if (failure == null) return false;
        var seen = Collections.newSetFromMap(new IdentityHashMap<Throwable, Boolean>());
        var pending = new ArrayDeque<Throwable>(); pending.add(failure);
        while (!pending.isEmpty()) {
            var current = pending.removeFirst();
            if (!seen.add(current)) continue;
            if (current instanceof Exhausted) return true;
            // Database IO codes alone also cover permission/corruption errors.
            // Require a specific filesystem reason, not words in SQL or user data.
            if (current instanceof FileSystemException fs) {
                if (reasonMatches(fs.getReason(), false)) return true;
            } else if (current instanceof IOException) {
                if (reasonMatches(current.getMessage(), true)) return true;
            }
            if (current.getCause() != null) pending.add(current.getCause());
            Collections.addAll(pending, current.getSuppressed());
            if (current instanceof SQLException sql && sql.getNextException() != null)
                pending.add(sql.getNextException());
        }
        return false;
    }

    private static boolean reasonMatches(String message, boolean allowFileSuffix) {
        if (message == null) return false;
        String text = message.strip().toLowerCase(Locale.ROOT);
        return REASONS.contains(text) || (allowFileSuffix && REASONS.stream()
                .anyMatch(reason -> text.endsWith(" (" + reason + ")")));
    }

    static Exception forDisplay(Exception failure) {
        return failure instanceof Exhausted || !exhausted(failure) ? failure : new Exhausted(failure);
    }

    static void requireWorkerSuccess(int exitCode) throws IOException {
        if (exitCode == EXHAUSTED_EXIT) throw new Exhausted(null);
        if (exitCode != 0) throw new IOException("Input preparation failed. Check the population columns, donor consistency "
                + "and policy schedule, then review and retry. Active inputs were preserved.");
    }

    static int workerExitCode(Throwable failure, QuietDiagnostics... diagnostics) {
        if (exhausted(failure)) return EXHAUSTED_EXIT;
        for (var diagnostic : diagnostics) if (diagnostic.storageExhausted) return EXHAUSTED_EXIT;
        return 1;
    }

    static final class Exhausted extends IOException {
        private static final long serialVersionUID = 1L;
        Exhausted(Throwable cause) { super(MESSAGE, cause); }
        Exhausted(Throwable cause, boolean rollbackFailed) {
            super(rollbackFailed ? "Preparation failed because storage space or a disk quota was exhausted. "
                    + "Input installation could not be rolled back. Do not Build; contact the server operator."
                    : MESSAGE, cause);
        }
    }

    // Used only in the isolated preparation JVM. Throwable.printStackTrace sends
    // the root Throwable to println(Object); inspect its causes before discarding
    // the entire diagnostic. Never parse arbitrary printed CSV/SQL text as errors.
    static final class QuietDiagnostics extends PrintStream {
        private volatile boolean outputSeen, storageExhausted;
        QuietDiagnostics() { super(OutputStream.nullOutputStream()); }
        @Override public void write(int value) { outputSeen = true; }
        @Override public void write(byte[] buffer, int offset, int length) { if (length > 0) outputSeen = true; }
        @Override public void println(Object value) {
            outputSeen = true;
            if (value instanceof Throwable failure && exhausted(failure)) storageExhausted = true;
        }
        boolean outputSeen() { return outputSeen; }
        void clearOutputSeen() { outputSeen = false; }
        void checkStorage() throws IOException { if (storageExhausted) throw new Exhausted(null); }
    }
}
