package simpaths.model.taxes;

/* (C) Copyright 2026, by Ross Richardson
 *
 * Build and load the tax-benefit donor pool and its matching indices.
 *
 * @author ross richardson
 */

import jakarta.persistence.Persistence;
import org.apache.commons.lang3.tuple.Triple;
import org.apache.commons.math3.util.Pair;
import simpaths.data.MahalanobisDistance;
import simpaths.data.Parameters;

import java.io.*;
import java.nio.file.*;
import java.security.DigestInputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.*;
import java.util.zip.GZIPInputStream;
import java.util.zip.GZIPOutputStream;

public final class DonorLookup {
    private static final Path BUNDLE = Path.of("prepared-donor-lookup.bin.gz");
    private static final int FORMAT = 1;
    private static Source installedSource;

    private record Source(String databaseHash, int basePriceYear, Map<Integer, Pair<String, Integer>> schedule) {
        static Source from(Path database) throws IOException {
            try (var bytes = new DigestInputStream(Files.newInputStream(Path.of(database + ".mv.db")),
                    MessageDigest.getInstance("SHA-256"))) {
                bytes.transferTo(OutputStream.nullOutputStream());
                return new Source(HexFormat.of().formatHex(bytes.getMessageDigest().digest()),
                        Parameters.BASE_PRICE_YEAR, new TreeMap<>(Parameters.EUROMODpolicyScheduleSystemYearMap));
            } catch (NoSuchAlgorithmException e) {
                throw new IllegalStateException(e);
            }
        }

        void write(DataOutputStream out) throws IOException {
            out.writeUTF(databaseHash);
            out.writeInt(basePriceYear);
            out.writeInt(schedule.size());
            for (var entry : schedule.entrySet()) {
                out.writeInt(entry.getKey());
                out.writeUTF(entry.getValue().getKey());
                out.writeInt(entry.getValue().getValue());
            }
        }

        boolean matches(DataInputStream in) throws IOException {
            if (!databaseHash.equals(in.readUTF()) || basePriceYear != in.readInt()
                    || schedule.size() != in.readInt()) return false;
            for (var entry : schedule.entrySet()) {
                if (entry.getKey() != in.readInt() || !entry.getValue().getKey().equals(in.readUTF())
                        || entry.getValue().getValue() != in.readInt()) return false;
            }
            return true;
        }
    }

    private DonorLookup() {}

    public static void initialise(Path database, boolean quickStartPilot) {
        if (!quickStartPilot && !Parameters.getTaxdbReferences().isEmpty()) return;

        Source source = null;
        if (quickStartPilot) {
            try {
                source = Source.from(database);
                if (source.equals(installedSource)) return;
                if (Files.isRegularFile(BUNDLE) && loadPrepared(source)) {
                    installedSource = source;
                    System.out.println("Loaded prepared UKMOD donor lookup");
                    return;
                }
            } catch (IOException e) {
                System.out.println("Prepared UKMOD donor lookup is unreadable; building from the input database");
            }
        }
        var pool = loadPool(database, false);
        install(pool, index(pool));
        installedSource = source;
    }

    private static boolean loadPrepared(Source source) throws IOException {
        try (var file = Files.newInputStream(BUNDLE);
             var in = new DataInputStream(new BufferedInputStream(new GZIPInputStream(file, 65536)))) {
            if (in.readInt() != FORMAT || !source.matches(in)) {
                System.out.println("Prepared UKMOD donor lookup does not match current inputs");
                return false;
            }
            var pool = readPool(in);
            var references = readReferences(in, pool.size());
            if (in.read() != -1) throw new IOException("Unexpected data after donor lookup");
            install(pool, references);
            return true;
        }
    }

