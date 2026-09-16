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
        return new SimPathsStartupConfig(Country.UK, 2019, 2026, 50000, 606L, true);
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

    private static long integer(Map<String, Object> parameters, String key) {
        try {
            return new BigDecimal(String.valueOf(parameters.get(key))).longValueExact();
        } catch (ArithmeticException | NumberFormatException e) {
            throw new IllegalArgumentException(key + " must be an integer");
        }
    }
}
