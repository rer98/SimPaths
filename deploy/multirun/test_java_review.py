"""(C) Copyright 2026, by Ross Richardson

Real-JVM observer tests with tiny fictional model APIs and deterministic output.
No scientific simulation, Docker, forced GC, database or network is used.
@author ross richardson
"""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

from . import java_review

STUBS={
 'microsim/engine/SimulationManager.java':'package microsim.engine; public interface SimulationManager {}',
 'microsim/event/SystemEventType.java':'package microsim.event; public enum SystemEventType { End }',
 'microsim/engine/SimulationEngine.java':'''package microsim.engine;
 public class SimulationEngine {
 private static final SimulationEngine INSTANCE=new SimulationEngine();
 public static SimulationEngine getInstance(){return INSTANCE;}
 public SimulationManager[] getModelArray(){return new SimulationManager[]{new simpaths.model.SimPathsModel()};}
 public int getCurrentRunNumber(){return 1;}
 private final java.util.List<java.util.function.Consumer<microsim.event.SystemEventType>> listeners=new java.util.ArrayList<>();
 public java.util.List<java.util.function.Consumer<microsim.event.SystemEventType>> getEngineListeners(){return listeners;}
 }''',
 'simpaths/model/SimPathsModel.java':'''package simpaths.model;
 public class SimPathsModel implements microsim.engine.SimulationManager {
 public int getYear(){return 2035;}
 public java.util.List<Integer> getPersons(){return java.util.List.of(1,2,3);}
 }''',
 'Fixture.java':'''public class Fixture {
 public static void main(String[] args) {
 if(args.length>0 && args[0].equals("fail"))throw new IllegalStateException("fixture-science-failure");
 System.out.println("Starting year 2035");
 System.out.println("Finished year 2035 (in 0.01 seconds)");
 for(var listener:microsim.engine.SimulationEngine.getInstance().getEngineListeners())
 listener.accept(microsim.event.SystemEventType.End);
 System.out.println("RESULT 42");
 } }'''}


class JavaReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.temporary.cleanup)
        cls.root=Path(cls.temporary.name);classes=cls.root/'classes';classes.mkdir()
        sources=[]
        for name,text in STUBS.items():
            file=cls.root/'src'/name;file.parent.mkdir(parents=True,exist_ok=True)
            file.write_text('/* (C) Copyright 2026, by Ross Richardson. Fictional observer fixture. @author ross richardson */\n'+text)
            sources.append(str(file))
        subprocess.run(['javac','-proc:none','-d',str(classes),*sources],check=True,capture_output=True,timeout=30)
        cls.model=cls.root/'model.jar'
        with zipfile.ZipFile(cls.model,'w') as jar:
            for file in classes.rglob('*.class'):jar.write(file,file.relative_to(classes).as_posix())
        cls.observer=java_review.prepare(cls.root,cls.model)

    def run_java(self,*arguments,directory=None,monitored=True):
        work=directory or self.root/('work-'+str(len(list(self.root.iterdir()))))
        work.mkdir(exist_ok=True)
        command=['java','-Xmx64m','-XX:ActiveProcessorCount=2']
        if monitored:
            command+=['-Dsimpaths.performance.directory='+str(work),'-cp',str(self.observer)+':'+str(self.model),
                      'simpaths.experiment.SimPathsPerformanceRun','Fixture']
        else:command+=['-cp',str(self.model),'Fixture']
        result=subprocess.run([*command,*arguments],capture_output=True,text=True,timeout=10)
        return result,work

    def test_optional_wrapper_preserves_scientific_output_and_records_numeric_boundaries(self):
        ordinary,_=self.run_java(monitored=False)
        observed,work=self.run_java()
        self.assertEqual(observed.returncode,0)
        self.assertEqual(ordinary.stdout,observed.stdout)
        from jasmine_web.batch.performance_trace import java_samples
        rows=java_samples(work)
        self.assertEqual(rows[0]['sequence'],0)
        self.assertEqual(rows[-1]['phase'],'finished')
        self.assertEqual([r['sequence'] for r in rows],list(range(len(rows))))
        self.assertTrue(any(r.get('population')==3 for r in rows))
        self.assertTrue(any(r['phase']=='between_repetitions' for r in rows))
        self.assertTrue(any(r.get('heap_max_bytes')==64*1024**2 for r in rows))
        self.assertNotIn('person',json.dumps(rows))

    def test_disabled_launch_creates_no_detailed_observer_files(self):
        result,work=self.run_java(monitored=False)
        self.assertEqual(result.returncode,0)
        self.assertEqual(list(work.iterdir()),[])

    def test_unwritable_measurement_destination_does_not_fail_science(self):
        work=self.root/'broken';work.mkdir(exist_ok=True)
        (work/'.performance-java.jsonl').mkdir()
        result,_=self.run_java(directory=work)
        self.assertEqual(result.returncode,0)
        self.assertIn('RESULT 42',result.stdout)

    def test_scientific_exception_remains_failure_with_original_cause(self):
        result,_=self.run_java('fail')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('fixture-science-failure',result.stderr)

    def test_installed_observer_rejects_replaced_jar_or_receipt(self):
        self.assertIsNotNone(java_review.installed(self.root,self.model))
        file=self.observer.with_suffix('.json');original=file.read_text()
        try:
            file.write_text(json.dumps(dict(model='0'*64)))
            self.assertIsNone(java_review.installed(self.root,self.model))
        finally:file.write_text(original)

    def test_production_simulation_and_preparation_adapters_preserve_original_main_and_limits(self):
        from .container_adapter import SimPathsContainerAdapter,ContainerExecution
        from .submission_adapter import PreparationAdapter
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory(dir=self.root) as temporary:
            request=Path(temporary)
            original='exec java -Xmx4096m -XX:ActiveProcessorCount=2 -cp /inputs/model.jar simpaths.experiment.SimPathsMultiRun -f /request/run.yml\n'
            (request/'run.sh').write_text(original)
            model=dict(sha256=java_review.file_hash(self.model))
            adapter=object.__new__(SimPathsContainerAdapter)
            adapter.prepared=self.root;adapter.receipt=dict(identity=dict(model=model))
            command=ContainerExecution('sha256:'+'a'*64,('/bin/sh','/request/run.sh','4096m'),str(self.root))
            changed=adapter.configure_review(None,request,command,15,state=self.root)
            self.assertEqual(changed.argv,('/bin/sh','/request/run-reviewed.sh','4096m'))
            self.assertEqual((request/'run.sh').read_text(),original)
            observed=(request/'run-reviewed.sh').read_text()
            self.assertIn('-Xmx4096m -XX:ActiveProcessorCount=2',observed)
            self.assertIn('SimPathsPerformanceRun simpaths.experiment.SimPathsMultiRun',observed)
            self.assertEqual((request/'performance-observer.jar').read_bytes(),self.observer.read_bytes())
            preparation=object.__new__(PreparationAdapter)
            preparation.parameters=Mock(return_value=dict(model=dict(release=dict(model=model))))
            command=ContainerExecution(command.image,('java','-Xmx512m','-XX:ActiveProcessorCount=2','-cp',
                '/request/classes:/inputs/model.jar','simpaths.experiment.PrepareDataset'),str(self.root))
            changed=preparation.configure_review(None,request,command,5,state=self.root)
            self.assertEqual(changed.argv[-2:],('simpaths.experiment.SimPathsPerformanceRun','simpaths.experiment.PrepareDataset'))
            self.assertEqual(changed.argv[2:4],('-Xmx512m','-XX:ActiveProcessorCount=2'))

    def test_cost_fixture_has_identical_result_and_reference_counters_with_and_without_observer(self):
        source=Path(__file__).resolve().parents[1]/'acceptance/MonitoringWorkload.java'
        classes=self.root/'cost-classes';classes.mkdir(exist_ok=True)
        subprocess.run(['javac','--release','17','-proc:none','-d',str(classes),str(source)],
            check=True,capture_output=True,timeout=30)
        results=[]
        for monitored in (False,True):
            with tempfile.TemporaryDirectory(dir=self.root) as temporary:
                work=Path(temporary)
                command=['java','-Xmx128m','-XX:ActiveProcessorCount=2',
                    '-Dsimpaths.performance.directory='+str(work),'-cp',str(self.observer)+':'+str(self.model)+':'+str(classes)]
                if monitored:command+=['simpaths.experiment.SimPathsPerformanceRun']
                subprocess.run([*command,'MonitoringWorkload','1000'],cwd=work,check=True,capture_output=True,timeout=10)
                results.append((work/'result.txt').read_bytes())
                self.assertGreater(json.loads((work/'reference.json').read_text())['process_cpu_ns'],0)
                self.assertEqual((work/'.performance-java.jsonl').exists(),monitored)
        self.assertEqual(results[0],results[1])
