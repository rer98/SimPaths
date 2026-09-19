/* (C) Copyright 2026, by Ross Richardson
 *
 * Sim Paths Model Test.
 *
 * @author ross richardson
 *
 */

package simpaths.model;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.io.File;
import java.nio.file.Path;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class SimPathsModelTest {

    @TempDir
    Path temporaryDirectory;

    @Test
    void runOptionsFileIsDerivedFromExperimentOutputDirectory() {
        Path outputDirectory = temporaryDirectory.resolve("run-a");

        File optionsFile = SimPathsModel.resolveRunOptionsFile(outputDirectory.toString());

        assertEquals(outputDirectory.resolve("input/options.txt").toFile(), optionsFile);
    }
}
