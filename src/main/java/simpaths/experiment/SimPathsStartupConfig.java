package simpaths.experiment;

import java.math.BigDecimal;
import java.util.Map;
import simpaths.model.enums.Country;

/** Explicit scientific defaults for the first web profile, independent of Swing. */
public record SimPathsStartupConfig(Country country, int startYear, int endYear,
        int populationSize, long seed, boolean includeObserver) {
    public SimPathsStartupConfig {
        if (country != Country.UK || startYear != 2019 || endYear < startYear
                || endYear > 2026 || populationSize <= 0) {
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

    public void validateBuildParameters(Map<String, Object> parameters) {
        if (parameters.containsKey("startYear") && integer(parameters, "startYear") != startYear) {
            throw new IllegalArgumentException("This prepared profile requires startYear=" + startYear);
        }
        if (parameters.containsKey("endYear")) {
            long year = integer(parameters, "endYear");
            if (year < startYear || year > 2026) {
                throw new IllegalArgumentException("endYear must be between 2019 and 2026 for this profile");
            }
        }
        if (parameters.containsKey("popSize") && integer(parameters, "popSize") <= 0) {
            throw new IllegalArgumentException("popSize must be positive");
        }
    }

    /** Fixed identity only for explicitly selected prepared Quick Start. */
    public void validatePreparedBuildParameters(Map<String, Object> parameters) {
        for (var entry : Map.of("startYear", (long) startYear, "endYear", (long) endYear,
                "popSize", (long) populationSize, "randomSeedIfFixed", seed).entrySet()) {
            if (parameters.containsKey(entry.getKey()) && integer(parameters, entry.getKey()) != entry.getValue())
                throw new IllegalArgumentException("Prepared Quick Start requires " + entry.getKey() + "=" + entry.getValue());
        }
        for (var entry : Map.of("fixRandomSeed", true, "useWeights", false,
                "ignoreTargetsAtPopulationLoad", false, "PersistPopulation", true).entrySet()) {
            if (parameters.containsKey(entry.getKey())
                    && !String.valueOf(parameters.get(entry.getKey())).equalsIgnoreCase(entry.getValue().toString()))
                throw new IllegalArgumentException("Prepared Quick Start requires " + entry.getKey() + "=" + entry.getValue());
        }
        if (parameters.containsKey("country") && !country.toString().equals(String.valueOf(parameters.get("country"))))
            throw new IllegalArgumentException("Prepared Quick Start requires country=" + country);
    }

    private static long integer(Map<String, Object> parameters, String key) {
        try {
            return new BigDecimal(String.valueOf(parameters.get(key))).longValueExact();
        } catch (ArithmeticException | NumberFormatException e) {
            throw new IllegalArgumentException(key + " must be an integer");
        }
    }
}
