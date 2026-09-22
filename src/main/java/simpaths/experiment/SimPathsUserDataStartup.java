/* (C) Copyright 2026, by Ross Richardson
 * Confirmed UK user-data startup, candidate uploads and isolated preparation.
 * @author ross richardson
 */
package simpaths.experiment;

import java.io.*;
import java.nio.file.*;
import java.util.*;
import java.util.function.Consumer;
import org.apache.poi.ss.usermodel.*;
import org.apache.poi.xssf.usermodel.XSSFWorkbook;
import microsim.web.SimulationServer;
import microsim.web.server.*;
import simpaths.data.Parameters;
import simpaths.model.enums.Country;

public final class SimPathsUserDataStartup implements WebStartupProvider {
    private static SimPathsUserDataStartup current;
    private final Path input, workspace, uploads;
    private boolean ready;
    private int preparedYear;
    private Map<String, String> approvedFiles;
    public SimPathsUserDataStartup() { this(Path.of("input"), Path.of(".simpaths-startup")); }
    SimPathsUserDataStartup(Path input, Path workspace) {
        this.input = input.toAbsolutePath().normalize();
        this.workspace = workspace.toAbsolutePath().normalize();
        uploads = this.workspace.resolve("uploads");
    }
    static String readOnlyReason(String path, microsim.input.InputEditingPolicy.State state) {
        if (path.startsWith("InitialPopulations/") || path.startsWith("EUROMODoutput/")
                || Set.of("input.mv.db", "tax_donor_population_UK.csv", "EUROMODpolicySchedule.xlsx",
                        "DatabaseCountryYear.xlsx", "system_bu_names.xlsx").contains(path))
            return state.buildStarted() ? "Dataset configuration is locked for this session. Start a new session to change it."
                    : "Use startup configuration to supply and prepare these inputs.";
        return null;
    }
    public static void requireReady() {
        if (current == null) throw new IllegalArgumentException("Complete user-data startup before Build");
        try { current.validateBuild(); }
        catch (Exception e) { throw new IllegalArgumentException(e.getMessage(), e); }
    }
    @Override public Map<String, Object> describe() {
        return Map.of("title", "Configure SimPaths UK with your data",
                "description", "Upload a starting population and UKMOD output files, or explicitly use the bundled examples. Review the policy schedule before preparing. UKMOD itself runs externally. Dataset choices are locked after the first Build.",
                "choices", List.of(), "fields", List.of(
                        Map.of("id", "source", "label", "Input dataset", "type", "select", "value", "uploads",
                                "options", List.of(Map.of("value", "uploads", "label", "My uploaded files"), Map.of("value", "examples", "label", "Bundled public training examples (2019)"))),
                        Map.of("id", "year", "label", "Start year (must match the population file)", "type", "number", "value", "2019", "min", 2011, "max", 2024)),
                "uploads", List.of(Map.of("id", "population", "label", "Starting-population CSV", "prefix", "InitialPopulations/", "accept", ".csv"),
                        Map.of("id", "donors", "label", "UKMOD output files", "prefix", "EUROMODoutput/", "accept", ".txt"),
                        Map.of("id", "workbooks", "label", "Replacement parameter workbooks", "prefix", "", "accept", ".xlsx,.xls")),
                "candidateFiles", candidateNames(), "table", Map.of(
                        "id", "schedule", "title", "Policy schedule",
                        "description", "Enter uploaded UKMOD filenames. A blank policy start year omits that policy. System year must match the year used in UKMOD. The earliest policy applies if none starts before the simulation.",
                        "columns", List.of(Map.of("label","Filename","type","text"),Map.of("label","Policy start year","type","number"),
                                Map.of("label","Policy system year","type","number"),Map.of("label","Description","type","text")),
                        "addLabel", "Add policy", "exampleLabel", "Use bundled example schedule", "exampleRows", exampleSchedule()));
    }
    private List<String> candidateNames() {
        if (!Files.isDirectory(uploads)) return List.of();
        try (var files = Files.walk(uploads)) {
            return files.filter(Files::isRegularFile).map(p -> uploads.relativize(p).toString()).sorted().toList();
        } catch (IOException e) { throw new UncheckedIOException(e); }
    }
    private List<List<String>> exampleSchedule() {
        try (var stream = Files.newInputStream(input.resolve("EUROMODoutput/training/EUROMODpolicySchedule.xlsx"));
             var book = WorkbookFactory.create(stream)) {
            var rows = new ArrayList<List<String>>(); var format = new DataFormatter(Locale.UK);
            Sheet sheet = book.getSheet("UK");
            for (int i=1; i<=sheet.getLastRowNum(); i++) {
                Row row = sheet.getRow(i); if (row == null) continue;
                var cells = new ArrayList<String>();
                for (int j=0;j<4;j++) cells.add(format.formatCellValue(row.getCell(j)));
                if (!cells.getFirst().isBlank()) rows.add(cells);
            }
            return rows;
        } catch (Exception e) { return List.of(); }
    }
    @Override public void upload(String path, InputStream body) throws Exception {
        boolean allowed = path != null && (path.matches("InitialPopulations/population_initial_UK_[0-9]{4}\\.csv")
                || path.matches("EUROMODoutput/[A-Za-z0-9_-]+\\.txt")
                || (path.matches("[A-Za-z0-9_-]+\\.xlsx?") && Files.isRegularFile(input.resolve(path))
                    && !Set.of("DatabaseCountryYear.xlsx", "EUROMODpolicySchedule.xlsx").contains(path)));
        if (!allowed) throw new IllegalArgumentException("Use population_initial_UK_YEAR.csv, UKMOD .txt files, or an existing parameter workbook. Edit the policy schedule in the startup table.");
        StartupUploads.store(uploads, path, body, 512L<<20, 2L<<30, 1L<<30);
        ready = false;
    }
    @Override public void discardUploads() throws Exception {
        SimPathsUserDataPreparation.deleteTree(uploads); ready = false;
    }
    // Checkbox-only entry points are deliberately unsupported by this provider.
    @Override public Map<String,Object> review(Map<String,Boolean> choices) { throw new IllegalArgumentException("Expected user-data configuration"); }
    @Override public void prepare(Map<String,Boolean> choices, Consumer<String> progress) { throw new IllegalArgumentException("Expected user-data configuration"); }
    private int year(Map<String,Object> request) {
        int year;
        try { year = Integer.parseInt(request.get("year").toString()); }
        catch (Exception e) { throw new IllegalArgumentException("Choose an integer start year"); }
        if (year < 2011 || year > 2024) throw new IllegalArgumentException("SimPaths supports UK start years 2011–2024");
        return year;
    }
    private List<List<String>> schedule(Map<String,Object> request) {
        if (!(request.get("schedule") instanceof List<?> supplied) || supplied.isEmpty() || supplied.size()>100)
            throw new IllegalArgumentException("Add at least one policy (maximum 100)");
        var result = new ArrayList<List<String>>(); var years = new HashSet<Integer>();
        for (Object object : supplied) {
            if (!(object instanceof List<?> cells) || cells.size()!=4) throw new IllegalArgumentException("Expected four policy columns");
            var row = cells.stream().map(v -> Objects.toString(v, "").trim()).toList();
            if (row.get(1).isBlank()) continue; // desktop: blank start year omits the policy
            if (!row.getFirst().matches("[A-Za-z0-9_-]+\\.txt")) throw new IllegalArgumentException("Select a valid UKMOD .txt filename");
            try {
                int start = Integer.parseInt(row.get(1)), system = Integer.parseInt(row.get(2));
                if (start < 1900 || start > 2500 || system < 1900 || system > 2500 || !years.add(start))
                    throw new IllegalArgumentException("Policy start years must be unique; policy years must be between 1900 and 2500");
            } catch (NumberFormatException e) { throw new IllegalArgumentException("Enter integer policy start and system years"); }
            if (row.get(3).length()>1000) throw new IllegalArgumentException("Policy description is too long");
            result.add(row);
        }
        if (result.isEmpty()) throw new IllegalArgumentException("At least one policy needs a start year");
        return List.copyOf(result);
    }
    private Map<String,Path> selectedFiles(Map<String,Object> request) throws IOException {
        int year = year(request); boolean examples = "examples".equals(request.get("source"));
        if (!examples && !"uploads".equals(request.get("source"))) throw new IllegalArgumentException("Choose an input dataset");
        if (examples && year!=2019) throw new IllegalArgumentException("Bundled population examples are for 2019");
        var files = new TreeMap<String,Path>();
        try (var stream = Files.list(input)) {
            stream.filter(p -> p.getFileName().toString().matches("[A-Za-z0-9_-]+\\.xlsx?")).forEach(p -> files.put(p.getFileName().toString(), p));
        }
        // Parameter uploads override defaults in either mode; example selection never replaces uploaded files in place.
        for (String name : candidateNames()) if (!name.contains("/")) files.put(name, uploads.resolve(name));
        String pop = "population_initial_UK_"+year+".csv";
        files.put("InitialPopulations/"+pop, examples ? input.resolve("InitialPopulations/training/"+pop) : uploads.resolve("InitialPopulations/"+pop));
        for (var row : schedule(request)) files.put("EUROMODoutput/"+row.getFirst(), examples
                ? input.resolve("EUROMODoutput/training/"+row.getFirst()) : uploads.resolve("EUROMODoutput/"+row.getFirst()));
        for (var e : files.entrySet()) if (!Files.isRegularFile(e.getValue()) || Files.isSymbolicLink(e.getValue()))
            throw new IllegalArgumentException("Missing input: " + e.getKey());
        validatePopulationHeader(files.get("InitialPopulations/"+pop));
        String benefitUnit = simpaths.model.taxes.database.DonorInputValidation.readBenefitUnitColumn(
                files.get("system_bu_names.xlsx"), Country.UK);
        var policies = new ArrayList<>(schedule(request));
        policies.sort(Comparator.comparingInt(row -> Integer.parseInt(row.get(1))));
        boolean first = true;
        for (var row : policies) {
            simpaths.model.taxes.database.DonorInputValidation.validateDonorHeader(
                    files.get("EUROMODoutput/" + row.getFirst()), benefitUnit, first);
            first = false;
        }
        files.remove("DatabaseCountryYear.xlsx"); files.remove("EUROMODpolicySchedule.xlsx");
        return files;
    }
    private static void validatePopulationHeader(Path path) {
        var required = new TreeSet<String>(String.CASE_INSENSITIVE_ORDER);
        required.addAll(Arrays.asList(Parameters.PERSON_VARIABLES_INITIAL));
        required.addAll(Arrays.asList(Parameters.BENEFIT_UNIT_VARIABLES_INITIAL));
        required.addAll(Arrays.asList(Parameters.HOUSEHOLD_VARIABLES_INITIAL));
        try (var rows = new org.h2.tools.Csv().read(path.toString(), null, null)) {
            var metadata = rows.getMetaData();
            for (int i=1; i<=metadata.getColumnCount(); i++) required.remove(metadata.getColumnName(i));
        } catch (java.sql.SQLException e) { throw new IllegalArgumentException("Cannot read starting-population CSV header"); }
        if (!required.isEmpty()) throw new IllegalArgumentException("Starting-population CSV is missing required columns: " + String.join(", ", required));
    }
    static Map<String,String> versions(Map<String,Path> files) throws IOException {
        var result = new TreeMap<String,String>();
        for (var e : files.entrySet()) result.put(e.getKey(), Files.size(e.getValue())+":"+Files.getLastModifiedTime(e.getValue()));
        return result;
    }
    @Override public Map<String,Object> reviewRequest(Map<String,Object> request) throws Exception {
        if (!Set.of("source","year","schedule").equals(request.keySet())) throw new IllegalArgumentException("Unexpected startup fields");
        var rows = schedule(request); var files = selectedFiles(request);
        return Map.of("warnings", List.of("Prepare the selected UK population and donor systems in a separate Java process. Replace the active input database, generated donor CSV, selected inputs and policy schedule only after successful validation.",
                "Bundled training directories remain examples. Training mode is disabled; Build will not copy their schedule over your selection.",
                "Preparation needs temporary disk space. Missing or invalid inputs leave Build unavailable; correct them and retry."),
                "schedule", rows, "files", versions(files), "year", year(request), "source", request.get("source"));
    }
    @Override public void prepareRequest(Map<String,Object> request, Consumer<String> progress) throws Exception {
        ready = false;
        var files = selectedFiles(request); int year = year(request);
        SimPathsUserDataPreparation.run(input, workspace, files, schedule(request), year, progress);
        preparedYear = year;
        approvedFiles = versions(datasetFiles());
        Parameters.setTrainingFlag(false);
        SimPathsStart.configureWeb(SimPathsStartupConfig.userData(year));
        ready = true;
    }
    private Map<String,Path> datasetFiles() throws IOException {
        var files = new TreeMap<String,Path>();
        for (String name : List.of("input.mv.db", "EUROMODpolicySchedule.xlsx", "system_bu_names.xlsx", "tax_donor_population_UK.csv", "DatabaseCountryYear.xlsx")) {
            Path p = input.resolve(name); if (Files.exists(p)) files.put(name,p);
        }
        for (String dir : List.of("InitialPopulations","EUROMODoutput")) {
            try (var paths = Files.list(input.resolve(dir))) { paths.filter(Files::isRegularFile).forEach(p -> files.put(input.relativize(p).toString(),p)); }
        }
        return files;
    }
    @Override public void validateBuild() throws Exception {
        if (!ready) throw new IllegalArgumentException("Complete user-data preparation before Build");
        if (Parameters.trainingFlag) throw new IllegalArgumentException("User-data sessions must not enter training mode");
        if (!approvedFiles.equals(versions(datasetFiles()))) throw new IllegalArgumentException("Prepared dataset changed. Use startup preparation before the first Build, or start a new session afterwards.");
        SimPathsSetupService.verifyDatabase(input.resolve("input"), Country.UK, preparedYear);
    }
    static void writeSchedule(Path target, List<List<String>> rows) throws IOException {
        try (var book = new XSSFWorkbook()) {
            var sheet = book.createSheet("UK");
            String[] headings = {Parameters.EUROMODpolicyScheduleHeadingFilename, Parameters.EUROMODpolicyScheduleHeadingScenarioYearBegins,
                    Parameters.EUROMODpolicyScheduleHeadingScenarioSystemYear, Parameters.EUROMODpolicySchedulePlanHeadingDescription};
            var header = sheet.createRow(0); for (int j=0;j<4;j++) header.createCell(j).setCellValue(headings[j]);
            for (int i=0;i<rows.size();i++) { var row = sheet.createRow(i+1); for (int j=0;j<4;j++) row.createCell(j).setCellValue(rows.get(i).get(j)); }
            try (var out = Files.newOutputStream(target)) { book.write(out); }
        }
    }
    public static void main(String[] args) {
        Parameters.setTrainingFlag(false);
        SimPathsStart.configureWeb(SimPathsStartupConfig.userData(2019));
        SimPathsSetupService.registerConverters();
        current = new SimPathsUserDataStartup();
        SimulationServer.setStartupProvider(current);
        SimulationServer.main(new String[0]);
    }
}
