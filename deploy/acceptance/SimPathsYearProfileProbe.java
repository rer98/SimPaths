/* (C) Copyright 2026, by Ross Richardson
 * Passive year/population resource observations around the frozen MultiRun JAR.
 * Native year messages run on the model thread; timer samples use immutable counts.
 * No model parameters, events, random draws, collectors or GC controls are changed.
 * @author ross richardson
 */
import java.io.PrintStream;
import java.lang.management.ManagementFactory;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import microsim.engine.SimulationEngine;
import microsim.engine.SimulationManager;
import microsim.event.SystemEventType;
import microsim.monitoring.MemoryMonitor;
import simpaths.model.SimPathsModel;

public final class SimPathsYearProfileProbe {
    private static final MemoryMonitor MONITOR = new MemoryMonitor(message -> {});
    private static final long START = System.nanoTime();
    private static final Pattern START_YEAR = Pattern.compile("^Starting year ([0-9]{4})$");
    private static final Pattern FINISH_YEAR = Pattern.compile("^Finished year ([0-9]{4}) \\(in .{1,80} seconds\\)$");
    private static PrintStream output;
    private static long sequence;
    private static SimulationEngine watched;
    private static YearProfileContext context = new YearProfileContext(0, 0, 0, -1, -1, -1, 0);

    private static long elapsed() { return (System.nanoTime() - START) / 1_000_000L; }

    private static SimPathsModel model(SimulationEngine engine) {
        for (SimulationManager manager : engine.getModelArray())
            if (manager instanceof SimPathsModel value) return value;
        throw new IllegalStateException("Year profiling cannot find the active model");
    }

    private static YearProfileContext observed(SimulationEngine engine, int year) {
        SimPathsModel value = model(engine);
        if (value.getPersons() == null || value.getBenefitUnits() == null || value.getHouseholds() == null)
            throw new IllegalStateException("Year profiling requires built population collections");
        return new YearProfileContext(engine.getCurrentRunNumber(), year, value.getYear(),
            value.getPersons().size(), value.getBenefitUnits().size(), value.getHouseholds().size(), elapsed());
    }

    private static long fileCounter(String name) {
        try { return Long.parseLong(Files.readString(Path.of("/sys/fs/cgroup", name)).trim()); }
        catch (Exception unavailable) { return -1; }
    }

    private static long cpuCounter(String name) {
        try {
            for (String line : Files.readAllLines(Path.of("/sys/fs/cgroup/cpu.stat"))) {
                String[] fields = line.trim().split("\\s+");
                if (fields.length == 2 && fields[0].equals(name)) return Long.parseLong(fields[1]);
            }
        } catch (Exception unavailable) { /* Report unavailable, never invent zero. */ }
        return -1;
    }

    private static synchronized void sample(String kind) {
        MemoryMonitor.Snapshot value = MONITOR.sample();
        YearProfileContext c = context;
        long gc = ManagementFactory.getGarbageCollectorMXBeans().stream()
            .mapToLong(bean -> Math.max(0, bean.getCollectionTime())).sum();
        long cpu = ManagementFactory.getOperatingSystemMXBean() instanceof com.sun.management.OperatingSystemMXBean bean
            ? bean.getProcessCpuTime() : -1;
        output.println("SIMPATHS_YEAR_RESOURCE_SAMPLE {\"sequence\":" + sequence++
            + ",\"kind\":\"" + kind + "\",\"elapsed_ms\":" + elapsed()
            + ",\"repetition\":" + c.repetition() + ",\"year\":" + c.year()
            + ",\"model_year\":" + c.modelYear() + ",\"person_count\":" + c.people()
            + ",\"benefit_count\":" + c.benefits() + ",\"household_count\":" + c.households()
            + ",\"population_observed_ms\":" + c.observedMs()
            + ",\"heap_used_bytes\":" + value.heapUsedBytes()
            + ",\"heap_committed_bytes\":" + value.heapCommittedBytes()
            + ",\"heap_max_bytes\":" + value.heapMaxBytes()
            + ",\"nonheap_used_bytes\":" + ManagementFactory.getMemoryMXBean().getNonHeapMemoryUsage().getUsed()
            + ",\"container_limit_bytes\":" + value.containerLimitBytes().orElse(-1)
            + ",\"container_used_bytes\":" + value.containerUsedBytes().orElse(-1)
            + ",\"inactive_file_bytes\":" + value.inactiveFileBytes().orElse(-1)
            + ",\"working_set_bytes\":" + value.containerWorkingSetBytes().orElse(-1)
            + ",\"cgroup_peak_bytes\":" + fileCounter("memory.peak")
            + ",\"gc_ms\":" + gc + ",\"process_cpu_ns\":" + cpu
            + ",\"cgroup_cpu_usage_usec\":" + cpuCounter("usage_usec")
            + ",\"cgroup_throttled_usec\":" + cpuCounter("throttled_usec")
            + ",\"cgroup_nr_periods\":" + cpuCounter("nr_periods")
            + ",\"cgroup_nr_throttled\":" + cpuCounter("nr_throttled") + "}");
    }

