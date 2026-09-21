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
        int populationSize, long seed, boolean includeObserver, boolean training) {
    private static final int MAX_TRAINING_END_YEAR = 2026;
    public SimPathsStartupConfig(Country country, int startYear, int endYear,
            int populationSize, long seed, boolean includeObserver) {
        this(country, startYear, endYear, populationSize, seed, includeObserver, true);
    }
    public static SimPathsStartupConfig userData(int year) {
        return new SimPathsStartupConfig(Country.UK, year, Math.max(year, 2026), 50000, 606L, true, false);
    }
    public SimPathsStartupConfig {
        if (country != Country.UK || endYear < startYear || populationSize <= 0
                || (training && (startYear != 2019 || endYear > MAX_TRAINING_END_YEAR))) {
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
        if (!training) return Map.of(
                "country", ParameterConstraints.Rule.required("UK", "This deployment supports UK"),
                "startYear", ParameterConstraints.Rule.integerRange(startYear, startYear, "Start year is selected during startup"),
                "endYear", new ParameterConstraints.Rule(Integer.toString(startYear), null, null, true, "End year must not precede start year"),
                "popSize", ParameterConstraints.Rule.integerRange(1, 50000, "This deployment supports target populations up to 50000"));
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
