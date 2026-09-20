/* (C) Copyright 2026, by Ross Richardson
 * Configurable UK training startup using desktop import routines and explicit confirmation.
 * @author ross richardson
 */
package simpaths.experiment;

import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.function.Consumer;
import microsim.web.SimulationServer;
import microsim.web.server.WebStartupProvider;
import org.apache.poi.ss.usermodel.*;
import simpaths.data.Parameters;
import simpaths.data.startingpop.DataParser;
import simpaths.model.enums.Country;
import simpaths.model.taxes.database.TaxDonorDataParser;

public final class SimPathsTrainingStartup implements WebStartupProvider {
    private final Path input;
    private String approvedTraining, approvedTop;

    public SimPathsTrainingStartup() { this(Path.of(Parameters.getInputDirectory())); }
    SimPathsTrainingStartup(Path input) { this.input = input; }

    @Override public Map<String, Object> describe() {
        return Map.of("title", "Configure SimPaths UK training session",
                "description", "UK, 2019. Uses public training data and the supplied fixed policy schedule. "
                        + "UKMOD runs externally. Preparation choices apply before the first Build; "
                        + "new-file uploads and non-training setup are not part of this deployment.",
                "choices", List.of(
                        Map.of("id", "population", "label", "Rebuild starting-population database from supplied training files"),
                        Map.of("id", "tax", "label", "Rebuild tax/benefit database from supplied training files")));
    }

    private Path trainingSchedule() { return input.resolve("EUROMODoutput/training/EUROMODpolicySchedule.xlsx"); }
    private Path topSchedule() { return input.resolve("EUROMODpolicySchedule.xlsx"); }

    private static String digest(Path path) throws Exception {
        if (!Files.exists(path)) return "absent";
        try (var stream = Files.newInputStream(path)) {
            var md = MessageDigest.getInstance("SHA-256");
            byte[] buffer = new byte[65536];
            for (int count; (count = stream.read(buffer)) != -1;) md.update(buffer, 0, count);
            return HexFormat.of().formatHex(md.digest());
        }
    }

    private List<List<String>> scheduleRows() throws Exception {
        try (var stream = Files.newInputStream(trainingSchedule()); var workbook = WorkbookFactory.create(stream)) {
            Sheet sheet = workbook.getSheet("UK");
            if (sheet == null) throw new IllegalArgumentException("Training policy schedule has no UK sheet");
            var rows = new ArrayList<List<String>>();
            var formatter = new DataFormatter(Locale.UK);
            for (Row row : sheet) {
                var values = new ArrayList<String>();
                for (int col = 0; col < row.getLastCellNum(); col++) values.add(formatter.formatCellValue(row.getCell(col)));
                rows.add(values);
            }
            return rows;
        }
    }

    @Override public Map<String, Object> review(Map<String, Boolean> choices) throws Exception {
        if (!Set.of("population", "tax").containsAll(choices.keySet()))
            throw new IllegalArgumentException("Unknown preparation choice");
        if (!Files.isRegularFile(trainingSchedule())) throw new IllegalArgumentException("Missing training policy schedule");
        var warnings = new ArrayList<String>();
        warnings.add("Build replaces input/EUROMODpolicySchedule.xlsx with the supplied training schedule. "
                + "Continue authorises this copy on each Build while those schedule contents remain unchanged.");
        if (choices.getOrDefault("population", false)) warnings.add(
                "Replace starting-population tables and reset the processed-population index in input/input.mv.db "
                        + "from supplied training CSV files (the same operation as desktop startup).");
        if (choices.getOrDefault("tax", false)) warnings.add(
                "Replace input/tax_donor_population_UK.csv and tax/benefit tables in input/input.mv.db. "
                + "Preparation also replaces input/EUROMODpolicySchedule.xlsx with the training schedule.");
        // Review tokens detect changed files without hashing the large input database.
        var stamps = new TreeMap<String, String>();
        List<Path> paths = new ArrayList<>(List.of(input.resolve("input.mv.db"), input.resolve("tax_donor_population_UK.csv")));
        for (String directory : List.of("InitialPopulations/training", "EUROMODoutput/training")) {
            try (var files = Files.list(input.resolve(directory))) { paths.addAll(files.filter(Files::isRegularFile).toList()); }
        }
        for (Path path : paths) stamps.put(input.relativize(path).toString(), Files.exists(path)
                ? Files.size(path) + ":" + Files.getLastModifiedTime(path) : "absent");
        return Map.of("warnings", warnings, "schedule", scheduleRows(), "trainingDigest", digest(trainingSchedule()),
                "topDigest", digest(topSchedule()), "fileVersions", stamps);
    }

    @Override public void prepare(Map<String, Boolean> choices, Consumer<String> progress) throws Exception {
        // Do not call databaseSetup: it deletes the whole database, unlike the Swing choices.
        approvedTraining = null;
        SimPathsSetupService.registerConverters();
        Parameters.setTrainingFlag(true);
        Parameters.setTaxDonorInputFileName("tax_donor_population_UK");
        Parameters.loadTimeSeriesFactorMaps(Country.UK);
        Parameters.instantiateAlignmentMaps();
        if (choices.getOrDefault("population", false)) {
            progress.accept("Importing starting population from supplied training files...");
            DataParser.databaseFromCSV(Country.UK, false);
        }
        if (choices.getOrDefault("tax", false)) {
            progress.accept("Preparing tax/benefit donor data from supplied training files...");
            TaxDonorDataParser.constructAggregateTaxDonorPopulationCSVfile(Country.UK, false);
            TaxDonorDataParser.databaseFromCSV(Country.UK, 2019, false);
            Parameters.loadTimeSeriesFactorForTaxDonor(Country.UK);
            TaxDonorDataParser.populateDonorTaxUnitTables(Country.UK, false);
        }
        progress.accept("Checking prepared database readiness...");
        SimPathsSetupService.verifyDatabase(input.resolve("input"));
        approvedTraining = digest(trainingSchedule());
        approvedTop = digest(topSchedule());
    }

    @Override public void validateBuild() throws Exception {
        if (approvedTraining == null) throw new IllegalArgumentException("Complete startup confirmation before Build.");
        String top = digest(topSchedule());
        if (!approvedTraining.equals(digest(trainingSchedule()))
                || !(top.equals(approvedTop) || top.equals(approvedTraining)))
            throw new IllegalArgumentException("Policy schedule changed since confirmation. Review startup again before the first Build; "
                    + "after Build has started, launch a new session to reconfigure inputs.");
    }

    public static void main(String[] args) {
        if (args.length != 0) throw new IllegalArgumentException("This launcher takes no arguments");
        Parameters.setTrainingFlag(true);
        SimPathsStart.configureWeb(SimPathsStartupConfig.quickStart());
        SimPathsSetupService.registerConverters();
        SimulationServer.setStartupProvider(new SimPathsTrainingStartup());
        SimulationServer.main(new String[0]);
    }
}
