/* (C) Copyright 2026, by Ross Richardson
 * Fixed-work CPU, allocation and disk fixture for optional monitoring costs.
 * Reference counters are read once at the end in every comparison condition.
 * @author ross richardson
 */
import java.lang.management.ManagementFactory;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.HexFormat;

public final class MonitoringWorkload {
    public static void main(String[] args) throws Exception {
        int iterations=Integer.parseInt(args[0]);
        if(iterations<1 || iterations>10000000)throw new IllegalArgumentException("Bound fixture iterations");
        byte[] block=new byte[65536];
        for(int i=0;i<block.length;i++)block[i]=(byte)(i*17);
        byte[][] ring=new byte[64][];
        MessageDigest sha=MessageDigest.getInstance("SHA-256");
        long accumulator=0;
        for(int i=0;i<iterations;i++) {
            block[0]=(byte)i;
            accumulator+=sha.digest(block)[0];
            if(i%256==0)ring[(i/256)%ring.length]=new byte[65536];
            if(i%1024==0)Files.write(Path.of("scratch.tmp"),block);
        }
        Files.deleteIfExists(Path.of("scratch.tmp"));
        String result=HexFormat.of().formatHex(sha.digest(Long.toString(accumulator).getBytes(java.nio.charset.StandardCharsets.UTF_8)));
        Files.writeString(Path.of("result.txt"),result+"\n");
        long cpu=ManagementFactory.getOperatingSystemMXBean() instanceof com.sun.management.OperatingSystemMXBean bean
            ? bean.getProcessCpuTime():-1;
        String peak="null";
        try {peak=Files.readString(Path.of("/sys/fs/cgroup/memory.peak")).trim();}catch(Exception ignored){}
        Files.writeString(Path.of("reference.json"),"{\"process_cpu_ns\":"+(cpu<0?"null":cpu)+",\"memory_peak_bytes\":"+peak+"}\n");
    }
}
