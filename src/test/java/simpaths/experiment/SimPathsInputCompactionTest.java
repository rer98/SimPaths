/* (C) Copyright 2026, by Ross Richardson
 * Real H2 checks for lossless input compaction and preparation refusal boundaries.
 * @author ross richardson
 */
package simpaths.experiment;

import java.nio.file.*;
import java.sql.*;
import java.util.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import static org.junit.jupiter.api.Assertions.*;

class SimPathsInputCompactionTest {
    @TempDir Path root;
    Path base() { return root.resolve("input"); }
    Path file() { return root.resolve("input.mv.db"); }
    Connection connect(boolean readOnly) throws Exception {
        return DriverManager.getConnection("jdbc:h2:file:" + base() + (readOnly ? ";IFEXISTS=TRUE;ACCESS_MODE_DATA=r" : ""), "sa", "");
    }
    void fixture() throws Exception {
        try (var connection = connect(false); var s = connection.createStatement()) {
            s.execute("CREATE TABLE SAMPLE(ID INT PRIMARY KEY, V VARCHAR, N NUMERIC(30,9), B VARBINARY)");
            s.execute("INSERT INTO SAMPLE VALUES(1,'£ é <plain text> '' quoted',123.456789123,X'0001ff'),(2,NULL,NULL,NULL)");
            s.execute("CREATE SEQUENCE FUTURE_ID START WITH 73");
            s.execute("CREATE TABLE DISCARDED AS SELECT X, SPACE(1000) V FROM SYSTEM_RANGE(1,10000)");
            s.execute("CHECKPOINT SYNC"); s.execute("DROP TABLE DISCARDED");
        }
        microsim.web.server.DatabaseQueryAccess.provision(base());
    }
    @Test void preservesAllValuesSequencesAndRestrictedReaderWithSmallerFile() throws Exception {
        fixture(); var mode = Files.getPosixFilePermissions(file());
        var result = SimPathsInputCompaction.compact(base());
        assertEquals(true, result.get("logical_contents_preserved"));
        assertTrue((long)result.get("compacted_bytes") < (long)result.get("original_bytes"));
        assertEquals(result.get("database_sha256"), SimPathsInputCompaction.digest(file()));
        assertEquals(mode, Files.getPosixFilePermissions(file()));
        try (var connection = connect(true); var s = connection.createStatement()) {
            try (var rows = s.executeQuery("SELECT V,N,RAWTOHEX(B) FROM SAMPLE WHERE ID=1")) {
                assertTrue(rows.next()); assertEquals("£ é <plain text> ' quoted", rows.getString(1));
                assertEquals("123.456789123", rows.getString(2)); assertEquals("0001ff", rows.getString(3));
            }
            try (var rows = s.executeQuery("SELECT V,N,B FROM SAMPLE WHERE ID=2")) {
                assertTrue(rows.next()); for (int i=1;i<=3;i++) assertNull(rows.getObject(i));
            }
            try (var rows = s.executeQuery("SELECT BASE_VALUE FROM INFORMATION_SCHEMA.SEQUENCES WHERE SEQUENCE_NAME='FUTURE_ID'")) {
                assertTrue(rows.next()); assertEquals(73, rows.getLong(1));
            }
        }
        try (var reader = DriverManager.getConnection("jdbc:h2:file:" + base() + ";IFEXISTS=TRUE", "JASMINE_WEB_READER", "");
             var s = reader.createStatement()) {
            try (var rows = s.executeQuery("SELECT COUNT(*) FROM SAMPLE")) { rows.next(); assertEquals(2,rows.getInt(1)); }
            assertThrows(SQLException.class, () -> s.execute("DELETE FROM SAMPLE"));
        }
        try (var paths = Files.list(root)) { assertFalse(paths.anyMatch(p -> p.getFileName().toString().startsWith(".input-compaction-"))); }
    }
    @Test void insufficientSpacePreservesOriginalAndLeavesNoTemporaryFiles() throws Exception {
        fixture(); String hash = SimPathsInputCompaction.digest(file());
        assertThrows(java.io.IOException.class, () -> SimPathsInputCompaction.compact(base(), 1));
        assertEquals(hash, SimPathsInputCompaction.digest(file()));
        try (var paths = Files.list(root)) { assertEquals(1, paths.count()); }
    }
    @Test void activeEmbeddedDatabaseCannotBeCompactedEvenWithoutAutoServerMarker() throws Exception {
        fixture();
        try (var connection = connect(false)) {
            assertFalse(Files.exists(root.resolve("input.lock.db")));
            assertThrows(Exception.class, () -> SimPathsInputCompaction.compact(base()));
            try (var s = connection.createStatement(); var rows = s.executeQuery("SELECT COUNT(*) FROM SAMPLE")) {
                rows.next(); assertEquals(2, rows.getInt(1));
            }
        }
    }
    @Test void interruptedMaintenanceAndLinksAreRefused() throws Exception {
        fixture(); String hash = SimPathsInputCompaction.digest(file());
        for (String suffix : List.of(".lock.db", ".temp.db", ".mv.db.tempFile", ".mv.db.newFile")) {
            Path marker = Path.of(base() + suffix); Files.writeString(marker,"fixture");
            assertThrows(java.io.IOException.class, () -> SimPathsInputCompaction.compact(base()));
            Files.delete(marker);
        }
        Path original = root.resolve("original.mv.db"); Files.move(file(),original); Files.createSymbolicLink(file(),original);
        assertThrows(java.io.IOException.class, () -> SimPathsInputCompaction.compact(base()));
        Files.delete(file()); Files.createLink(file(),original);
        assertThrows(java.io.IOException.class, () -> SimPathsInputCompaction.compact(base()));
        assertEquals(hash, SimPathsInputCompaction.digest(original));
    }
    @Test void packagedReceiptBindsFinalDatabaseAndAnExistingReceiptCannotBeOverwritten() throws Exception {
        fixture(); Path query = root.resolve("query.json"), receipt = root.resolve("compaction.json");
        microsim.web.server.DatabaseQueryAccess.main(new String[]{base().toString(), query.toString()});
        SimPathsInputCompaction.main(new String[]{base().toString(), receipt.toString(), query.toString()});
        var json = new com.fasterxml.jackson.databind.ObjectMapper();
        var result = json.readTree(receipt.toFile());
        assertEquals(SimPathsInputCompaction.digest(file()), result.get("database_sha256").asText());
        assertEquals(result.get("database_sha256"), json.readTree(query.toFile()).get("database_sha256"));
        String hash = SimPathsInputCompaction.digest(file());
        assertThrows(java.io.IOException.class, () -> SimPathsInputCompaction.main(new String[]{base().toString(),receipt.toString()}));
        assertEquals(hash, SimPathsInputCompaction.digest(file()));
    }
}
