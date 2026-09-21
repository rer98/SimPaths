package simpaths.experiment;

import microsim.parameter.ParameterConstraints;
import java.util.LinkedHashMap;
import java.util.Map;
import simpaths.model.enums.Country;

/* (C) Copyright 2026, by Ross Richardson
 *
 * Explicit scientific defaults for the first web profile, independent of Swing.
 *
 * @author ross richardson
 *
 */
public record SimPathsStartupConfig(Country country, int startYear, int endYear,
        int populationSize, long seed, boolean includeObserver) {
    private static final int MAX_TRAINING_END_YEAR = 2026;
    public SimPathsStartupConfig {
        if (country != Country.UK || startYear != 2019 || endYear < startYear
                || endYear > MAX_TRAINING_END_YEAR || populationSize <= 0) {
            throw new IllegalArgumentException("Unsupported UK/2019 training profile configuration");
        }
    }

    public static SimPathsStartupConfig quickStart() {
        return quickStart(50000);
    }

    public static SimPathsStartupConfig quickStart(int population) {
        if (population != 20000 && population != 50000)
            throw new IllegalArgumentException("Quick Start supports 20000 or 50000 people");
        return new SimPathsStartupConfig(Country.UK, 2019, 2026, population, 606L, true);
    }

    public Map<String, ParameterConstraints.Rule> constraints(boolean prepared) {
        var rules = new LinkedHashMap<String, ParameterConstraints.Rule>();
        rules.put("country", ParameterConstraints.Rule.required(country.toString(), "This training profile requires country=" + country));
        rules.put("startYear", ParameterConstraints.Rule.integerRange(startYear, startYear,
                "This training profile requires startYear=" + startYear));
        rules.put("endYear", ParameterConstraints.Rule.integerRange(startYear, MAX_TRAINING_END_YEAR,
                "endYear must be between " + startYear + " and " + MAX_TRAINING_END_YEAR + " for this profile"));
        rules.put("popSize", ParameterConstraints.Rule.integerRange(prepared ? populationSize : 1,
                prepared ? populationSize : Integer.MAX_VALUE,
                prepared ? "Prepared Quick Start requires popSize=" + populationSize : "popSize must be positive"));
        if (prepared) {
            for (var entry : Map.of("useWeights", false, "ignoreTargetsAtPopulationLoad", false,
                    "PersistPopulation", true).entrySet()) {
                rules.put(entry.getKey(), ParameterConstraints.Rule.required(entry.getValue().toString(),
                        "Prepared Quick Start requires " + entry.getKey() + "=" + entry.getValue()));
            }
        }
        return Map.copyOf(rules);
    }

    public void validateBuildParameters(Map<String, Object> parameters) {
        ParameterConstraints.validate(constraints(false), parameters);
    }

    public void validatePreparedBuildParameters(Map<String, Object> parameters) {
        ParameterConstraints.validate(constraints(true), parameters);
    }
}
