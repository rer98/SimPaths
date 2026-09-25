/* (C) Copyright 2026, by Ross Richardson
 * Read-only compatibility check of trusted Quick Start input databases for MultiRun.
 * Compiled against the selected model JAR and executed in an isolated container.
 * @author ross richardson
 */
package simpaths.experiment;

import java.nio.file.Path;

public final class VerifyPreparedQuickStart {
    public static void main(String[] args) throws Exception {
        var root = Path.of("/inputs");
        var profile = SimPathsQuickStart.readValidatedReceipt(root.resolve("profile.json"));
        var database = root.resolve("input/input");
        SimPathsSetupService.verifyDatabase(database);
        SimPathsQuickStart.verifyProcessed(database, profile.path("actual_counts"), false,
                profile.path("profile").path("requested_population").asInt());
        System.out.println("MULTIRUN_PREPARED_PROFILE_VALIDATED");
    }
}
