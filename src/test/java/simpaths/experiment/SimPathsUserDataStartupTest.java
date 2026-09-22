/* (C) Copyright 2026, by Ross Richardson
 * User-data review, profile limits and transactional input installation tests.
 * @author ross richardson
 */
package simpaths.experiment;
import java.nio.file.*;
import java.io.*;
import java.util.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import microsim.input.InputEditingPolicy;
import static org.junit.jupiter.api.Assertions.*;
class SimPathsUserDataStartupTest {
    @TempDir Path root;
    Map<String,Object> request() { return Map.of("source","uploads","year","2019","schedule",List.of(List.of("policy_2019.txt","2019","2019","Uploaded scenario"))); }
    @Test void uploadedDataReviewNeverCopiesTrainingOrMutatesActiveFiles() throws Exception {
        Path input = root.resolve("input"); Files.createDirectories(input.resolve("EUROMODoutput/training"));
        Files.writeString(input.resolve("EUROMODpolicySchedule.xlsx"),"original user schedule");
        Files.writeString(input.resolve("EUROMODoutput/training/EUROMODpolicySchedule.xlsx"),"training schedule");
        var startup = new SimPathsUserDataStartup(input, root.resolve("stage"));
        assertThrows(IllegalArgumentException.class, () -> startup.reviewRequest(request()));
        startup.upload("InitialPopulations/population_initial_UK_2019.csv",new ByteArrayInputStream(String.join(",", java.util.stream.Stream.of(simpaths.data.Parameters.PERSON_VARIABLES_INITIAL,
                simpaths.data.Parameters.BENEFIT_UNIT_VARIABLES_INITIAL, simpaths.data.Parameters.HOUSEHOLD_VARIABLES_INITIAL)
                .flatMap(Arrays::stream).distinct().toList()).getBytes()));
        try (var book = new org.apache.poi.xssf.usermodel.XSSFWorkbook()) {
            var sheet = book.createSheet("Names");
            var header = sheet.createRow(0); header.createCell(0).setCellValue("Country"); header.createCell(1).setCellValue("Variable");
            var row = sheet.createRow(1); row.createCell(0).setCellValue(simpaths.model.enums.Country.UK.getCountryName()); row.createCell(1).setCellValue("unit_id");
            try (var out = Files.newOutputStream(input.resolve("system_bu_names.xlsx"))) { book.write(out); }
        }
        var donorColumns = new LinkedHashSet<>(Arrays.asList(simpaths.data.Parameters.DONOR_STATIC_VARIABLES));
        donorColumns.add("unit_id"); donorColumns.addAll(Arrays.asList(simpaths.data.Parameters.DONOR_POLICY_VARIABLES));
        startup.upload("EUROMODoutput/policy_2019.txt",new ByteArrayInputStream((String.join("\t", donorColumns)+"\n").getBytes()));
        var review = startup.reviewRequest(request());
        assertEquals(2019,review.get("year"));
        assertEquals("original user schedule",Files.readString(input.resolve("EUROMODpolicySchedule.xlsx")));
        assertFalse(Files.exists(input.resolve("input.mv.db")));
        assertThrows(IllegalArgumentException.class,startup::validateBuild);
        assertThrows(IllegalArgumentException.class, () -> startup.upload("EUROMODpolicySchedule.xlsx",InputStream.nullInputStream()));
        assertThrows(IllegalArgumentException.class, () -> startup.upload("../input.mv.db",InputStream.nullInputStream()));
        startup.discardUploads();
        assertThrows(IllegalArgumentException.class, () -> startup.reviewRequest(request()));
    }
    @Test void userProfileDoesNotInheritTrainingEndYearCeiling() {
        var user = SimPathsStartupConfig.userData(2019);
        assertDoesNotThrow(() -> user.validateBuildParameters(Map.of("endYear",2040)));
        assertThrows(IllegalArgumentException.class, () -> user.validateBuildParameters(Map.of("endYear",2018)));
        assertThrows(IllegalArgumentException.class, () -> SimPathsStartupConfig.quickStart().validateBuildParameters(Map.of("endYear",2040)));
        assertNotNull(SimPathsUserDataStartup.readOnlyReason("EUROMODpolicySchedule.xlsx",new InputEditingPolicy.State(false,true)));
        assertNull(SimPathsUserDataStartup.readOnlyReason("economic_time_series.xlsx",new InputEditingPolicy.State(false,true)));
    }
    @Test void failedInstallationRestoresOriginalFiles() throws Exception {
        Path input = root.resolve("input"), candidate = root.resolve("candidate"), backup = root.resolve("backup");
        Files.createDirectories(input.resolve("z")); Files.createDirectories(candidate);
        Files.writeString(input.resolve("a"),"original"); Files.writeString(candidate.resolve("a"),"replacement");
        Files.writeString(candidate.resolve("z"),"cannot replace directory");
        assertThrows(IOException.class, () -> SimPathsUserDataPreparation.install(candidate,input,backup));
        assertEquals("original",Files.readString(input.resolve("a")));
        assertTrue(Files.isDirectory(input.resolve("z")));
    }
    @Test void failedWorkerLeavesActiveInputsUntouchedAndRemovesAttempt() throws Exception {
        org.junit.jupiter.api.Assumptions.assumeTrue(Files.getFileStore(root).getUsableSpace() > (3L<<30));
        Path input = root.resolve("input"), stage = root.resolve("stage"); Files.createDirectories(input);
        Path bad = root.resolve("economic_time_series.xlsx"); Files.writeString(bad,"not a workbook");
        Files.writeString(input.resolve("input.mv.db"),"existing database");
        Files.writeString(input.resolve("EUROMODpolicySchedule.xlsx"),"existing schedule");
        var messages = new java.util.concurrent.CopyOnWriteArrayList<String>();
        assertThrows(IOException.class, () -> SimPathsUserDataPreparation.run(input,stage,Map.of("economic_time_series.xlsx",bad),
                List.of(List.of("policy_2019.txt","2019","2019","Example")),2019,messages::add));
        assertTrue(messages.stream().anyMatch(m -> m.contains("Preparation failed while loading parameter workbooks")), messages.toString());
        assertEquals("existing database",Files.readString(input.resolve("input.mv.db")));
        assertEquals("existing schedule",Files.readString(input.resolve("EUROMODpolicySchedule.xlsx")));
        try (var files = Files.list(stage)) { assertEquals(0,files.count()); }
    }
}
