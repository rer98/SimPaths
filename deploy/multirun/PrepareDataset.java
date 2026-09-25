/* (C) Copyright 2026, by Ross Richardson
 * Container-only adapter to the existing UK upload validation and preparation.
 * It starts no web server and never opens an uploaded database.
 * @author ross richardson
 */
package simpaths.experiment;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.file.*;
import java.util.*;
import simpaths.data.Parameters;

public final class PrepareDataset {
    static final class MissingBasePriceYear extends IllegalArgumentException {
        MissingBasePriceYear() {
            super("Include a UKMOD output file with policy system year " + Parameters.BASE_PRICE_YEAR
                    + " (the model's base price year) in the policy schedule, even when the simulation starts later.");
        }
    }

    static void requireBasePriceYear(List<List<String>> schedule) {
        // Rows have already passed the existing startup schedule validation.
        // Match the native model's system-year requirement, not the start year.
        if (schedule.stream().noneMatch(row -> !row.get(1).isBlank()
                && Integer.parseInt(row.get(2)) == Parameters.BASE_PRICE_YEAR)) {
            throw new MissingBasePriceYear();
        }
    }

    @SuppressWarnings("unchecked")
    public static void main(String[] args) {
        String stage = "validating selected files";
        try {
            Path sources = Path.of("/inputs"), input = Path.of("input");
            Files.createDirectory(input);
            try (var files = Files.list(sources.resolve("defaults"))) {
                for (Path file : files.toList()) Files.copy(file, input.resolve(file.getFileName()));
            }
            Map<String,Object> request = new ObjectMapper().readValue(
                    Path.of("/request/selection.json").toFile(), Map.class);
            var startup = new SimPathsUserDataStartup(input, Path.of("startup"));
            try (var files = Files.list(sources.resolve("uploads"))) {
                for (Path file : files.toList()) {
                    String name = file.getFileName().toString();
                    String target = name.endsWith(".csv") ? "InitialPopulations/" + name
                            : name.endsWith(".txt") ? "EUROMODoutput/" + name : name;
                    try (var body = Files.newInputStream(file)) { startup.upload(target, body); }
                }
            }
            var reviewed = startup.reviewRequest(request);
            requireBasePriceYear((List<List<String>>) reviewed.get("schedule"));
            stage = "preparing population and donor tables";
            startup.prepareRequest(request, System.out::println);
            startup.validateBuild();
            System.out.println("MULTIRUN_INPUTS_PREPARED_AND_VALIDATED");
            System.exit(0);
        } catch (Exception failure) {
            // Exceptions from import libraries may contain source records.
            boolean exhausted = SimPathsPreparationStorage.exhausted(failure);
            System.err.println(exhausted ? SimPathsPreparationStorage.MESSAGE
                    : failure instanceof MissingBasePriceYear ? failure.getMessage()
                    : "Preparation failed while " + stage
                      + ". Check population headers, UKMOD columns, workbooks and policy schedule.");
            System.exit(exhausted ? 73 : 1);
        }
    }
}
