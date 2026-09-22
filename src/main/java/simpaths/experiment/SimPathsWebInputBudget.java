/* (C) Copyright 2026, by Ross Richardson
 * Web-only input structure and preparation-work limits; desktop import behaviour is unchanged.
 * @author ross richardson
 */
package simpaths.experiment;

import java.io.*;
import java.nio.file.*;
import java.util.*;
import microsim.web.server.CsvRecordReader;
import microsim.web.server.WorkbookBudget;

final class SimPathsWebInputBudget {
    static final long POLICY_BYTES = 4L * 1024 * 1024 * 1024;
    private SimPathsWebInputBudget() {}

    static List<String> header(Path file, char delimiter) throws IOException {
        try (var rows = reader(file, delimiter)) {
            var result = rows.readRecord();
            if (result == null) throw new IOException("Missing tabular header");
            return result;
        }
    }

    private static CsvRecordReader reader(Path file, char delimiter) throws IOException {
        return new CsvRecordReader(Files.newBufferedReader(file), delimiter, 2*1024*1024,65536,2048);
    }

    static void validateDirectory(Path input) throws IOException {
        try(var files=Files.walk(input)) {
            for (Path file : files.filter(Files::isRegularFile).toList()) {
                String name=file.getFileName().toString().toLowerCase(Locale.ROOT);
                if (name.endsWith(".xlsx") || name.endsWith(".xls")) WorkbookBudget.validate(file,name);
                if (name.endsWith(".csv") || name.endsWith(".txt")) {
                    if(Files.size(file)>512L*1024*1024) throw new IOException("Preparation input exceeds 512 MiB");
                    try(var rows=reader(file,name.endsWith(".txt")?'\t':',')) {
                        int count=0;
                        while(rows.readRecord()!=null) if(++count>2_000_000) throw new IOException("Input exceeds two million records");
                    }
                }
            }
        }
    }

    static String boundedLine(Reader input) throws IOException {
        var line=new StringBuilder(); int c; boolean any=false;
        while((c=input.read())!=-1 && c!='\n') { any=true; if(line.length()<65536)line.append((char)c); }
        return c<0 && !any ? null : line.toString();
    }
}
