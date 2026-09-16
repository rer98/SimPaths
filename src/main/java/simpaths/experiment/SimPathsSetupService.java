package simpaths.experiment;

import java.io.*;
import java.nio.file.*;
import java.security.*;
import java.util.*;
import org.apache.commons.beanutils.ConvertUtils;
import org.apache.commons.beanutils.Converter;
import org.apache.commons.io.FileUtils;
import simpaths.data.Parameters;
import simpaths.data.XLSXfileWriter;
import simpaths.model.enums.Country;
import simpaths.model.enums.UnionMatchingMethod;

/** Non-visual preparation shared by desktop CLI and the web bootstrap. */
public final class SimPathsSetupService {
    private SimPathsSetupService() {}
    private static final String PROFILE = "uk-2019-training-v1";
    private static Path marker() {
        return Path.of(Parameters.getInputDirectory(), ".simpaths-web-profile.properties");
    }

    /** Only use in a private workspace. Rebuilding an existing database is explicit. */
    public static void prepareQuickStart(SimPathsStartupConfig config, boolean rebuild) throws IOException {
        Parameters.setTrainingFlag(true);
        Parameters.validateStartYear(config.startYear());
        registerConverters();
        Path input = Path.of(Parameters.getInputDirectory());
        Path population = input.resolve("InitialPopulations/training/population_initial_UK_2019.csv");
        Path schedule = input.resolve("EUROMODoutput/training/EUROMODpolicySchedule.xlsx");
        if (!Files.isRegularFile(population) || !Files.isRegularFile(schedule)) {
            throw new IOException("UK/2019 training population and policy template are required");
        }
        if (!rebuild && Files.exists(marker())) {
            requirePreparedInputs();
            Parameters.loadTimeSeriesFactorMaps(config.country());
            Parameters.instantiateAlignmentMaps();
            Parameters.setTaxDonorInputFileName("tax_donor_population_" + config.country());
            System.out.println("Reusing validated UK/2019 training inputs");
            return;
        }
        if (!rebuild && Files.exists(input.resolve("input.mv.db"))) {
            throw new IOException("Existing database has no validated web profile. "
                    + "Use a fresh private workspace or --rebuild-inputs explicitly.");
        }
        // Invalidate before preparation so an interrupted rebuild cannot appear ready.
        Files.deleteIfExists(marker());
        Files.copy(schedule, input.resolve("EUROMODpolicySchedule.xlsx"), StandardCopyOption.REPLACE_EXISTING);
        prepareDesktopInputs(config.country(), config.startYear(), false, false);
        if (!Files.isRegularFile(input.resolve("input.mv.db"))) {
            throw new IOException("Preparation did not create the input database");
        }
        verifyDatabase(input.resolve("input"));
        Properties prepared = new Properties();
        prepared.setProperty("profile", PROFILE);
        prepared.setProperty("artifact", artifactFingerprint());
        prepared.setProperty("inputs", fingerprint(input));
        Path temporary = Files.createTempFile(input, ".simpaths-profile-", ".tmp");
        try {
            try (OutputStream out = Files.newOutputStream(temporary)) {
                prepared.store(out, "Validated SimPaths web profile; regenerate after input changes");
            }
            Files.move(temporary, marker(), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
        } finally {
            Files.deleteIfExists(temporary);
        }
        System.out.println("Prepared UK/2019 training inputs");
    }

    /** Build-time check, never invoked merely to inspect parameter defaults. */
    public static void requirePreparedInputs() {
        try {
            Properties prepared = new Properties();
            try (InputStream in = Files.newInputStream(marker())) { prepared.load(in); }
            if (!PROFILE.equals(prepared.getProperty("profile"))
                    || !artifactFingerprint().equals(prepared.getProperty("artifact"))
                    || !fingerprint(Path.of(Parameters.getInputDirectory())).equals(prepared.getProperty("inputs"))) {
                throw new IOException("Profile mismatch");
            }
        } catch (IOException e) {
            throw new IllegalArgumentException("Prepared inputs are missing or changed. "
                    + "Prepare this private workspace again with --rebuild-inputs before Build.", e);
        }
    }

    private static String artifactFingerprint() throws IOException {
        try {
            Path code = Path.of(SimPathsSetupService.class.getProtectionDomain().getCodeSource().getLocation().toURI());
            return fingerprint(code);
        } catch (java.net.URISyntaxException e) {
            throw new IOException("Cannot identify the model artifact", e);
        }
    }

    static void verifyDatabase(Path databaseBase) throws IOException {
        // The legacy CSV importers can log SQL failures and return; existence is
        // insufficient evidence that their output is ready for model building.
        try (var connection = java.sql.DriverManager.getConnection(
                "jdbc:h2:file:" + databaseBase.toAbsolutePath() + ";IFEXISTS=TRUE;ACCESS_MODE_DATA=r", "sa", "");
             var statement = connection.createStatement()) {
            for (String table : List.of("HOUSEHOLD_UK_2019", "BENEFITUNIT_UK_2019", "PERSON_UK_2019",
                    "DONORPERSON", "DONORTAXUNIT", "DONORPERSONPOLICY", "DONORTAXUNITPOLICY")) {
                try (var rows = statement.executeQuery("SELECT 1 FROM " + table + " LIMIT 1")) {
                    if (!rows.next()) throw new IOException("Prepared table is empty: " + table);
                }
            }
        } catch (java.sql.SQLException e) {
            throw new IOException("Prepared database is incomplete or unavailable", e);
        }
    }

    /** Hash all non-hidden input files, including H2; reject symbolic links. */
    static String fingerprint(Path root) throws IOException {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            List<Path> paths;
            if (Files.isDirectory(root)) {
                try (var stream = Files.walk(root)) { paths = stream.sorted().toList(); }
            } else {
                paths = List.of(root);
            }
            byte[] buffer = new byte[65536];
            for (Path path : paths) {
                if (Files.isSymbolicLink(path)) throw new IOException("Symbolic links are not supported in prepared inputs");
                if (!Files.isRegularFile(path)) continue;
                Path relative = root.equals(path) ? Path.of("artifact") : root.relativize(path);
                boolean hidden = false;
                for (Path part : relative) if (part.toString().startsWith(".")) hidden = true;
                if (hidden) continue;
                digest.update(relative.toString().getBytes(java.nio.charset.StandardCharsets.UTF_8));
                digest.update((byte) 0);
                digest.update(java.nio.ByteBuffer.allocate(Long.BYTES).putLong(Files.size(path)).array());
                try (InputStream in = Files.newInputStream(path)) {
                    int count;
                    while ((count = in.read(buffer)) != -1) digest.update(buffer, 0, count);
                }
                digest.update((byte) 0);
            }
            return HexFormat.of().formatHex(digest.digest());
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }
    public static void registerConverters() {
        ConvertUtils.register(new Converter() {
            @Override
            public <T> T convert(Class<T> type, Object value) {
                if (value == null || value.toString().isBlank()) {
                    return type.cast(UnionMatchingMethod.ParametricNoRegion);
                }
                try {
                    return type.cast(UnionMatchingMethod.valueOf(value.toString()));
                } catch (IllegalArgumentException e) {
                    return type.cast(UnionMatchingMethod.ParametricNoRegion);
                }
            }
        }, UnionMatchingMethod.class);
    }
    public static void prepareDesktopInputs(Country country, int startYear,
            boolean showGui, boolean rewritePolicySchedule) throws FileNotFoundException {

		// Detect if data available; set to training data if not.
		Collection<File> testList = FileUtils.listFiles(new File(Parameters.getInputDirectoryInitialPopulations()), new String[]{"csv"}, false);
		if (testList.size()==0)
			Parameters.setTrainingFlag(true);

		Parameters.validateStartYear(startYear);

		// Create EUROMODPolicySchedule input from files
		if (!rewritePolicySchedule &&
				!new File(Parameters.getInputDirectory() + Parameters.EUROMODpolicyScheduleFilename + ".xlsx").exists()) {
			throw new FileNotFoundException("Policy Schedule file '"+ File.separator + Parameters.getInputDirectory() +
					Parameters.EUROMODpolicyScheduleFilename + ".xlsx` doesn't exist. " +
					"Provide excel file or use `--rewrite-policy-schedule` to re-construct from available policy files.");
		};
		if (rewritePolicySchedule) writePolicySchedule(country);
		//Save the last selected country and year to Excel to use in the model if GUI launched straight away
		String[] columnNames = {"Country", "Year"};
		Object[][] data = new Object[1][columnNames.length];
		data[0][0] = country.toString();
		data[0][1] = startYear;
		XLSXfileWriter.createXLSX(Parameters.INPUT_DIRECTORY, Parameters.DatabaseCountryYearFilename, "Data", columnNames, data);

		// load uprating factors
		Parameters.loadTimeSeriesFactorMaps(country);
		Parameters.instantiateAlignmentMaps();

		// set-up database
		Parameters.databaseSetup(country, showGui, startYear);
    }
    public static void writePolicySchedule(Country country) {

		Collection<File> euromodOutputTextFiles = FileUtils.listFiles(new File(Parameters.getEuromodOutputDirectory()), new String[]{"txt"}, false);
		Iterator<File> fIter = euromodOutputTextFiles.iterator();
		while (fIter.hasNext()) {
			File file = fIter.next();
			if (file.getName().endsWith("_EMHeader.txt")) {
				fIter.remove();
			}
		}

		// create table to allow user specification of policy environment
		String[] columnNames = {
				Parameters.EUROMODpolicyScheduleHeadingFilename,
				Parameters.EUROMODpolicyScheduleHeadingScenarioYearBegins.replace('_', ' '),
				Parameters.EUROMODpolicyScheduleHeadingScenarioSystemYear.replace('_', ' '),
				Parameters.EUROMODpolicySchedulePlanHeadingDescription
		};
		Object[][] data = new Object[euromodOutputTextFiles.size()][columnNames.length];
		int row = 0;
		for (File file: euromodOutputTextFiles) {
			String name = file.getName();
			data[row][0] = name;
			data[row][1] = name.split("_")[1];
			data[row][2] = name.split("_")[1];
			data[row][3] = "";
			row++;
		}

		XLSXfileWriter.createXLSX(Parameters.INPUT_DIRECTORY, Parameters.EUROMODpolicyScheduleFilename, country.toString(), columnNames, data);
    }
}
