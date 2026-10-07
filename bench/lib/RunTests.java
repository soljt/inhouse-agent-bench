import org.junit.platform.launcher.*; import org.junit.platform.launcher.core.*;
import org.junit.platform.launcher.listeners.*;
import static org.junit.platform.engine.discovery.DiscoverySelectors.selectClass;
// Same JUnit Platform launcher as the AGENTS.md recipe.
public class RunTests {
  public static void main(String[] a) {
    LauncherDiscoveryRequestBuilder b = LauncherDiscoveryRequestBuilder.request();
    for (String c : a) b.selectors(selectClass(c));
    Launcher l = LauncherFactory.create();
    SummaryGeneratingListener s = new SummaryGeneratingListener();
    l.registerTestExecutionListeners(s); l.execute(b.build());
    s.getSummary().printFailuresTo(new java.io.PrintWriter(System.out));
    var sum = s.getSummary();
    System.out.println("TESTS found="+sum.getTestsFoundCount()+" ok="+sum.getTestsSucceededCount()+" fail="+sum.getTestsFailedCount());
    System.exit(sum.getTestsFailedCount()>0||sum.getTestsFoundCount()==0?1:0);
  }
}
