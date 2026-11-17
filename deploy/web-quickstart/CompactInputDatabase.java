/* (C) Copyright 2026, by Ross Richardson
 * Build-time compaction of a closed, prepared input database. Compare complete
 * logical SQL exports, including security metadata, before replacing the copy.
 * This helper is excluded from the runtime image.
 * @author ross richardson
 */
package simpaths.packaging;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.core.type.TypeReference;
import java.io.InputStream;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermission;
import java.security.MessageDigest;
import java.sql.*;
import java.util.*;
import java.util.zip.GZIPInputStream;

public final class CompactInputDatabase {
    private static final ObjectMapper JSON = new ObjectMapper();
    private CompactInputDatabase() { }

    private static Path ordinary(Path path) throws Exception {
        path = path.toAbsolutePath().normalize();
        for (Path p = path; p != null; p = p.getParent()) {
            if (Files.isSymbolicLink(p)) throw new IllegalArgumentException("Linked input path");
        }
        if (!Files.isRegularFile(path) || ((Number) Files.getAttribute(path, "unix:nlink")).intValue() != 1)
            throw new IllegalArgumentException("Expected a regular input file");
        return path;
    }

    private static String digest(InputStream in) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        byte[] bytes = new byte[1024 * 1024];
        for (int n; (n = in.read(bytes)) != -1;) digest.update(bytes, 0, n);
        return HexFormat.of().formatHex(digest.digest());
    }

    private static String digest(Path path) throws Exception {
        try (InputStream in = Files.newInputStream(path)) { return digest(in); }
    }

    private static String logicalDigest(Path database, Path script) throws Exception {
        String file = database.toString();
        if (!file.endsWith(".mv.db") || file.contains(";") || file.contains("\n"))
            throw new IllegalArgumentException("Unsupported database path");
        String url = "jdbc:h2:file:" + file.substring(0, file.length() - 6)
            + ";IFEXISTS=TRUE;ACCESS_MODE_DATA=r";
        // Export to a private temporary file to bound JVM memory. Keep users,
        // grants, settings, sequences, schema and all values in the comparison.
        // Hash the uncompressed SQL so gzip/container timestamps do not matter.
        try (Connection c = DriverManager.getConnection(url, "sa", "");
             Statement s = c.createStatement()) {
            s.setQueryTimeout(120);
            s.execute("SCRIPT SIMPLE TO '" + script.toString().replace("'", "''")
                + "' COMPRESSION GZIP");
        }
        try (InputStream in = new GZIPInputStream(Files.newInputStream(script))) {
            return digest(in);
        } finally { Files.deleteIfExists(script); }
    }

    private static void atomicJson(Path path, Object value) throws Exception {
        Path temporary = Files.createTempFile(path.getParent(), ".compact-receipt-", ".json");
        try {
            JSON.writeValue(temporary.toFile(), value);
            Files.move(temporary, path, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
        } finally { Files.deleteIfExists(temporary); }
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 3) throw new IllegalArgumentException(
            "Expected prepared database base, existing query receipt and new compaction receipt");
        Path database = ordinary(Path.of(args[0] + ".mv.db"));
        Path access = ordinary(Path.of(args[1]));
        Path receipt = Path.of(args[2]).toAbsolutePath().normalize();
        if (Files.exists(receipt, LinkOption.NOFOLLOW_LINKS)
                || !receipt.getParent().equals(access.getParent()))
            throw new IllegalArgumentException("Require a new compaction receipt beside the query receipt");
        for (String suffix : List.of(".lock.db", ".temp.db", ".mv.db.tempFile", ".mv.db.newFile")) {
            if (Files.exists(Path.of(args[0] + suffix), LinkOption.NOFOLLOW_LINKS))
                throw new IllegalArgumentException("Database is active or has unfinished maintenance");
        }
        Map<String, Object> query = JSON.readValue(access.toFile(), new TypeReference<Map<String, Object>>() { });
        String before = digest(database);
        if (!Integer.valueOf(1).equals(query.get("format_version"))
                || !"JASMINE_WEB_READER".equals(query.get("principal"))
                || !"SELECT on approved local base tables".equals(query.get("privileges"))
                || !before.equals(query.get("database_sha256")))
            throw new IllegalArgumentException("Query receipt does not describe this database");
        long originalBytes = Files.size(database);
        if (Files.getFileStore(database).getUsableSpace() < originalBytes + 512L * 1024 * 1024)
            throw new IllegalStateException("Insufficient temporary space for verified compaction");
        Set<PosixFilePermission> mode = Files.getPosixFilePermissions(database);
        Path temporary = Files.createTempDirectory(database.getParent(), ".input-compaction-");
        Path copy = temporary.resolve("input.mv.db");
        try {
            String logicalBefore = logicalDigest(database, temporary.resolve("before.sql.gz"));
            Files.copy(database, copy);
            // Use the H2 version from the very same application JAR as the image.
            Class.forName("org.h2.mvstore.MVStoreTool").getMethod("compact", String.class, boolean.class)
                .invoke(null, copy.toString(), false);
            String logicalAfter = logicalDigest(copy, temporary.resolve("after.sql.gz"));
            if (!logicalBefore.equals(logicalAfter))
                throw new IllegalStateException("Compaction changed logical database contents");
            if (!before.equals(digest(database)))
                throw new IllegalStateException("Source database changed during compaction");
            long compactBytes = Files.size(copy);
            if (compactBytes > originalBytes) throw new IllegalStateException("Compaction increased database size");
            String after = digest(copy);
            Files.setPosixFilePermissions(copy, mode);
            Files.move(copy, database, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
            query.put("database_sha256", after);
            query.put("compaction_source_database_sha256", before);
            atomicJson(access, query);
            atomicJson(receipt, Map.of("format_version", 1, "passed", true,
                "source_database_sha256", before, "database_sha256", after,
                "logical_sql_sha256", logicalAfter, "original_bytes", originalBytes,
                "compacted_bytes", compactBytes, "logical_contents_preserved", true));
            System.out.println("Verified input compaction: " + originalBytes + " -> " + compactBytes + " bytes");
        } finally {
            try (var paths = Files.walk(temporary)) {
                for (Path p : paths.sorted(Comparator.reverseOrder()).toList()) Files.delete(p);
            }
        }
    }
}
