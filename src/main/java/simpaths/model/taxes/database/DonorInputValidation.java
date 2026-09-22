/* (C) Copyright 2026, by Ross Richardson
 * Validate donor column definitions before importing and quote SQL identifiers safely.
 * @author ross richardson
 */
package simpaths.model.taxes.database;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.SQLException;
import java.util.*;
import microsim.data.excel.ExcelAssistant;
import simpaths.data.Parameters;
import simpaths.model.enums.Country;

public final class DonorInputValidation {
    private DonorInputValidation() { }

    public static String benefitUnitColumn(Object value) {
        if (!(value instanceof String name) || !name.matches("[A-Za-z_][A-Za-z0-9_]{0,127}"))
            throw new IllegalArgumentException("system_bu_names.xlsx must specify a plain donor column name for the selected country");
        return name;
    }

    public static String readBenefitUnitColumn(Path workbook, Country country) {
        if (workbook == null || !Files.isRegularFile(workbook))
            throw new IllegalArgumentException("Missing system_bu_names.xlsx");
        return benefitUnitColumn(ExcelAssistant.loadCoefficientMap(workbook.toString(), "Names", 1, 1)
                .getValue(country.getCountryName()));
    }

    /** Matches the existing TXT importer's exact header spelling; ambiguity is rejected. */
    public static void validateDonorHeader(Path file, String benefitUnit, boolean includeStatic) throws IOException {
        benefitUnitColumn(benefitUnit);
        try (var reader = Files.newBufferedReader(file)) {
            String line = reader.readLine();
            if (line == null) throw new IllegalArgumentException("UKMOD file is empty: " + file.getFileName());
            List<String> header = Arrays.asList(line.split("\t", -1));
            requireUnambiguous(header);
            var required = new LinkedHashSet<>(Arrays.asList(Parameters.DONOR_POLICY_VARIABLES));
            if (includeStatic) {
                required.addAll(Arrays.asList(Parameters.DONOR_STATIC_VARIABLES));
                required.add(benefitUnit);
            }
            if (!header.containsAll(required))
                throw new IllegalArgumentException("UKMOD file is missing required donor columns: " + file.getFileName());
        }
    }

    /** Validate CSV names before executing any schema-changing statement. */
    static void validateAggregateHeader(Path file, Collection<String> required) {
        try (var rows = new org.h2.tools.Csv().read(file.toString(), null, "UTF-8")) {
            var meta = rows.getMetaData();
            var names = new ArrayList<String>();
            for (int i = 1; i <= meta.getColumnCount(); i++) names.add(meta.getColumnName(i));
            // The legacy writer appends a trailing comma; H2 gives that empty final column a generated name.
            requireUnambiguous(names);
            var upper = new HashSet<String>();
            for (String name : names) upper.add(name.toUpperCase(Locale.ROOT));
            if (required.stream().anyMatch(name -> !upper.contains(name.toUpperCase(Locale.ROOT))))
                throw new IllegalArgumentException("Aggregated donor CSV is missing required columns");
        } catch (SQLException e) {
            throw new IllegalArgumentException("Cannot read aggregated donor CSV header", e);
        }
    }

    private static void requireUnambiguous(Collection<String> names) {
        var seen = new HashSet<String>();
        for (String name : names) {
            if (name.isBlank() || !seen.add(name.toUpperCase(Locale.ROOT)))
                throw new IllegalArgumentException("Donor header contains blank or duplicate column names");
        }
    }

    /** CSVREAD creates unquoted uppercase names. Preserve that established convention. */
    static String quoteIdentifier(String name) {
        return quoteExactIdentifier(name.toUpperCase(Locale.ROOT));
    }

    static String quoteExactIdentifier(String name) {
        return "\"" + name.replace("\"", "\"\"") + "\"";
    }

    static String columnList(Collection<String> names, Map<String, String> importedNames) {
        return names.stream().map(name -> quoteExactIdentifier(importedNames.get(name.toUpperCase(Locale.ROOT))))
                .collect(java.util.stream.Collectors.joining(","));
    }

    static String csvField(String value) {
        if (value.indexOf(',') >= 0 || value.indexOf('"') >= 0 || value.indexOf('\r') >= 0 || value.indexOf('\n') >= 0)
            return "\"" + value.replace("\"", "\"\"") + "\"";
        return value;
    }
}
