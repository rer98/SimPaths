/* (C) Copyright 2026, by Ross Richardson
 * Harmless donor-import injection regressions and valid-data compatibility checks.
 * @author ross richardson
 */
package simpaths.model.taxes.database;

import java.lang.reflect.InvocationTargetException;
import java.nio.file.*;
import java.sql.*;
import java.util.*;
import org.apache.poi.xssf.usermodel.XSSFWorkbook;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.io.TempDir;
import simpaths.data.Parameters;
import simpaths.model.enums.Country;
import static org.junit.jupiter.api.Assertions.*;

class DonorInputValidationTest {
    @TempDir Path root;
    static final String INJECTION = "0 AS EXTRA FROM tax_donor_population_UK); CREATE TABLE REVIEW_INJECTED(ID INT);--";

    final Map<java.lang.reflect.Field,Object> previous = new LinkedHashMap<>();
    @BeforeEach void isolateStaticSetupState() throws Exception {
        for (String name : List.of("WORKING_DIRECTORY", "INPUT_DIRECTORY", "INPUT_DIRECTORY_INITIAL_POPULATIONS",
                "EUROMOD_OUTPUT_DIRECTORY", "EUROMOD_TRAINING_DIRECTORY", "trainingFlag", "taxDonorInputFileName",
                "benefitUnitVariableNames", "countryRegions", "EUROMODpolicyScheduleSystemYearMap")) {
            var field = Parameters.class.getDeclaredField(name); field.setAccessible(true);
            previous.put(field, field.get(null));
        }
        Parameters.EUROMODpolicyScheduleSystemYearMap = new TreeMap<>();
    }
    @AfterEach void restoreStaticSetupState() throws Exception {
        for (var entry : previous.entrySet()) entry.getKey().set(null, entry.getValue());
    }

    void fixture(String column, String policy) throws Exception {
        Parameters.setWorkingDirectory(root.toString());
        Parameters.setTrainingFlag(false);
        Parameters.setTaxDonorInputFileName("tax_donor_population_UK");
        Files.createDirectories(root.resolve("input/EUROMODoutput"));
        try (var book = new XSSFWorkbook()) {
            var sheet = book.createSheet("Names");
            var h = sheet.createRow(0); h.createCell(0).setCellValue("Country"); h.createCell(1).setCellValue("Variable");
            var row = sheet.createRow(1); row.createCell(0).setCellValue(Country.UK.getCountryName()); row.createCell(1).setCellValue(column);
            try (var out = Files.newOutputStream(root.resolve("input/system_bu_names.xlsx"))) { book.write(out); }
        }
        var schedule = simpaths.experiment.SimPathsUserDataStartup.class.getDeclaredMethod("writeSchedule", Path.class, List.class);
        schedule.setAccessible(true);
        schedule.invoke(null, root.resolve("input/EUROMODpolicySchedule.xlsx"),
                List.of(List.of(policy + ".txt", "2019", "2015", "Synthetic fixture")));
        var headers = new LinkedHashSet<>(Arrays.asList(Parameters.DONOR_STATIC_VARIABLES));
        headers.add(column); headers.addAll(Arrays.asList(Parameters.DONOR_POLICY_VARIABLES));
        Files.writeString(root.resolve("input/EUROMODoutput/" + policy + ".txt"), String.join("\t", headers) + "\n"
                + String.join("\t", Collections.nCopies(headers.size(), "1")) + "\n");
    }

    void importTables(Connection connection) throws Exception {
        var method = TaxDonorDataParser.class.getDeclaredMethod("createTaxDonorTables", Connection.class, Country.class, int.class);
        method.setAccessible(true);
        try { method.invoke(null, connection, Country.UK, 2019); }
        catch (InvocationTargetException e) { throw (Exception)e.getCause(); }
    }

    @Test void workbookInjectionRejectedBeforeCsvReplacementAndSchemaChanges() throws Exception {
        fixture(INJECTION, "probe");
        Path csv = root.resolve("input/tax_donor_population_UK.csv");
        Files.writeString(csv, "original");
        assertThrows(IllegalArgumentException.class, () -> TaxDonorDataParser.constructAggregateTaxDonorPopulationCSVfile(Country.UK, false));
        assertEquals("original", Files.readString(csv));
        Parameters.setCountryBenefitUnitName();
        try (var c = DriverManager.getConnection("jdbc:h2:mem:injection", "sa", "")) {
            c.createStatement().execute("CREATE TABLE TAX_DONOR_POPULATION_UK (ID INT)");
            c.createStatement().execute("INSERT INTO TAX_DONOR_POPULATION_UK VALUES (7)");
            assertThrows(IllegalArgumentException.class, () -> importTables(c));
            try (var r = c.createStatement().executeQuery("SELECT ID FROM TAX_DONOR_POPULATION_UK")) { assertTrue(r.next()); assertEquals(7, r.getInt(1)); }
            try (var r = c.createStatement().executeQuery("SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME='REVIEW_INJECTED'")) { r.next(); assertEquals(0, r.getInt(1)); }
        }
    }

