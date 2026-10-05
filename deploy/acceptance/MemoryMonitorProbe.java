/* (C) Copyright 2026, by Ross Richardson
 * Fictional MultiRun exercising the packaged memory monitor with real JVM
 * allocations and, in Docker, real cgroup readings. No scientific model runs.
 * @author ross richardson
 */
package simpaths.experiment;

import java.io.PrintStream;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.OptionalLong;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.function.BooleanSupplier;

import microsim.engine.MultiRun;
import microsim.engine.SimulationEngine;
import microsim.monitoring.MemoryMonitor;

public final class MemoryMonitorProbe extends MultiRun {
    private static final int BLOCK = 256 * 1024;
    private static final long BEGAN = System.nanoTime();
    private static final List<byte[]> HEAP = new ArrayList<>();
    private static final List<ByteBuffer> DIRECT = new ArrayList<>();
    private static final List<Warning> WARNINGS = new CopyOnWriteArrayList<>();
    private static String mode;
    private static long initialLimit, changedLimit;

    private record Warning(String kind, long elapsedMs) { }

    public static void main(String[] args) {
        mode = args[0];
        initialLimit = Long.parseLong(args[1]);
        changedLimit = Long.parseLong(args[2]);
        Thread.setDefaultUncaughtExceptionHandler((thread, error) -> {
            System.err.println("Probe failed: " + error.getClass().getSimpleName());
            System.exit(1);
        });
        System.setOut(new PrintStream(System.out, true, StandardCharsets.UTF_8) {
            @Override
            public void println(String message) {
                if (message.startsWith("WARNING: Java heap is ")) record("heap");
                if (message.startsWith("WARNING: Container working set is ")) record("container");
                super.println(message);
            }
        });
        new MemoryMonitorProbe().start();
    }

    private static void record(String kind) {
        WARNINGS.add(new Warning(kind, (System.nanoTime() - BEGAN) / 1_000_000));
    }

    private static long count(String kind) {
        return WARNINGS.stream().filter(value -> value.kind.equals(kind)).count();
    }

    private static long monitors() {
        return Thread.getAllStackTraces().keySet().stream()
            .filter(thread -> thread.isAlive() && thread.isDaemon()
                && thread.getName().equals("microsim-memory-monitor")).count();
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new IllegalStateException(message);
    }

    private static String value(OptionalLong bytes) {
        return bytes.isPresent() ? Long.toString(bytes.getAsLong()) : "null";
    }

    private static void snapshot(String phase, MemoryMonitor.Snapshot sample) {
        System.out.println("MEMORY_MONITOR_SAMPLE {\"phase\":\"" + phase + "\""
            + ",\"elapsed_ms\":" + (System.nanoTime() - BEGAN) / 1_000_000
            + ",\"heap_used_bytes\":" + sample.heapUsedBytes()
            + ",\"heap_committed_bytes\":" + sample.heapCommittedBytes()
            + ",\"heap_max_bytes\":" + sample.heapMaxBytes()
            + ",\"container_limit_bytes\":" + value(sample.containerLimitBytes())
            + ",\"container_used_bytes\":" + value(sample.containerUsedBytes())
            + ",\"inactive_file_bytes\":" + value(sample.inactiveFileBytes())
            + ",\"working_set_bytes\":" + value(sample.containerWorkingSetBytes())
            + ",\"monitor_threads\":" + monitors()
            + ",\"direct_allocated_bytes\":" + (long) DIRECT.size() * BLOCK + "}");
    }

    private static void waitFor(BooleanSupplier condition, long milliseconds, String message)
            throws InterruptedException {
        long deadline = System.nanoTime() + milliseconds * 1_000_000;
        while (!condition.getAsBoolean()) {
            require(System.nanoTime() < deadline, message);
            Thread.sleep(50);
        }
    }

    private static void heapPressure(MemoryMonitor sampler) {
        // Keep allocations reachable and measure after GC, so short-lived
        // sampling allocations do not create a misleading pressure fixture.
        for (int batch = 0; batch < 64; batch++) {
            System.gc();
            MemoryMonitor.Snapshot sample = sampler.sample();
            if (sample.heapUsedBytes() >= sample.heapMaxBytes() * .88) return;
            long missing = (long) (sample.heapMaxBytes() * .89) - sample.heapUsedBytes();
            int blocks = (int) Math.min(8, Math.max(1, missing / BLOCK));
            for (int i = 0; i < blocks; i++) {
                byte[] block = new byte[BLOCK];
                block[0] = 1;
                HEAP.add(block);
            }
        }
        throw new IllegalStateException("Could not establish bounded heap pressure");
    }

