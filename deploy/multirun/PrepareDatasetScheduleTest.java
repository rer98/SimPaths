/* (C) Copyright 2026, by Ross Richardson
 * Compare the preparation preflight and public proof schedule with the actual
 * model JAR's policy validation, without starting H2 or importing donor records.
 * @author ross richardson
 */
package simpaths.experiment;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.file.*;
import java.util.*;
import simpaths.data.Parameters;
import simpaths.model.enums.Country;

public final class PrepareDatasetScheduleTest {
    private static void nativeSchedule(Path directory, List<List<String>> rows) throws Exception {
        Parameters.EUROMODpolicyScheduleSystemYearMap.clear();
        SimPathsUserDataStartup.writeSchedule(directory.resolve("EUROMODpolicySchedule.xlsx"), rows);
        Parameters.calculateEUROMODpolicySchedule(Country.UK);
    }

    private static void rejectedByPreflight(List<List<String>> rows) {
        try {
            PrepareDataset.requireBasePriceYear(rows);
            throw new AssertionError("Missing base-price system year was accepted");
        } catch (PrepareDataset.MissingBasePriceYear expected) {
            if (!expected.getMessage().contains("policy system year " + Parameters.BASE_PRICE_YEAR))
                throw new AssertionError("Diagnostic does not identify the required system year");
        }
    }

    @SuppressWarnings("unchecked")
    public static void main(String[] args) throws Exception {
        Path directory = Path.of(args[0]);
        Parameters.setTrainingFlag(false);
        Parameters.setInputDirectory(directory.toAbsolutePath().toString());
        var request = new ObjectMapper().readValue(Path.of(args[1]).toFile(), Map.class);
        List<List<String>> corrected = (List<List<String>>) request.get("schedule");
        var incomplete = corrected.stream()
                .filter(row -> Integer.parseInt(row.get(2)) != Parameters.BASE_PRICE_YEAR).toList();
        if (incomplete.isEmpty() || incomplete.size() == corrected.size())
            throw new AssertionError("Proof fixture must include the base-price policy and simulation policies");
        rejectedByPreflight(incomplete);
        try {
            nativeSchedule(directory, incomplete);
            throw new AssertionError("The installed model accepted the original incomplete schedule");
        } catch (RuntimeException expected) {
            if (!expected.getMessage().contains("base price year")) throw expected;
        }
        rejectedByPreflight(List.of(List.of("scenario.txt", Integer.toString(Parameters.BASE_PRICE_YEAR),
                Integer.toString(Parameters.BASE_PRICE_YEAR + 1), "Start year is not system year")));
        rejectedByPreflight(List.of(List.of("omitted.txt", "", Integer.toString(Parameters.BASE_PRICE_YEAR), "Omitted")));
        // A later policy start year is valid if its system year is the base year.
        PrepareDataset.requireBasePriceYear(List.of(List.of("base.txt", "2019",
                Integer.toString(Parameters.BASE_PRICE_YEAR), "Base-price data")));
        PrepareDataset.requireBasePriceYear(corrected);
        nativeSchedule(directory, corrected);
        System.out.println("PASS: native model and preflight reject missing base-price policy; corrected proof schedule is valid");
    }
}