    @Test void validAlternativeColumnAndHyphenatedPolicyImportWithoutChangingValues() throws Exception {
        root = Files.createDirectory(root.resolve("quote'd-path"));
        fixture("alternative_unit", "policy-2015");
        TaxDonorDataParser.constructAggregateTaxDonorPopulationCSVfile(Country.UK, false);
        try (var c = DriverManager.getConnection("jdbc:h2:mem:valid-donor", "sa", "")) {
            importTables(c);
            try (var r = c.createStatement().executeQuery("SELECT ID, TUID FROM DONORPERSON_UK")) {
                assertTrue(r.next()); assertEquals(1, r.getLong(1)); assertEquals(1, r.getLong(2)); assertFalse(r.next());
            }
            try (var r = c.createStatement().executeQuery("SELECT PID, FROM_YEAR, SYSTEM_YEAR FROM DONORPERSONPOLICY_UK")) {
                assertTrue(r.next()); assertEquals(1, r.getLong(1)); assertEquals(2019, r.getInt(2)); assertEquals(2015, r.getInt(3));
            }
        }
    }

    @Test void missingOrAmbiguousColumnsFailBeforeImport() throws Exception {
        Path file = root.resolve("donor.txt");
        Files.writeString(file, "id\tID\n");
        assertThrows(IllegalArgumentException.class, () -> DonorInputValidation.validateDonorHeader(file, "unit", true));
        Files.writeString(file, "id\tunit\n");
        assertThrows(IllegalArgumentException.class, () -> DonorInputValidation.validateDonorHeader(file, "unit", true));
        assertEquals("unit_1", DonorInputValidation.benefitUnitColumn("unit_1"));
        assertThrows(IllegalArgumentException.class, () -> DonorInputValidation.benefitUnitColumn("unit;DROP TABLE X"));
    }

    @Test void csvEscapingRoundTripsTextWithoutColumnShifts() throws Exception {
        Path csv = root.resolve("roundtrip.csv");
        String value = "text, with \"quotes\"\nand a line break";
        Files.writeString(csv, "A,B\n" + DonorInputValidation.csvField(value) + ",2\n");
        try (var rows = new org.h2.tools.Csv().read(csv.toString(), null, "UTF-8")) {
            assertTrue(rows.next()); assertEquals(value, rows.getString(1)); assertEquals("2", rows.getString(2));
            assertFalse(rows.next());
        }
    }

    @Test void bundledAndUploadedTrainingSamplesProduceIdenticalDonorTables() throws Exception {
        Path supplied = Path.of("input").toAbsolutePath();
        List<List<List<String>>> imported = new ArrayList<>();
        byte[] originalCsv = null;
        for (boolean training : new boolean[]{true, false}) {
            Path work = Files.createDirectory(root.resolve(training ? "bundled" : "uploaded"));
            Path input = Files.createDirectories(work.resolve("input"));
            Path donors = Files.createDirectories(input.resolve(training ? "EUROMODoutput/training" : "EUROMODoutput"));
            Files.copy(supplied.resolve("system_bu_names.xlsx"), input.resolve("system_bu_names.xlsx"));
            Files.copy(supplied.resolve("EUROMODoutput/training/EUROMODpolicySchedule.xlsx"),
                    (training ? donors : input).resolve("EUROMODpolicySchedule.xlsx"));
            try (var files = Files.list(supplied.resolve("EUROMODoutput/training"))) {
                for (Path file : files.filter(p -> p.toString().endsWith(".txt")).toList()) {
                    try (var lines = Files.lines(file)) {
                        Files.write(donors.resolve(file.getFileName()), lines.limit(3).toList());
                    }
                }
            }
            Parameters.setWorkingDirectory(work.toString()); Parameters.setTrainingFlag(training);
            Parameters.setTaxDonorInputFileName("tax_donor_population_UK");
            Parameters.EUROMODpolicyScheduleSystemYearMap = new TreeMap<>();
            TaxDonorDataParser.constructAggregateTaxDonorPopulationCSVfile(Country.UK, false);
            byte[] csv = Files.readAllBytes(input.resolve("tax_donor_population_UK.csv"));
            if (originalCsv == null) originalCsv = csv; else assertArrayEquals(originalCsv, csv);
            List<List<String>> records = new ArrayList<>();
            try (var connection = DriverManager.getConnection("jdbc:h2:mem:sample-" + training, "sa", "")) {
                importTables(connection);
                for (String table : List.of("DONORPERSON_UK", "DONORPERSONPOLICY_UK", "DONORTAXUNIT_UK", "DONORTAXUNITPOLICY_UK")) {
                    try (var rows = connection.createStatement().executeQuery("SELECT * FROM " + table + " ORDER BY 1")) {
                        while (rows.next()) {
                            List<String> row = new ArrayList<>(); row.add(table);
                            for (int i=1; i<=rows.getMetaData().getColumnCount(); i++) row.add(rows.getString(i));
                            records.add(row);
                        }
                    }
                }
            }
            imported.add(records);
        }
        assertFalse(imported.get(0).isEmpty());
        assertEquals(imported.get(0), imported.get(1));
    }
}
