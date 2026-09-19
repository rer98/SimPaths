package simpaths.experiment;

import java.io.*;
import java.nio.file.*;
import java.util.*;
import org.apache.commons.beanutils.ConvertUtils;
import org.apache.commons.beanutils.Converter;
import org.apache.commons.io.FileUtils;
import simpaths.data.Parameters;
import simpaths.data.XLSXfileWriter;
import simpaths.model.enums.Country;
import simpaths.model.enums.UnionMatchingMethod;

/* (C) Copyright 2026, by Ross Richardson
 *
 * Non-visual preparation shared by desktop CLI and the web bootstrap.
 *
 * @author ross richardson
 *
 */
public final class SimPathsSetupService {
    private SimPathsSetupService() {}
    /** Only use in a private workspace. Rebuilding an existing database is explicit. */
    public static void prepareQuickStart(SimPathsStartupConfig config, boolean rebuild) throws IOException {
        Parameters.setTrainingFlag(true);
        Parameters.validateStartYear(config.startYear());
        registerConverters();
        Path input = Path.of(Parameters.getInputDirectory());
        // Only a definitely absent database permits automatic preparation.
        if (!rebuild && !Files.notExists(input.resolve("input.mv.db"))) {
            verifyDatabase(input.resolve("input"));
            Parameters.loadTimeSeriesFactorMaps(config.country());
            Parameters.instantiateAlignmentMaps();
            Parameters.setTaxDonorInputFileName("tax_donor_population_" + config.country());
            System.out.println("Reusing existing UK/2019 input database after basic readiness checks");
            return;
        }
        Path population = input.resolve("InitialPopulations/training/population_initial_UK_2019.csv");
        Path schedule = input.resolve("EUROMODoutput/training/EUROMODpolicySchedule.xlsx");
        if (!Files.isRegularFile(population) || !Files.isRegularFile(schedule)) {
            throw new IOException("UK/2019 training population and policy template are required for preparation");
        }
        Files.copy(schedule, input.resolve("EUROMODpolicySchedule.xlsx"), StandardCopyOption.REPLACE_EXISTING);
        prepareDesktopInputs(config.country(), config.startYear(), false, false);
        verifyDatabase(input.resolve("input"));
        System.out.println("Prepared UK/2019 training inputs");
    }

    /** Read-only Build check; never regenerates inputs or scans/hashes the input tree. */
    public static void requirePreparedInputs() {
        try {
            verifyDatabase(Path.of(Parameters.getInputDirectory(), "input"));
        } catch (IOException e) {
            throw new IllegalArgumentException("Input database readiness check failed: " + e.getMessage()
                    + ". Resolve access problems or explicitly prepare the private workspace "
                    + "with --rebuild-inputs if regeneration is intended. No automatic rebuild was attempted.", e);
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
            throw new IOException("Prepared database is incomplete or unavailable: " + e.getMessage(), e);
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