    public static void prepare(Path database) throws IOException {
        Source source = Source.from(database);
        var pool = loadPool(database, true);
        var references = index(pool);
        Path temporary = Files.createTempFile(BUNDLE.toAbsolutePath().getParent(), "donor-lookup-", ".tmp");
        try {
            try (var out = new DataOutputStream(new BufferedOutputStream(new GZIPOutputStream(
                    Files.newOutputStream(temporary), 65536)))) {
                out.writeInt(FORMAT);
                source.write(out);
                writePool(out, pool);
                out.writeInt(references.size());
                for (var entry : references.entrySet()) {
                    var key = entry.getKey();
                    out.writeInt(key.getLeft());
                    out.writeInt(key.getMiddle());
                    out.writeInt(key.getRight());
                    out.writeInt(entry.getValue().size());
                    for (int donor : entry.getValue()) out.writeInt(donor);
                }
            }
            Files.move(temporary, BUNDLE, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
        } finally {
            Files.deleteIfExists(temporary);
        }
        System.out.println("Prepared UKMOD donor lookup for " + pool.size() + " donor tax units");
    }

    private static List<DonorTaxUnit> loadPool(Path database, boolean readOnly) {
        System.out.println("Populating donor database indices");
        Map<String, String> properties = new HashMap<>();
        properties.put("hibernate.connection.url", "jdbc:h2:file:" + database
                + ";TRACE_LEVEL_FILE=0;TRACE_LEVEL_SYSTEM_OUT=0"
                + (readOnly ? ";IFEXISTS=TRUE;ACCESS_MODE_DATA=r" : ";AUTO_SERVER=TRUE"));
        if (readOnly) properties.put("hibernate.hbm2ddl.auto", "none");
        var factory = Persistence.createEntityManagerFactory("tax-database", properties);
        try {
            var em = factory.createEntityManager();
            try {
                var transaction = em.getTransaction();
                transaction.begin();
                try {
                    var pool = em.createQuery(
                            "SELECT DISTINCT tu FROM DonorTaxUnit tu LEFT JOIN FETCH tu.policies tp ORDER BY tp.originalIncomePerMonth",
                            DonorTaxUnit.class).getResultList();
                    System.out.println("Completed accessing donor data from the database");
                    return pool;
                } finally {
                    transaction.rollback();
                }
            } finally {
                em.close();
            }
        } finally {
            factory.close();
        }
    }

    private static Map<Triple<Integer, Integer, Integer>, List<Integer>> index(List<DonorTaxUnit> pool) {
        Map<Triple<Integer, Integer, Integer>, List<Integer>> references = new LinkedHashMap<>();
        for (int i = 0; i < pool.size(); i++) {
            for (var scheduledPolicy : Parameters.EUROMODpolicyScheduleSystemYearMap.values()) {
                int year = scheduledPolicy.getValue();
                var policy = pool.get(i).getPolicyBySystemYear(year);
                for (int regime = 0; regime < Parameters.TAXDB_REGIMES; regime++) {
                    var key = Triple.of(year, regime, policy.getDonorKey(regime));
                    var donors = references.computeIfAbsent(key, ignored -> new ArrayList<>());
                    int position = donors.size();
                    // Insert after ties, in the same order as the original donor query.
                    while (position > 0 && pool.get(donors.get(position - 1)).getPolicyBySystemYear(year)
                            .getOriginalIncomePerMonth() > policy.getOriginalIncomePerMonth()) {
                        position--;
                    }
                    donors.add(position, i);
                }
            }
        }
        return references;
    }

    private static void install(List<DonorTaxUnit> pool, Map<Triple<Integer, Integer, Integer>, List<Integer>> references) {
        List<double[]> dualIncome = new ArrayList<>();
        List<double[]> childcare = new ArrayList<>();
        List<double[]> dualIncomeChildcare = new ArrayList<>();
        for (var donor : pool) {
            var policy = donor.getPolicyBySystemYear(Parameters.BASE_PRICE_YEAR);
            double income = policy.getNormalisedOriginalIncomePerMonth();
            double secondIncome = policy.getNormalisedSecondIncomePerMonth();
            double childcareCost = policy.getNormalisedChildcareCostPerMonth();
            if (secondIncome > 0.01 && childcareCost < 0.01) dualIncome.add(new double[]{income, secondIncome});
            if (childcareCost > 0.01 && secondIncome < 0.01) childcare.add(new double[]{income, childcareCost});
            if (secondIncome > 0.01 && childcareCost > 0.01)
                dualIncomeChildcare.add(new double[]{income, secondIncome, childcareCost});
        }
        var mdDualIncome = new MahalanobisDistance(dualIncome.toArray(double[][]::new));
        var mdChildcare = new MahalanobisDistance(childcare.toArray(double[][]::new));
        var mdDualIncomeChildcare = new MahalanobisDistance(dualIncomeChildcare.toArray(double[][]::new));
        Parameters.setDonorPool(pool);
        Parameters.setTaxdbReferences(references);
        Parameters.setMdDualIncome(mdDualIncome);
        Parameters.setMdChildcare(mdChildcare);
        Parameters.setMdDualIncomeChildcare(mdDualIncomeChildcare);
    }

    private static void writePool(DataOutputStream out, List<DonorTaxUnit> pool) throws IOException {
        out.writeInt(pool.size());
        for (var donor : pool) {
            out.writeLong(donor.getId());
            out.writeDouble(donor.getWeight());
            out.writeInt(donor.getPolicies().size());
            // These are the policy values used in matching and tax-benefit imputation.
            for (var policy : donor.getPolicies()) {
                out.writeInt(policy.getFromYear());
                out.writeInt(policy.getSystemYear());
                out.writeDouble(policy.getOriginalIncomePerMonth());
                out.writeDouble(policy.getDisposableIncomePerMonth());
                out.writeDouble(policy.getSecondIncomePerMonth());
                out.writeDouble(policy.getChildcareCostPerMonth());
                out.writeDouble(policy.getBenMeansTestPerMonth());
                out.writeDouble(policy.getBenNonMeansTestPerMonth());
                out.writeInt(policy.getReceivesUC());
                out.writeInt(policy.getReceivesLegacyBenefit());
            }
        }
    }

    private static List<DonorTaxUnit> readPool(DataInputStream in) throws IOException {
        int count = readCount(in, 1_000_000);
        List<DonorTaxUnit> pool = new ArrayList<>(count);
        for (int i = 0; i < count; i++) {
            var donor = new DonorTaxUnit();
            donor.setId(in.readLong());
            donor.setWeight(in.readDouble());
            int policies = readCount(in, 100);
            for (int j = 0; j < policies; j++) {
                var policy = new DonorTaxUnitPolicy(in.readInt(), donor);
                policy.setSystemYear(in.readInt());
                policy.setOriginalIncomePerMonth(in.readDouble());
                policy.setDisposableIncomePerMonth(in.readDouble());
                policy.setSecondIncomePerMonth(in.readDouble());
                policy.setChildcareCostPerMonth(in.readDouble());
                policy.setBenMeansTestPerMonth(in.readDouble());
                policy.setBenNonMeansTestPerMonth(in.readDouble());
                policy.setReceivesUC(in.readInt());
                policy.setReceivesLegacyBenefit(in.readInt());
                donor.getPolicies().add(policy);
            }
            pool.add(donor);
        }
        return pool;
    }

    private static Map<Triple<Integer, Integer, Integer>, List<Integer>> readReferences(DataInputStream in, int poolSize)
            throws IOException {
        int count = readCount(in, 1_000_000);
        Map<Triple<Integer, Integer, Integer>, List<Integer>> references = new LinkedHashMap<>();
        for (int i = 0; i < count; i++) {
            var key = Triple.of(in.readInt(), in.readInt(), in.readInt());
            int size = readCount(in, poolSize * Parameters.EUROMODpolicyScheduleSystemYearMap.size());
            List<Integer> donors = new ArrayList<>(size);
            for (int j = 0; j < size; j++) {
                int donor = in.readInt();
                if (donor < 0 || donor >= poolSize) throw new IOException("Invalid donor position");
                donors.add(donor);
            }
            if (references.put(key, donors) != null) throw new IOException("Duplicate donor key");
        }
        return references;
    }

    private static int readCount(DataInputStream in, int maximum) throws IOException {
        int value = in.readInt();
        if (value < 1 || value > maximum) throw new IOException("Invalid donor lookup size");
        return value;
    }
}
