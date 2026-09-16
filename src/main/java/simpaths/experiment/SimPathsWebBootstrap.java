package simpaths.experiment;

import microsim.web.SimulationServer;

/** Prepare a private UK/2019 workspace before opening the web server. */
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
        SimPathsStartupConfig config = SimPathsStartupConfig.quickStart();
        SimPathsSetupService.prepareQuickStart(config, rebuild);
        SimPathsStart.configureWeb(config);
        if (!prepareOnly) SimulationServer.main(new String[0]);
    }
}
