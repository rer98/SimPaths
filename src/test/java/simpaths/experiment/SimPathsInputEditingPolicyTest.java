/* (C) Copyright 2026, by Ross Richardson
 * Tests training profile file permissions without changing desktop defaults.
 * @author ross richardson
 */
package simpaths.experiment;
import microsim.input.InputEditingPolicy;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
class SimPathsInputEditingPolicyTest {
 @Test void bothTrainingProfilesProtectOnlySchedulesAcrossLifecycleStates() throws Exception {
  var field=SimPathsStart.class.getDeclaredField("webProfile"); field.setAccessible(true);
  Object old=field.get(null);
  try {
   var builder=new SimPathsStart();
   field.set(null,null);
   assertNull(builder.inputReadOnlyReason("EUROMODpolicySchedule.xlsx",new InputEditingPolicy.State(false,false)));
   for(int population:new int[]{20000,50000}) {
    field.set(null,SimPathsStartupConfig.quickStart(population));
    for(boolean built:new boolean[]{false,true}) for(boolean started:new boolean[]{false,true}) {
     var state=new InputEditingPolicy.State(built,started);
     assertNotNull(builder.inputReadOnlyReason("EUROMODpolicySchedule.xlsx",state));
     assertNotNull(builder.inputReadOnlyReason("EUROMODoutput/training/EUROMODpolicySchedule.xlsx",state));
     assertNull(builder.inputReadOnlyReason("other.xlsx",state));
    }
   }
  } finally {field.set(null,old);}
 }
}
