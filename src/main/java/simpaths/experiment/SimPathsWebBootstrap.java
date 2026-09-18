package simpaths.experiment;


/** Launch prepared Quick Start, or explicitly prepare base inputs administratively. */
public final class SimPathsWebBootstrap {
    private SimPathsWebBootstrap() {}

    public static void main(String[] args) throws Exception {
        boolean prepareOnly = false;
        boolean rebuild = false;
        for (String arg : args) {
            switch (arg) {
                case "--prepare-only" -> prepareOnly = true;
                case "--rebuild-inputs" -> rebuild = true;
                default -> throw new IllegalArgumentException("Unknown bootstrap argument: " + arg);
            }
        }
        if (rebuild && !prepareOnly)
            throw new IllegalArgumentException("--rebuild-inputs requires --prepare-only; normal Quick Start never prepares inputs");
        if (prepareOnly) {
            SimPathsSetupService.prepareQuickStart(SimPathsStartupConfig.quickStart(), rebuild);
            System.out.println("Base inputs prepared. A saved Quick Start population still requires offline profile preparation.");
        } else {
            SimPathsQuickStart.main(new String[]{"--web"});
        }
    }
}
