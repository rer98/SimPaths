/* (C) Copyright 2026, by Ross Richardson
 * Optional, bounded numeric observations around an unchanged scientific main.
 * Selected at process launch; never forces GC or changes model events/seeds.
 * @author ross richardson
 */
package simpaths.experiment;

import java.io.PrintStream;
import java.lang.management.ManagementFactory;
import java.lang.management.MemoryUsage;
import java.lang.reflect.InvocationTargetException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.util.Arrays;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;
import microsim.engine.SimulationEngine;
import microsim.engine.SimulationManager;
import microsim.event.SystemEventType;
import simpaths.model.SimPathsModel;

/** Monitoring is deliberately absent from the ordinary, unwrapped launch. */
public final class SimPathsPerformanceRun {
    private static final long START = System.nanoTime();
    private static final ArrayBlockingQueue<String> EVENTS = new ArrayBlockingQueue<>(64);
    private static volatile Context context = new Context("setup", 0, 0, -1, 0);
    private static volatile SimulationEngine watched;
    private static long sequence;
    private static final AtomicLong DROPPED = new AtomicLong();
    private static volatile boolean failed;
    private static volatile boolean finishing;
    private static Thread sampler;
    private static Path destination;
    private static final long MAX_BYTES = 1024 * 1024;
    private record Context(String phase, int repetition, int year, int population, long observed) {}

    private static long elapsed() { return (System.nanoTime()-START)/1_000_000; }

    // Called only on the model's thread. Collection size is constant-time;
    // the sampler never traverses or concurrently inspects model collections.
    private static void progress(String line) {
        if (failed || line == null || line.length()>160 ||
                !(line.startsWith("Starting year ") || line.startsWith("Finished year "))) return;
        try {
            SimulationEngine engine=SimulationEngine.getInstance();
            for (SimulationManager manager:engine.getModelArray()) {
                if (!(manager instanceof SimPathsModel model)) continue;
                if (watched!=engine) {
                    watched=engine;
                    engine.getEngineListeners().add(0,event -> {
                        if (event==SystemEventType.End) {
                            context=new Context("between_repetitions",engine.getCurrentRunNumber(),0,-1,elapsed());
                            boundary();
                            watched=null;
                        }
                    });
                }
                context=new Context(line.startsWith("Starting")?"annual":"output",
                    engine.getCurrentRunNumber(),model.getYear(),
                    model.getPersons()==null?-1:model.getPersons().size(),elapsed());
                boundary();
                return;
            }
        } catch (Exception error) { failed=true; }
    }

    private static String fields(Context c) {
        return "\"phase\":\""+c.phase()+"\",\"repetition\":"+c.repetition()+",\"year\":"+c.year()
            +",\"population\":"+(c.population()<0?"null":c.population())
            +",\"population_observed_ms\":"+c.observed();
    }

    private static synchronized void boundary() {
        // Queue order is capture order, including timer and annual snapshots.
        // No disk write runs on the model thread; a full queue drops evidence.
        if (failed || finishing) return;
        try {
            if (!EVENTS.offer("\"observed_epoch_ms\":"+System.currentTimeMillis()+",\"elapsed_ms\":"+elapsed()+","+
                    fields(context)+metrics())) DROPPED.incrementAndGet();
        } catch (Exception error) { failed=true; }
    }

    private static synchronized void write(String fields) {
        if (failed) return;
        try {
            if (Files.exists(destination,LinkOption.NOFOLLOW_LINKS) &&
                    (!Files.isRegularFile(destination,LinkOption.NOFOLLOW_LINKS) || Files.isSymbolicLink(destination)))
                throw new IllegalStateException("Unsafe measurement output");
            if (Files.exists(destination) && Files.size(destination)>=MAX_BYTES-8192)
                Files.move(destination,destination.resolveSibling(".performance-java.previous"),
                    StandardCopyOption.REPLACE_EXISTING);
            String line="{\"sequence\":"+(sequence++)+","+fields+"}\n";
            Files.writeString(destination,line,StandardCharsets.UTF_8,
                StandardOpenOption.CREATE,StandardOpenOption.APPEND,LinkOption.NOFOLLOW_LINKS);
        } catch (Exception error) { failed=true; }
    }

