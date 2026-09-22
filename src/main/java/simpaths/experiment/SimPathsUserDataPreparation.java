/* (C) Copyright 2026, by Ross Richardson
 * Isolated preparation worker and reversible installation of validated UK inputs.
 * @author ross richardson
 */
package simpaths.experiment;

import java.io.*;
import java.nio.file.*;
import java.sql.DriverManager;
import java.util.*;
import java.util.concurrent.*;
import java.util.function.Consumer;
import simpaths.data.Parameters;
import simpaths.data.XLSXfileWriter;
import simpaths.data.startingpop.DataParser;
import simpaths.model.enums.Country;
import simpaths.model.taxes.database.TaxDonorDataParser;

public final class SimPathsUserDataPreparation {
    private SimPathsUserDataPreparation() {}
    static void run(Path input, Path workspace, Map<String,Path> sources, List<List<String>> schedule,
            int year, Consumer<String> progress) throws Exception {
        Files.createDirectories(workspace);
        long sourceBytes = 0;
        for (var file : sources.values()) sourceBytes += Files.size(file);
        if (Files.getFileStore(workspace).getUsableSpace() < sourceBytes + (2L<<30))
            throw new IOException("Preparation needs space for candidate inputs plus at least 2 GiB working reserve");
        var storage = microsim.web.server.SessionStorage.fromEnvironment().status();
        if (Boolean.TRUE.equals(storage.get("enabled")) && ((Number)storage.get("remainingBytes")).longValue() < sourceBytes + (2L<<30))
            throw new IOException("Insufficient session allowance for candidate inputs and preparation reserve");
        Path attempt = Files.createTempDirectory(workspace, "prepare-");
        boolean cleanup = true;
        Process process = null;
        Thread shutdown = null;
        try {
            Path candidate = attempt.resolve("input"); Files.createDirectories(candidate);
            for (var entry : sources.entrySet()) {
                Path target = candidate.resolve(entry.getKey()); Files.createDirectories(target.getParent());
                Files.copy(entry.getValue(), target);
            }
            SimPathsUserDataStartup.writeSchedule(candidate.resolve("EUROMODpolicySchedule.xlsx"), schedule);
            String cp = Arrays.stream(System.getProperty("java.class.path").split(java.util.regex.Pattern.quote(File.pathSeparator)))
                    .map(p -> Path.of(p).toAbsolutePath().toString()).collect(java.util.stream.Collectors.joining(File.pathSeparator));
            Files.createDirectories(attempt.resolve("tmp"));
            progress.accept("Starting isolated input preparation; the current input database is unchanged.");
            var worker = new ProcessBuilder(Path.of(System.getProperty("java.home"), "bin", "java").toString(),
                    "-Xmx3g", "-XX:+ExitOnOutOfMemoryError", "-Djava.awt.headless=true", "-Djava.io.tmpdir="+attempt.resolve("tmp"), "-cp", cp,
                    SimPathsUserDataPreparation.class.getName(), Integer.toString(year))
                    .directory(attempt.toFile()).redirectErrorStream(true);
            worker.environment().clear();
            worker.environment().put("LANG", "C.UTF-8");
            worker.environment().put("TMPDIR", attempt.resolve("tmp").toString());
            process = worker.start();
            Process child = process;
            shutdown = new Thread(child::destroyForcibly, "stop-input-preparation");
            Runtime.getRuntime().addShutdownHook(shutdown);
            // Read continuously so the worker cannot block on a full output pipe. Only known progress is published.
            var reader = Thread.ofPlatform().daemon().start(() -> {
                try (var lines = child.inputReader()) {
                    for (String line; (line=SimPathsWebInputBudget.boundedLine(lines))!=null;) {
                        if (line.startsWith("WEB_PREP:")) progress.accept(line.substring(9));
                    }
                } catch (IOException ignored) { }
            });
            if (!child.waitFor(60, TimeUnit.MINUTES)) {
                child.destroyForcibly(); throw new IOException("Preparation exceeded one hour; reduce the input size or check the dataset");
            }
            reader.join(5000);
            if (child.exitValue()!=0) throw new IOException("Input preparation failed. Check the population columns, donor consistency and policy schedule, then review and retry. Active inputs were preserved.");
            SimPathsSetupService.verifyDatabase(candidate.resolve("input"), Country.UK, year);
            // The worker has exited: provision on the closed candidate, before installation
            // and before the startup controller records approved file versions.
            microsim.web.server.DatabaseQueryAccess.provision(candidate.resolve("input"));
            // Install only files from this attempt, with rollback on filesystem errors.
            try { install(candidate, input, attempt.resolve("backup")); }
            catch (IOException e) {
                if (e.getSuppressed().length > 0) {
                    cleanup = false;
                    progress.accept("Input installation rollback failed; preparation files retained for operator recovery. Do not Build.");
                }
                throw e;
            }
            progress.accept("Prepared inputs validated and installed. Configure parameters and Build.");
        } finally {
            if (process != null && process.isAlive()) { process.destroyForcibly(); process.waitFor(10, TimeUnit.SECONDS); }
            if (shutdown != null) Runtime.getRuntime().removeShutdownHook(shutdown);
            if (cleanup) deleteTree(attempt);
        }
    }
    static void install(Path candidate, Path input, Path backup) throws IOException {
        Files.createDirectories(input); Files.createDirectories(backup);
        List<Path> relative;
        try (var paths = Files.walk(candidate)) {
            relative = paths.filter(Files::isRegularFile).map(candidate::relativize)
                    .filter(p -> !p.toString().endsWith(".trace.db") && !p.toString().endsWith(".lock.db")).sorted().toList();
        }
        var installed = new ArrayList<Path>(); var saved = new ArrayList<Path>();
        try {
            for (Path path : relative) {
                Path target = input.resolve(path), old = backup.resolve(path);
                Files.createDirectories(target.getParent());
                if (Files.exists(target)) {
                    if (!Files.isRegularFile(target) || Files.isSymbolicLink(target)) throw new IOException("Cannot replace input " + path);
                    Files.createDirectories(old.getParent()); Files.move(target,old); saved.add(path);
                }
                Files.move(candidate.resolve(path),target); installed.add(path);
            }
        } catch (IOException e) {
            for (Path path : installed.reversed()) {
                try { Files.deleteIfExists(input.resolve(path)); } catch (IOException failure) { e.addSuppressed(failure); }
            }
            for (Path path : saved.reversed()) {
                try { Files.move(backup.resolve(path), input.resolve(path), StandardCopyOption.REPLACE_EXISTING); }
                catch (IOException failure) { e.addSuppressed(failure); }
            }
            throw e;
        }
    }
    static void deleteTree(Path root) throws IOException {
        if (!Files.exists(root)) return;
        try (var paths = Files.walk(root)) {
            for (Path p : paths.sorted(Comparator.reverseOrder()).toList()) Files.delete(p);
        }
    }
    public static void main(String[] args) {
        PrintStream progress = System.out;
        // Importers can print data in diagnostics. Never forward their raw output to the session log.
        System.setOut(new PrintStream(OutputStream.nullOutputStream()));
        System.setErr(new PrintStream(OutputStream.nullOutputStream()));
        String stage = "checking files";
        try {
            int year = Integer.parseInt(args[0]);
            SimPathsWebInputBudget.validateDirectory(Path.of("input"));
            Parameters.setTrainingFlag(false); Parameters.validateStartYear(year);
            Parameters.startYear = year; Parameters.endYear = Math.max(year,2026);
            Parameters.setWorkingDirectory(Path.of(".").toAbsolutePath().normalize().toString());
            SimPathsSetupService.registerConverters();
            Parameters.setTaxDonorInputFileName("tax_donor_population_UK");
            Parameters.setPopulationInitialisationInputFileName("population_initial_UK");
            stage = "loading parameter workbooks"; progress.println("WEB_PREP:Loading parameter workbooks");
            Parameters.loadTimeSeriesFactorMaps(Country.UK); Parameters.instantiateAlignmentMaps();
            stage = "importing the starting population"; progress.println("WEB_PREP:Importing the starting population");
            try (var connection = DriverManager.getConnection("jdbc:h2:file:"+Path.of("input/input").toAbsolutePath(),"sa", "")) {
                // The legacy parser logs SQL exceptions. Detect those in this worker without changing desktop code.
                var errors = new java.util.concurrent.atomic.AtomicBoolean();
                PrintStream previous = System.err;
                System.setErr(new PrintStream(new OutputStream() { public void write(int value) { errors.set(true); } }));
                try { DataParser.createDatabaseForPopulationInitialisationByYearFromCSV(Country.UK,
                        "population_initial_UK", new ArrayList<>(List.of(year)), connection); }
                finally { System.setErr(previous); }
                if (errors.get()) throw new IOException("Population import reported an error");
            }
            stage = "preparing tax/benefit donor data"; progress.println("WEB_PREP:Preparing tax/benefit donor data");
            TaxDonorDataParser.constructAggregateTaxDonorPopulationCSVfile(Country.UK, false);
            TaxDonorDataParser.databaseFromCSV(Country.UK, year, false);
            Parameters.loadTimeSeriesFactorForTaxDonor(Country.UK);
            TaxDonorDataParser.populateDonorTaxUnitTables(Country.UK, false);
            stage = "validating prepared tables"; progress.println("WEB_PREP:Validating prepared tables");
            SimPathsSetupService.verifyDatabase(Path.of("input/input"), Country.UK, year);
            XLSXfileWriter.createXLSX(Parameters.getInputDirectory(), Parameters.DatabaseCountryYearFilename,
                    "Data", new String[]{"Country","Year"}, new Object[][]{{"UK",year}});
            progress.println("WEB_PREP:Preparation complete");
            System.exit(0); // Also closes worker-only persistence resources.
        } catch (Exception e) {
            progress.println("WEB_PREP:Preparation failed while " + stage + ". Check the selected input files.");
            System.exit(1);
        }
    }
}
