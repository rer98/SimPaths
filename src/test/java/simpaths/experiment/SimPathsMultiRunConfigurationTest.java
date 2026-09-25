/* (C) Copyright 2026, by Ross Richardson
 * Check generated configuration values against native MultiRun seed/field assignment.
 * This test does not build a population, execute a simulation or modify model inputs.
 * @author ross richardson
 */
package simpaths.experiment;

import java.lang.reflect.Field;
import java.lang.reflect.Modifier;
import java.util.LinkedHashMap;
import java.util.Map;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.yaml.snakeyaml.LoaderOptions;
import org.yaml.snakeyaml.Yaml;
import org.yaml.snakeyaml.constructor.SafeConstructor;
import simpaths.model.SimPathsModel;
import simpaths.model.enums.Country;
import static org.junit.jupiter.api.Assertions.*;

class SimPathsMultiRunConfigurationTest {
    private final Map<Field, Object> saved = new LinkedHashMap<>();

    @BeforeEach void saveLauncherState() throws Exception {
        for (Field field : SimPathsMultiRun.class.getDeclaredFields()) {
            if (Modifier.isStatic(field.getModifiers()) && !Modifier.isFinal(field.getModifiers())) {
                field.setAccessible(true);
                saved.put(field, field.get(null));
            }
        }
    }

    @AfterEach void restoreLauncherState() throws Exception {
        for (var entry : saved.entrySet()) entry.getKey().set(null, entry.getValue());
    }

    @Test void generatedSettingsKeepStandardSeedsAcrossIndependentRunSets() throws Exception {
        checkAssignments(606L);
    }

    @Test void nativeStartingSeedPreservesValuesBeyondJavascriptIntegerPrecision() throws Exception {
        checkAssignments(9007199254740993L);
    }

    @SuppressWarnings("unchecked")
    private void checkAssignments(long firstSeed) throws Exception {
        Map<String, Object> configuration;
        try (var input = getClass().getResourceAsStream("/multirun/native-configuration.yml")) {
            assertNotNull(input);
            configuration = new Yaml(new SafeConstructor(new LoaderOptions())).load(input);
        }
        var modelArgs = (Map<String, Object>) configuration.get("model_args");
        var collectorArgs = (Map<String, Object>) configuration.get("collector_args");
        assertFalse(modelArgs.containsKey("randomSeedIfFixed"));
        assertEquals(Boolean.TRUE, modelArgs.get("fixRandomSeed"));
        int repetitions = ((Number) configuration.get("maxNumberOfRuns")).intValue();
        var assignLauncher = SimPathsMultiRun.class.getDeclaredMethod("updateLocalParameters", SimPathsModel.class);
        assignLauncher.setAccessible(true);

        // Two separately launched Run Sets must restart the same sequence.
        for (int runSet = 0; runSet < 2; runSet++) {
            for (var entry : configuration.entrySet()) {
                if (!entry.getKey().endsWith("_args")) {
                    SimPathsMultiRun.updateLocalParameters(entry.getKey(), entry.getValue());
                }
            }
            SimPathsMultiRun.updateLocalParameters((Map<String, Object>) configuration.get("innovation_args"));
            SimPathsMultiRun.updateLocalParameters("randomSeed", firstSeed);
            var launcher = new SimPathsMultiRun();
            for (int repetition = 0; repetition < repetitions; repetition++) {
                var model = new SimPathsModel(Country.UK, ((Number) configuration.get("startYear")).intValue());
                // Same assignment order as buildExperiment; no engine/data loading.
                assignLauncher.invoke(launcher, model);
                SimPathsMultiRun.updateParameters(model, modelArgs);
                long expectedSeed = firstSeed + repetition;
                assertEquals(expectedSeed, model.getRandomSeedIfFixed());
                assertEquals(expectedSeed + "_" + repetition, launcher.setupRunLabel());
                assertAssignments(model, modelArgs);
                assertAssignments(model, Map.of("endYear", configuration.get("endYear"),
                                               "popSize", configuration.get("popSize")));
                var collector = new SimPathsCollector(model);
                SimPathsMultiRun.updateParameters(collector, collectorArgs);
                assertAssignments(collector, collectorArgs);
                assertEquals(repetition + 1 < repetitions, launcher.nextModel());
            }
        }
    }

    private static void assertAssignments(Object target, Map<String, Object> expected) throws Exception {
        for (var entry : expected.entrySet()) {
            var field = target.getClass().getDeclaredField(entry.getKey());
            field.setAccessible(true);
            Object actual = field.get(target);
            if (entry.getValue() instanceof Number number && actual instanceof Number value) {
                if (actual instanceof Double || actual instanceof Float) {
                    assertEquals(number.doubleValue(), value.doubleValue(), entry.getKey());
                } else {
                    assertEquals(number.longValue(), value.longValue(), entry.getKey());
                }
            } else {
                assertEquals(entry.getValue(), actual, entry.getKey());
            }
        }
    }
}
