/* (C) Copyright 2026, by Ross Richardson
 * Passive numeric telemetry around the unchanged MultiRun entry point.
 * Used only by the isolated resource-recovery acceptance rehearsal.
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
        sample();
        Thread sampler = new Thread(() -> {
            try {
                while (true) {
                    // Two seconds also keeps a 75-minute attempt inside the
                    // executor's existing bounded Docker log allowance.
                    Thread.sleep(2000);
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