    private static String metrics() {
            MemoryUsage heap=ManagementFactory.getMemoryMXBean().getHeapMemoryUsage();
            long gcMs=0,gcCount=0;
            boolean gcKnown=true;
            for (var gc:ManagementFactory.getGarbageCollectorMXBeans()) {
                if (gc.getCollectionTime()<0 || gc.getCollectionCount()<0) gcKnown=false;
                else { gcMs+=gc.getCollectionTime(); gcCount+=gc.getCollectionCount(); }
            }
            long cpu=ManagementFactory.getOperatingSystemMXBean() instanceof com.sun.management.OperatingSystemMXBean bean
                ? bean.getProcessCpuTime():-1;
            return ",\"heap_used_bytes\":"+heap.getUsed()+",\"heap_committed_bytes\":"+heap.getCommitted()
                +",\"heap_max_bytes\":"+(heap.getMax()<0?"null":heap.getMax())
                +",\"nonheap_used_bytes\":"+ManagementFactory.getMemoryMXBean().getNonHeapMemoryUsage().getUsed()
                +",\"gc_ms\":"+(gcKnown?gcMs:"null")+",\"gc_count\":"+(gcKnown?gcCount:"null")
                +",\"process_cpu_ns\":"+(cpu<0?"null":cpu)+",\"dropped\":"+DROPPED.get();
    }

    public static void main(String[] args) throws Throwable {
        if (args.length<1) throw new IllegalArgumentException("Supply the scientific main class");
        try {
            long interval=Long.parseLong(System.getProperty("simpaths.performance.seconds","15"));
            if (interval<5 || interval>300) throw new IllegalArgumentException("Invalid review interval");
            destination=Path.of(System.getProperty("simpaths.performance.directory","/work"),".performance-java.jsonl");
            PrintStream original=System.out;
            System.setOut(new PrintStream(original,true,StandardCharsets.UTF_8) {
                @Override public void println(String line) { original.println(line); progress(line); }
            });
            boundary();
            Runtime.getRuntime().addShutdownHook(new Thread(() -> {
                finishing=true;
                sampler.interrupt();
                try { sampler.join(2000); }
                catch (InterruptedException stopped) { Thread.currentThread().interrupt(); return; }
                // A stuck observer must not hold up an otherwise finished job.
                // Its absent final marker will make coverage explicitly partial.
                if (sampler.isAlive()) return;
                context=new Context("finished",context.repetition(),0,-1,elapsed());
                while (!EVENTS.isEmpty()) { String event=EVENTS.poll(); if (event!=null) write(event); }
                try {
                    write("\"observed_epoch_ms\":"+System.currentTimeMillis()+",\"elapsed_ms\":"+elapsed()+","+fields(context)+metrics());
                } catch (Exception error) { failed=true; }
            },"performance-finish"));
            sampler=new Thread(() -> {
                long next=System.nanoTime()+interval*1_000_000_000;
                try {
                    while (!failed && !finishing) {
                        long remaining=Math.max(0,next-System.nanoTime());
                        String event=EVENTS.poll(remaining,TimeUnit.NANOSECONDS);
                        if (event!=null) write(event);
                        if (System.nanoTime()>=next) {
                            boundary(); next=System.nanoTime()+interval*1_000_000_000;
                        }
                    }
                } catch (InterruptedException stopped) { Thread.currentThread().interrupt(); }
            },"performance-numeric-sampler");
            sampler.setDaemon(true); sampler.start();
        } catch (Exception error) { failed=true; }
        // Monitoring failures never swallow, replace or create scientific errors.
        try { Class.forName(args[0]).getMethod("main",String[].class).invoke(null,(Object)Arrays.copyOfRange(args,1,args.length)); }
        catch (InvocationTargetException error) { throw error.getCause(); }
    }
}
