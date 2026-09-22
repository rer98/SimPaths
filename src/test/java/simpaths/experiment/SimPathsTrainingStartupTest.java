/* (C) Copyright 2026, by Ross Richardson
 * Training startup review is read-only and imports preserve independent Swing choices.
 * @author ross richardson
 */
package simpaths.experiment;

import java.nio.file.*;
import java.util.Map;
import org.apache.poi.xssf.usermodel.XSSFWorkbook;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import simpaths.data.Parameters;
import simpaths.data.startingpop.DataParser;
import simpaths.model.enums.Country;
import simpaths.model.taxes.database.TaxDonorDataParser;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class SimPathsTrainingStartupTest {
    @TempDir Path input;
    void fixtures() throws Exception {
        Files.createDirectories(input.resolve("InitialPopulations/training"));
        Files.createDirectories(input.resolve("EUROMODoutput/training"));
        try (var workbook = new XSSFWorkbook()) {
            workbook.createSheet("UK").createRow(0).createCell(0).setCellValue("Filename");
            try (var out = Files.newOutputStream(input.resolve("EUROMODoutput/training/EUROMODpolicySchedule.xlsx"))) { workbook.write(out); }
        }
        Files.writeString(input.resolve("EUROMODpolicySchedule.xlsx"), "existing user schedule");
    }
    @Test void reviewDoesNotOverwriteAndRejectsUnknownChoices() throws Exception {
        fixtures(); var startup = new SimPathsTrainingStartup(input);
        var result = startup.review(Map.of("tax", true));
        assertTrue(result.containsKey("schedule"));
        assertEquals("existing user schedule", Files.readString(input.resolve("EUROMODpolicySchedule.xlsx")));
        assertFalse(Files.exists(input.resolve("input.mv.db")));
        assertThrows(IllegalArgumentException.class, () -> startup.review(Map.of("unknown", true)));
        assertThrows(IllegalArgumentException.class, startup::validateBuild);
    }
    @Test void selectedImportsAreIndependentAndChangedScheduleNeedsConfirmation() throws Exception {
        fixtures();
        try (var connection = java.sql.DriverManager.getConnection("jdbc:h2:file:" + input.resolve("input"), "sa", "");
             var statement = connection.createStatement()) { statement.execute("CREATE TABLE FIXTURE(ID INT)"); }
        for (String choice : new String[]{"none", "population", "tax"}) {
            try (var parameters = mockStatic(Parameters.class);
                 var population = mockStatic(DataParser.class);
                 var tax = mockStatic(TaxDonorDataParser.class);
                 var setup = mockStatic(SimPathsSetupService.class)) {
                var startup = new SimPathsTrainingStartup(input);
                startup.prepare(choice.equals("none") ? Map.of() : Map.of(choice, true), x -> {});
                population.verify(() -> DataParser.databaseFromCSV(Country.UK, false), times(choice.equals("population") ? 1 : 0));
                tax.verify(() -> TaxDonorDataParser.constructAggregateTaxDonorPopulationCSVfile(Country.UK, false), times(choice.equals("tax") ? 1 : 0));
                tax.verify(() -> TaxDonorDataParser.databaseFromCSV(Country.UK, 2019, false), times(choice.equals("tax") ? 1 : 0));
                tax.verify(() -> TaxDonorDataParser.populateDonorTaxUnitTables(Country.UK, false), times(choice.equals("tax") ? 1 : 0));
                parameters.verify(() -> Parameters.databaseSetup(Country.UK, false, 2019), never());
                startup.validateBuild();
                Files.writeString(input.resolve("EUROMODpolicySchedule.xlsx"), "new user edit " + choice);
                assertThrows(IllegalArgumentException.class, startup::validateBuild);
            }
        }
    }
}
