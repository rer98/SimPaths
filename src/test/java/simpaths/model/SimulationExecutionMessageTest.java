/* (C) Copyright 2026, by Ross Richardson
 * Verifies the additional execution-time message preserves legacy completion output.
 * @author ross richardson
 */
package simpaths.model;

import microsim.engine.SimulationEngine;
import org.junit.jupiter.api.Test;
import simpaths.model.enums.Country;
import java.io.ByteArrayOutputStream;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class SimulationExecutionMessageTest {
    @Test void printsExecutionTimeOnlyAtFinalYearAlongsideOriginalMessage() throws Exception {
        SimPathsModel model = new SimPathsModel(Country.UK, 2019);
        model.commentsOn = false;
        SimulationEngine engine = mock(SimulationEngine.class);
        when(engine.getExecutionTimeNanos()).thenReturn(90_000_000_000L);
        model.setEngine(engine);
        var year = SimPathsModel.class.getDeclaredField("year");
        year.setAccessible(true);
        year.set(model, 2025);
        var output = new ByteArrayOutputStream();
        PrintStream original = System.out;
        try (var capture = new PrintStream(output, true, StandardCharsets.UTF_8)) {
            System.setOut(capture);
            model.onEvent(SimPathsModel.Processes.UpdateYear);
            assertFalse(output.toString(StandardCharsets.UTF_8).contains("Simulation execution time:"));
            output.reset();
            model.onEvent(SimPathsModel.Processes.UpdateYear);
        } finally {
            System.setOut(original);
        }
        String text = output.toString(StandardCharsets.UTF_8);
        assertTrue(text.contains("Finished simulating population in "));
        assertTrue(text.contains("Simulation execution time: 1.5 minutes"));
        assertTrue(text.contains("excluding build, waiting and pauses"));
        assertEquals(2027, year.get(model));
    }
}
