/* (C) Copyright 2026, by Ross Richardson
 * Passive numeric telemetry around the unchanged MultiRun entry point.
 * Used only by isolated resource-recovery and research-calibration rehearsals.
 * @author ross richardson
 */
import java.lang.management.ManagementFactory;
import java.nio.file.Files;
import java.nio.file.Path;
import microsim.monitoring.MemoryMonitor;
import simpaths.experiment.SimPathsMultiRun;

public final class SimPathsResourceProbe {
    private static final MemoryMonitor MONITOR = new MemoryMonitor(message -> {});
    private static final long START = System.nanoTime();
    private static long sequence;

    private static long counter(String name) {
        try {
            return Long.parseLong(Files.readString(Path.of("/sys/fs/cgroup", name)).trim());
        } catch (Exception unavailable) {
            return -1;
        }
    }

    private static synchronized void sample() {
        MemoryMonitor.Snapshot value = MONITOR.sample();
        long gc = ManagementFactory.getGarbageCollectorMXBeans().stream()
            .mapToLong(bean -> Math.max(0, bean.getCollectionTime())).sum();
        System.out.println("SIMPATHS_RESOURCE_SAMPLE {\"sequence\":" + sequence++
            + ",\"elapsed_ms\":" + (System.nanoTime() - START) / 1_000_000L
            + ",\"heap_used_bytes\":" + value.heapUsedBytes()
            + ",\"heap_committed_bytes\":" + value.heapCommittedBytes()
            + ",\"heap_max_bytes\":" + value.heapMaxBytes()
            + ",\"nonheap_used_bytes\":" + ManagementFactory.getMemoryMXBean().getNonHeapMemoryUsage().getUsed()
            + ",\"container_limit_bytes\":" + value.containerLimitBytes().orElse(-1)
            + ",\"container_used_bytes\":" + value.containerUsedBytes().orElse(-1)
            + ",\"inactive_file_bytes\":" + value.inactiveFileBytes().orElse(-1)
            + ",\"working_set_bytes\":" + value.containerWorkingSetBytes().orElse(-1)
            + ",\"cgroup_peak_bytes\":" + counter("memory.peak")
            + ",\"gc_ms\":" + gc + "}");
    }

    public static void main(String[] args) {
        // No forced GC, extra allocation, engine controls, JMX port or input reads.
        long interval = Long.getLong("simpaths.resource.sample.millis", 2000L);
        if (interval < 2000 || interval > 300000)
            throw new IllegalArgumentException("Resource sample interval must be 2000–300000 ms");
        sample();
        Thread sampler = new Thread(() -> {
            try {
                while (true) {
                    // Longer private calibration attempts use a recorded interval
                    // to stay within the same bounded Docker log allowance.
                    Thread.sleep(interval);
                    sample();
                }
            } catch (InterruptedException stopped) {
                Thread.currentThread().interrupt();
            }
        }, "resource-acceptance-sampler");
        sampler.setDaemon(true);
        sampler.start();
        SimPathsMultiRun.main(args);
        sample();
    }
}