    private static void containerPressure(MemoryMonitor sampler) throws InterruptedException {
        for (int block = 0; block < 768; block++) {
            MemoryMonitor.Snapshot sample = sampler.sample();
            require(sample.containerLimitBytes().orElse(0) == initialLimit,
                "Container does not expose its configured memory limit");
            long working = sample.containerWorkingSetBytes().orElseThrow();
            if (working >= initialLimit * .88) return;
            // Do not try to defeat a large cache charge or approach OOM merely
            // to obtain a warning. Fail this fixture with headroom remaining.
            require(sample.containerUsedBytes().orElseThrow() + BLOCK < initialLimit * .92,
                "Insufficient safe headroom for the pressure fixture");
            ByteBuffer buffer = ByteBuffer.allocateDirect(BLOCK);
            for (int page = 0; page < BLOCK; page += 4096) buffer.put(page, (byte) 1);
            DIRECT.add(buffer);
            Thread.sleep(5);
        }
        throw new IllegalStateException("Could not establish bounded container pressure");
    }

    private static void warningEvidence() {
        StringBuilder json = new StringBuilder("MEMORY_MONITOR_WARNINGS [");
        for (Warning warning : WARNINGS) {
            if (json.charAt(json.length() - 1) != '[') json.append(',');
            json.append("{\"kind\":\"").append(warning.kind)
                .append("\",\"elapsed_ms\":").append(warning.elapsedMs).append('}');
        }
        System.out.println(json.append(']').toString());
    }

    @Override
    public synchronized void go() {
        try (MemoryMonitor sampler = new MemoryMonitor(message -> System.out.println(message))) {
            boolean enabled = !mode.equals("disabled");
            waitFor(() -> monitors() == (enabled ? 1 : 0), 2000,
                "Unexpected MultiRun monitor opt-in state");
            MemoryMonitor.Snapshot initial = sampler.sample();
            snapshot("initial", initial);
            if (initialLimit > 0) require(initial.containerLimitBytes().orElse(0) == initialLimit,
                "Initial cgroup limit differs from Docker policy");
            if (enabled) {
                heapPressure(sampler);
                if (mode.equals("container")) containerPressure(sampler);
                snapshot("pressure", sampler.sample());
                waitFor(() -> count("heap") > 0
                        && (!mode.equals("container") || count("container") > 0),
                    25_000, "Expected memory warnings did not reach MultiRun stdout");
                // Cross a further ten-second polling interval while staying
                // within the thirty-second warning throttle.
                Thread.sleep(11_000);
                snapshot("held", sampler.sample());
                if (mode.equals("container")) {
                    System.out.println("MEMORY_MONITOR_RESIZE_READY");
                    waitFor(() -> sampler.sample().containerLimitBytes().orElse(0) == changedLimit,
                        30_000, "Container limit change was not observed");
                    MemoryMonitor.Snapshot changed = sampler.sample();
                    require(changed.heapMaxBytes() == initial.heapMaxBytes(),
                        "Container resize unexpectedly changed Java heap maximum");
                    require(changed.containerWorkingSetBytes().orElseThrow() < changedLimit * .85,
                        "Bounded resize did not relieve measured container pressure");
                    snapshot("resized", changed);
                }
            }
            warningEvidence();
            require(monitors() == (enabled ? 1 : 0), "Monitor lifecycle changed during the fixture");
            System.out.println("MEMORY_MONITOR_OK " + mode);
            toBeContinued = false;
            executionActive = false;
        } catch (Exception error) {
            System.err.println("Probe stopped: " + error.getMessage());
            throw new IllegalStateException("Memory monitor probe did not pass", error);
        }
    }

    @Override public boolean nextModel() { return false; }
    @Override public String setupRunLabel() { return "fictional-memory-monitor-probe"; }
    @Override public void buildExperiment(SimulationEngine engine) { }
}
