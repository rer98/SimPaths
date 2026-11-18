/* (C) Copyright 2026, by Ross Richardson
 * Compact closed, unpublished H2 inputs with complete logical-content validation.
 * Invoked only during input preparation, never by Build or simulation events.
 * @author ross richardson
 */
package simpaths.experiment;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.*;
import java.nio.channels.FileChannel;
import java.nio.file.*;
import java.security.MessageDigest;
import java.sql.*;
import java.util.*;
import java.util.zip.GZIPInputStream;

public final class SimPathsInputCompaction {
    private SimPathsInputCompaction() { }

    private static Path ordinary(Path path) throws IOException {
        path = path.toAbsolutePath().normalize();
        for (Path p = path; p != null; p = p.getParent())
            if (Files.isSymbolicLink(p)) throw new IOException("Linked input path");
        if (!Files.isRegularFile(path) || ((Number) Files.getAttribute(path, "unix:nlink")).intValue() != 1)
            throw new IOException("Expected an ordinary, unshared input file");
        String name = path.toString();
        if (name.contains(";") || name.contains("\n") || name.contains("\r"))
            throw new IOException("Unsupported database path");
        return path;
    }

    private static String digest(InputStream stream) throws Exception {
        var hash = MessageDigest.getInstance("SHA-256");
        byte[] buffer = new byte[1024 * 1024];
        for (int n; (n = stream.read(buffer)) != -1;) hash.update(buffer, 0, n);
        return HexFormat.of().formatHex(hash.digest());
    }

    static String digest(Path file) throws Exception {
        try (var stream = Files.newInputStream(file)) { return digest(stream); }
    }

    private static String logicalDigest(Path database, Path script) throws Exception {
        String name = database.toString();
        String url = "jdbc:h2:file:" + name.substring(0, name.length() - 6) + ";IFEXISTS=TRUE;ACCESS_MODE_DATA=r";
        // Private on-disk export bounds heap use. Compare values, schema, sequences,
        // settings, users and grants; never print or retain the exported microdata.
        try (var connection = DriverManager.getConnection(url, "sa", "");
             var statement = connection.createStatement()) {
            statement.setQueryTimeout(300);
            statement.execute("SCRIPT SIMPLE TO '" + script.toString().replace("'", "''") + "' COMPRESSION GZIP");
        }
        try (var stream = new GZIPInputStream(Files.newInputStream(script))) { return digest(stream); }
        finally { Files.deleteIfExists(script); }
    }

    /** Only closed trusted candidates: callers exclude writers and publish afterwards. */
    public static Map<String, Object> compact(Path base) throws Exception {
        Path database = ordinary(Path.of(base + ".mv.db"));
        long available = Files.getFileStore(database).getUsableSpace();
        var storage = microsim.web.server.SessionStorage.fromEnvironment().status();
        if (Boolean.TRUE.equals(storage.get("enabled")))
            available = Math.min(available, ((Number) storage.get("remainingBytes")).longValue());
        return compact(base, available);
    }

    static Map<String, Object> compact(Path base, long available) throws Exception {
        Path database = ordinary(Path.of(base + ".mv.db"));
        for (String suffix : List.of(".lock.db", ".temp.db", ".mv.db.tempFile", ".mv.db.newFile"))
            if (Files.exists(Path.of(base + suffix), LinkOption.NOFOLLOW_LINKS))
                throw new IOException("Input database is active or has unfinished maintenance");
        // Detect embedded writers too, which need not have an AUTO_SERVER marker.
        try (var channel = FileChannel.open(database, StandardOpenOption.READ);
             var lock = channel.tryLock(0, Long.MAX_VALUE, true)) {
            if (lock == null) throw new IOException("Input database is still open");
        }
        long originalBytes = Files.size(database);
        // The offline compactor can hold both its input copy and replacement;
        // retain space for the compressed logical verification export as well.
        long required = Math.addExact(Math.multiplyExact(originalBytes, 2), 512L << 20);
        if (available < required)
            throw new IOException("Insufficient storage for input compaction; free space before preparing inputs again");
        String before = digest(database);
        var permissions = Files.getPosixFilePermissions(database);
        Path temporary = Files.createTempDirectory(database.getParent(), ".input-compaction-");
        try {
            String logical = logicalDigest(database, temporary.resolve("before.sql.gz"));
            Path copy = temporary.resolve("input.mv.db");
            Files.copy(database, copy);
            // Resolve H2 from this model's classpath, never a separately chosen version.
            Class.forName("org.h2.mvstore.MVStoreTool").getMethod("compact", String.class, boolean.class)
                    .invoke(null, copy.toString(), false);
            if (!logical.equals(logicalDigest(copy, temporary.resolve("after.sql.gz"))))
                throw new IOException("Compaction changed logical database contents");
            if (!before.equals(digest(database))) throw new IOException("Input database changed during compaction");
            long bytes = Files.size(copy);
            // A tiny, already compact database can grow by an H2 page; retain the
            // verified original in that case. Never make an input larger.
            boolean replace = bytes < originalBytes;
            String after = replace ? digest(copy) : before;
            if (replace) {
                try (var channel = FileChannel.open(copy, StandardOpenOption.WRITE)) { channel.force(true); }
                Files.setPosixFilePermissions(copy, permissions);
                Files.move(copy, database, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
            }
            return Map.of("format_version", 1, "passed", true, "source_database_sha256", before,
                    "database_sha256", after, "logical_sql_sha256", logical,
                    "original_bytes", originalBytes, "compacted_bytes", replace ? bytes : originalBytes,
                    "logical_contents_preserved", true);
        } finally {
            try (var paths = Files.walk(temporary)) {
                for (Path p : paths.sorted(Comparator.reverseOrder()).toList()) Files.delete(p);
            }
        }
    }

    /** Explicit offline packaging/maintenance command; receipt is published last. */
    public static void main(String[] args) throws Exception {
        if (args.length != 2 && args.length != 3)
            throw new IllegalArgumentException("Expected database base, new receipt, and optional existing query receipt");
        Path receipt = Path.of(args[1]).toAbsolutePath().normalize();
        if (Files.exists(receipt, LinkOption.NOFOLLOW_LINKS)) throw new IOException("Receipt already exists");
        for (Path p = receipt.getParent(); p != null; p = p.getParent())
            if (Files.isSymbolicLink(p)) throw new IOException("Linked receipt path");
        var json = new ObjectMapper();
        Map<String, Object> query = null;
        Path access = null;
        if (args.length == 3) {
            access = ordinary(Path.of(args[2]));
            query = json.readValue(access.toFile(), new com.fasterxml.jackson.core.type.TypeReference<>() {});
            if (!Integer.valueOf(1).equals(query.get("format_version"))
                    || !"JASMINE_WEB_READER".equals(query.get("principal"))
                    || !"SELECT on approved local base tables".equals(query.get("privileges"))
                    || !digest(ordinary(Path.of(args[0] + ".mv.db"))).equals(query.get("database_sha256")))
                throw new IOException("Query receipt does not describe this database");
        }
        var result = compact(Path.of(args[0]));
        if (access != null) {
            query.put("database_sha256", result.get("database_sha256"));
            query.put("compaction_source_database_sha256", result.get("source_database_sha256"));
            Files.writeString(access, json.writeValueAsString(query) + "\n");
        }
        Files.writeString(receipt, json.writeValueAsString(result) + "\n", StandardOpenOption.CREATE_NEW);
        System.out.println("Input compaction verified: " + result.get("original_bytes") + " -> " + result.get("compacted_bytes") + " bytes");
    }
}