    private static synchronized void finished(SimulationEngine engine) {
        SimPathsModel value = model(engine);
        if (context.repetition() != engine.getCurrentRunNumber() || context.year() != value.getEndYear()
                || value.getYear() != value.getEndYear()+1)
            throw new IllegalStateException("Year profiling missed the final annual boundary");
        context = observed(engine, context.year());
        sample("year_closed");
        sample("run_finish");
        // Subsequent population loading belongs to setup, never to the completed year.
        context = new YearProfileContext(0, 0, 0, -1, -1, -1, elapsed());
        watched = null;
    }

    static synchronized void message(String line) {
        if (line == null || line.length() > 160) return;
        Matcher start = START_YEAR.matcher(line), finish = FINISH_YEAR.matcher(line);
        if (!start.matches() && !finish.matches()) return;
        SimulationEngine engine = SimulationEngine.getInstance();
        SimPathsModel value = model(engine);
        int year = Integer.parseInt(start.matches() ? start.group(1) : finish.group(1));
        if (year != value.getYear() || year < value.getStartYear() || year > value.getEndYear())
            throw new IllegalStateException("Native year message and model state differ");
        if (start.matches()) {
            if (watched != engine) {
                if (year != value.getStartYear() || context.year() != 0)
                    throw new IllegalStateException("Year profiling missed repetition start");
                watched = engine;
                // The year message is inside an event, before the engine iterates
                // its listeners. Insert before MultiRun's disposal listener.
                engine.getEngineListeners().add(0, event -> {
                    if (event == SystemEventType.End) finished(engine);
                });
            } else {
                if (context.year()+1 != year || context.repetition() != engine.getCurrentRunNumber())
                    throw new IllegalStateException("Noncontiguous annual profiling boundary");
                context = observed(engine, context.year());
                sample("year_closed");
            }
            context = observed(engine, year);
            sample("year_start");
        } else {
            if (context.year() != year || context.repetition() != engine.getCurrentRunNumber())
                throw new IllegalStateException("Year profiling finish has no matching start");
            context = observed(engine, year);
            sample("year_finish");
        }
    }

    public static void main(String[] args) {
        output = System.out;
        System.setOut(new YearProfileOutput(output));
        sample("process_start");
        Thread sampler = new Thread(() -> {
            try {
                while (true) { Thread.sleep(2000); sample("sample"); }
            } catch (InterruptedException stopped) { Thread.currentThread().interrupt(); }
        }, "year-resource-profile-sampler");
        sampler.setUncaughtExceptionHandler((thread, error) -> {
            System.err.println("Year resource profiler failed: " + error.getClass().getSimpleName());
            System.exit(1);
        });
        sampler.setDaemon(true);
        sampler.start();
        SimPathsResourceProbe.main(args);
    }
}

record YearProfileContext(int repetition, int year, int modelYear, int people,
                          int benefits, int households, long observedMs) {}

final class YearProfileOutput extends PrintStream {
    private final PrintStream destination;
    YearProfileOutput(PrintStream destination) {
        super(destination, true, StandardCharsets.UTF_8);
        this.destination = destination;
    }
    @Override public void println(String line) {
        destination.println(line); // Preserve every original line unchanged.
        SimPathsYearProfileProbe.message(line);
    }
}
