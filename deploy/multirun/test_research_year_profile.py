"""(C) Copyright 2026, by Ross Richardson

Annual telemetry integrity, interval peaks and passive Java lifecycle checks.
The Java fixture has no scientific data, database, Docker or network activity.
@author ross richardson
"""
from copy import deepcopy
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from deploy.multirun import research_year_profile as years

ROOT=Path(__file__).resolve().parents[2]
MIB=1024**2


def profile_rows(*,population=1000,end_year=2021,repetitions=2,heap_mib=4096,memory_mib=5120):
    """Fictional peaks deliberately fall after startup and after the first year."""
    rows=[];observed=0;counts=(-1,-1,-1);peak=0
    def add(kind,rep=0,year=0,heap=500):
        nonlocal observed,counts,peak
        seq=len(rows);elapsed=seq*1000
        if kind!='sample' or not year:
            observed=elapsed
            counts=(population+rep*10+(year-2019)*20,500,400) if year else (-1,-1,-1)
        heap=heap*heap_mib//4096
        used=(heap+600)*MIB;peak=max(peak,used)
        rows.append(dict(sequence=seq,kind=kind,elapsed_ms=elapsed,repetition=rep,year=year,
            model_year=year+(kind in ('year_closed','run_finish')) if year else 0,
            person_count=counts[0],benefit_count=counts[1],household_count=counts[2],
            population_observed_ms=observed,heap_used_bytes=heap*MIB,heap_committed_bytes=heap_mib*MIB,
            heap_max_bytes=heap_mib*MIB,nonheap_used_bytes=100*MIB,container_limit_bytes=memory_mib*MIB,
            container_used_bytes=used,inactive_file_bytes=100*MIB,working_set_bytes=used-100*MIB,
            cgroup_peak_bytes=peak,gc_ms=seq*20,process_cpu_ns=seq*1000000,
            cgroup_cpu_usage_usec=seq*1000,cgroup_throttled_usec=seq*100,
            cgroup_nr_periods=seq*10,cgroup_nr_throttled=seq))
    add('process_start',heap=3800)
    for rep in range(1,repetitions+1):
        if rep>1:add('sample',heap=700)
        for year in range(2019,end_year+1):
            add('year_start',rep,year,heap=1000)
            add('sample',rep,year,heap=3000 if year==2019 else 600)
            add('year_finish',rep,year,heap=800)
            # Output work after the model's finish still belongs to this year.
            add('sample',rep,year,heap=3200 if year==2019 else 1200)
            add('year_closed',rep,year,heap=500)
        add('run_finish',rep,end_year,heap=500)
    add('sample',heap=3900)
    add('process_finish',heap=3500)
    return rows


class YearProfileTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.rows=profile_rows()

    def summary(self,rows=None,**changes):
        return years.summarise(self.rows if rows is None else rows,
            **(dict(population=1000,seeds=['606','607'],end_year=2021,milestones=(2020,2021))|changes))

    def test_intervals_include_output_work_and_keep_early_peaks_when_memory_falls(self):
        result=self.summary();first,last=result['repetitions'][0]['intervals']
        self.assertEqual(first['sampled_peaks']['heap_used_bytes'],3800*MIB)
        self.assertEqual(last['sampled_peaks']['heap_used_bytes'],1200*MIB)
        self.assertEqual(last['process_cumulative_peaks']['heap_used_bytes'],3800*MIB)
        annual=result['repetitions'][0]['annual'][0]
        self.assertEqual(annual['sampled_peaks']['heap_used_bytes'],3200*MIB)
        self.assertEqual(annual['elapsed_ms'],4000)
        self.assertEqual(annual['counters']['gc_ms'],80)
        self.assertEqual((first['start_year'],first['end_year'],last['start_year']),(2019,2020,2021))
        self.assertEqual(last['kernel_process_peak_bytes'],4400*MIB)

    def test_population_counts_are_observations_and_second_repetition_does_not_reuse_first(self):
        result=self.summary()
        self.assertEqual([r['seed'] for r in result['repetitions']],['606','607'])
        first,second=result['repetitions']
        self.assertEqual(first['annual'][0]['person_count_at_start'],1010)
        self.assertEqual(second['annual'][0]['person_count_at_start'],1020)
        self.assertEqual(second['intervals'][0]['max_observed_person_count'],1040)
        self.assertEqual(second['intervals'][1]['max_observed_person_count'],1060)
        self.assertEqual(first['timer_samples'],6)
        self.assertEqual(first['maximum_timer_gap_ms'],3000)
        self.assertIn('Not an exact intra-year',result['population_measurement'])

    def test_every_decade_is_closed_in_a_full_52_year_observation(self):
        rows=profile_rows(end_year=2070,repetitions=1)
        result=years.summarise(rows,population=1000,seeds=['606'])['repetitions'][0]
        self.assertEqual(len(result['annual']),52)
        self.assertEqual([r['end_year'] for r in result['intervals']],[2030,2035,2040,2050,2060,2070])
        self.assertEqual(result['annual'][-1]['year'],2070)

    def test_finalisation_peak_is_retained_separately_from_annual_peaks(self):
        result=self.summary()
        self.assertEqual(result['phases']['initialisation']['sampled_peaks']['heap_used_bytes'],3800*MIB)
        self.assertEqual(result['phases']['finalisation']['sampled_peaks']['heap_used_bytes'],3900*MIB)
        self.assertEqual(result['whole_process']['sampled_peaks']['heap_used_bytes'],3900*MIB)
        self.assertEqual(result['repetitions'][-1]['intervals'][-1]['process_cumulative_peaks']['heap_used_bytes'],3800*MIB)
        self.assertEqual(result['phases']['between_repetitions']['segments'],1)
        rows=deepcopy(self.rows);rows.append(dict(rows[-1],sequence=len(rows)))
        with self.assertRaises(ValueError):self.summary(rows)
        rows=deepcopy(self.rows);rows[-1].update(repetition=2,year=2021,model_year=2021,
            person_count=1000,benefit_count=500,household_count=400)
        with self.assertRaises(ValueError):self.summary(rows)

    def test_cpu_gc_and_throttling_ratios_use_elapsed_time_and_selected_quota(self):
        counters=dict(gc_ms=150,process_cpu_ns=1,cgroup_cpu_usage_usec=1500000,
            cgroup_throttled_usec=300000,cgroup_nr_periods=10,cgroup_nr_throttled=3)
        result=years.utilisation(counters,1000,2000)
        self.assertEqual(result,dict(average_vcpus=1.5,cpu_allowance_percent=75,
            gc_counter_wall_percent=15,throttled_period_percent=30))
        self.assertEqual(years.utilisation(counters,1000,1500)['cpu_allowance_percent'],100)
        counters['cgroup_cpu_usage_usec']=None;counters['cgroup_nr_periods']=None
        self.assertIsNone(years.utilisation(counters,1000,2000)['average_vcpus'])
        self.assertIsNone(years.utilisation(counters,1000,2000)['throttled_period_percent'])
        self.assertIsNone(years.utilisation(counters,0,2000)['gc_counter_wall_percent'])

    def test_missing_or_duplicate_sequences_changed_limits_and_reversed_counters_are_rejected(self):
        for change in (lambda r:r.pop(3),lambda r:r[3].update(sequence=2),
                lambda r:r[3].update(heap_max_bytes=5120*MIB),
                lambda r:r[3].update(container_limit_bytes=6144*MIB),
                lambda r:r[3].update(gc_ms=0),lambda r:r[3].update(elapsed_ms=0),
                lambda r:r[3].update(cgroup_nr_periods=0)):
            rows=deepcopy(self.rows);change(rows)
            with self.subTest(change=change),self.assertRaises(ValueError):self.summary(rows)

    def test_missing_year_finish_final_close_and_repetition_are_rejected_even_if_renumbered(self):
        for kind in ('year_start','year_finish','year_closed','run_finish','process_finish'):
            rows=deepcopy(self.rows);rows.pop(next(i for i,r in enumerate(rows) if r['kind']==kind))
            for i,row in enumerate(rows):row['sequence']=i
            with self.subTest(kind=kind),self.assertRaises(ValueError):self.summary(rows)
        rows=[r for r in self.rows if r['repetition']!=2]
        with self.assertRaises(ValueError):self.summary(rows)

    def test_wrong_year_repetition_and_fake_population_context_are_rejected(self):
        for change in (lambda r:r[2].update(year=2020,model_year=2020),
                lambda r:r[1].update(repetition=2),lambda r:r[0].update(person_count=1000),
                lambda r:r[1].update(model_year=2020),lambda r:r[2].update(population_observed_ms=999999)):
            rows=deepcopy(self.rows);change(rows)
            with self.subTest(change=change),self.assertRaises(ValueError):self.summary(rows)

    def test_missing_optional_counters_remain_unavailable_and_no_extra_fields_enter_evidence(self):
        rows=deepcopy(self.rows)
        for row in rows:
            for key in years.OPTIONAL:row[key]=-1
        result=self.summary(rows)['repetitions'][0]['intervals'][0]
        self.assertIsNone(result['kernel_process_peak_bytes'])
        self.assertIsNone(result['counters']['cgroup_throttled_usec'])
        for key,value in (('owner','secret'),('kind',[]),('heap_used_bytes',True),('working_set_bytes',-1)):
            changed=deepcopy(self.rows);changed[2][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):self.summary(changed)

    def test_unavailable_counter_does_not_hide_a_later_counter_reset(self):
        rows=deepcopy(self.rows);rows[3]['cgroup_cpu_usage_usec']=-1;rows[4]['cgroup_cpu_usage_usec']=0
        with self.assertRaises(ValueError):self.summary(rows)
        prefix=years.append([],rows[:4])
        with self.assertRaises(ValueError):years.append(prefix,rows[4:])

    def test_seed_and_milestone_metadata_must_match_complete_observations(self):
        for changes in (dict(seeds=['606']),dict(seeds=['606','606']),dict(seeds=[606,607]),
                dict(milestones=(2021,2020)),dict(milestones=(2020,)),dict(milestones=(2018,2021)),
                dict(population=True),dict(end_year=2071)):
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.summary(**changes)

    def test_bounded_private_stream_round_trip_never_overwrites_a_file(self):
        path=self.root/'samples.jsonl';years.write_samples(path,self.rows)
        self.assertEqual(years.read_samples(path),self.rows)
        self.assertEqual(path.stat().st_mode&0o777,0o600)
        with self.assertRaises(FileExistsError):years.write_samples(path,self.rows)
        self.assertEqual(years.read_samples(path),self.rows)
        link=self.root/'link';link.symlink_to(path)
        with self.assertRaises(ValueError):years.read_samples(link)

    def test_duplicate_nonfinite_oversized_and_interrupted_streams_are_not_accepted(self):
        path=self.root/'samples.jsonl'
        for body in (b'{"sequence":0,"sequence":1}\n',b'{"sequence":NaN}\n',b'x'*4097):
            path.write_bytes(body)
            with self.assertRaises(ValueError):years.read_samples(path)
        path.unlink()
        with patch.object(years,'MAX_BYTES',1),self.assertRaises(ValueError):years.write_samples(path,self.rows)
        self.assertFalse(path.exists())
        path.write_bytes(b'x'*20)
        with patch.object(years,'MAX_BYTES',10),self.assertRaises(ValueError):years.read_samples(path)
        with patch.object(years,'MAX_ROWS',1),self.assertRaises(ValueError):years.append([],self.rows)


@unittest.skipUnless(shutil.which('javac') and shutil.which('java'),'Java 17+ tools required')
class YearProfileJavaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.temp.cleanup)
        cls.root=Path(cls.temp.name);cls.classes=cls.root/'classes';cls.classes.mkdir()
        notice='/* (C) Copyright 2026, by Ross Richardson\n * Fictional year-probe lifecycle fixture.\n * @author ross richardson\n */\n'
        fixtures={
            'microsim/event/SystemEventType.java':'''package microsim.event;
                public enum SystemEventType { End }''',
            'microsim/engine/SimulationManager.java':'''package microsim.engine;
                public interface SimulationManager {}''',
            'microsim/engine/SimulationEngine.java':'''package microsim.engine;
                import java.util.*; import java.util.function.Consumer;
                import microsim.event.SystemEventType;
                public class SimulationEngine {
                    static SimulationEngine instance; final int run;
                    final List<SimulationManager> models=new ArrayList<>();
                    final List<Consumer<SystemEventType>> listeners=new ArrayList<>();
                    public SimulationEngine(int run, SimulationManager model) {
                        instance=this; this.run=run; models.add(model);
                        listeners.add(event -> { models.clear(); System.out.println("DISPOSED "+run); });
                    }
                    public static SimulationEngine getInstance() { return instance; }
                    public List<SimulationManager> getModelArray() { return models; }
                    public int getCurrentRunNumber() { return run; }
                    public List<Consumer<SystemEventType>> getEngineListeners() { return listeners; }
                    public void end() { for(var listener:listeners) listener.accept(SystemEventType.End); }
                }''',
            'simpaths/model/SimPathsModel.java':'''package simpaths.model;
                import java.util.*; import microsim.engine.SimulationManager;
                public class SimPathsModel implements SimulationManager {
                    final Thread owner=Thread.currentThread(); public int year=2019;
                    void guard() { if(Thread.currentThread()!=owner) throw new AssertionError("Background population access"); }
                    public int getYear() { guard(); return year; }
                    public int getStartYear() { return 2019; } public int getEndYear() { return 2021; }
                    public List<Integer> getPersons() { guard(); return Collections.nCopies(1000+(year-2019)*10,0); }
                    public List<Integer> getBenefitUnits() { guard(); return Collections.nCopies(500,0); }
                    public List<Integer> getHouseholds() { guard(); return Collections.nCopies(400,0); }
                }''',
            'microsim/monitoring/MemoryMonitor.java':'''package microsim.monitoring;
                import java.util.OptionalLong; import java.util.function.Consumer;
                public class MemoryMonitor {
                    public MemoryMonitor(Consumer<String> sink) {}
                    public Snapshot sample() { return new Snapshot(); }
                    public static class Snapshot {
                        public long heapUsedBytes() { return 1000000; }
                        public long heapCommittedBytes() { return 2000000; }
                        public long heapMaxBytes() { return 4294967296L; }
                        public OptionalLong containerLimitBytes() { return OptionalLong.of(5368709120L); }
                        public OptionalLong containerUsedBytes() { return OptionalLong.of(3000000); }
                        public OptionalLong inactiveFileBytes() { return OptionalLong.of(1000000); }
                        public OptionalLong containerWorkingSetBytes() { return OptionalLong.of(2000000); }
                    }
                }''',
            'simpaths/experiment/SimPathsMultiRun.java':'''package simpaths.experiment;
                import microsim.engine.SimulationEngine; import simpaths.model.SimPathsModel;
                public class SimPathsMultiRun {
                    public static void main(String[] args) {
                        if(args.length!=1) throw new AssertionError("Arguments not forwarded");
                        if(args[0].equals("engine-failure")) {
                            // A surviving non-daemon thread reproduces MultiRun's
                            // wait after its separate engine thread has crashed.
                            new Thread(() -> {
                                while(true) try { Thread.sleep(1000); }
                                    catch(InterruptedException error) { return; }
                            }, "fictional-multirun-waiter").start();
                            new Thread(() -> { throw new IllegalStateException("fictional engine failure"); },
                                "fictional-engine").start();
                            return;
                        }
                        for(int rep=1;rep<=2;rep++) {
                            var model=new SimPathsModel(); var engine=new SimulationEngine(rep,model);
                            for(int year=2019;year<=2021;year++) {
                                model.year=year;
                                System.out.println("Starting year "+(args[0].equals("wrong-year")?year+1:year));
                                if(rep==1 && year==2019) try { Thread.sleep(2150); }
                                    catch(InterruptedException error) { throw new AssertionError(error); }
                                System.out.println("Finished year "+year+" (in 0.01 seconds)");
                                model.year++;
                            }
                            engine.end();
                        }
                        if(args[0].equals("fixture-exit")) System.exit(0);
                    }
                }'''}
        paths=[]
        for name,source in fixtures.items():
            path=cls.root/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(notice+source+'\n');paths.append(str(path))
        cls.command=['javac','--release','17','-d',str(cls.classes),*paths,
            str(ROOT/'deploy/acceptance/SimPathsResourceProbe.java'),
            str(ROOT/'deploy/acceptance/SimPathsYearProfileProbe.java')]
        subprocess.run(cls.command,capture_output=True,text=True,check=True,timeout=60)

    def test_real_probe_preserves_native_lines_closes_before_disposal_and_never_reads_population_on_timer(self):
        result=subprocess.run(['java','-cp',str(self.classes),'SimPathsYearProfileProbe','fixture'],
            capture_output=True,text=True,check=True,timeout=30)
        rows=[years.decode(line[len(years.PREFIX):]) for line in result.stdout.splitlines() if line.startswith(years.PREFIX)]
        summary=years.summarise(rows,population=1000,seeds=['606','607'],end_year=2021,milestones=(2020,2021))
        self.assertEqual(len(summary['repetitions']),2)
        self.assertGreaterEqual(summary['repetitions'][0]['timer_samples'],1)
        self.assertEqual(summary['repetitions'][0]['annual'][-1]['person_count_at_close'],1030)
        native=[line for line in result.stdout.splitlines() if not line.startswith('SIMPATHS_')]
        expected=[]
        for rep in (1,2):
            for year in range(2019,2022):expected += [f'Starting year {year}',f'Finished year {year} (in 0.01 seconds)']
            expected.append(f'DISPOSED {rep}')
        self.assertEqual(native,expected)
        self.assertFalse(any('$' in path.name for path in self.classes.glob('*Profile*.class')))

    def test_shutdown_hook_captures_final_phase_when_the_native_driver_exits(self):
        result=subprocess.run(['java','-cp',str(self.classes),'SimPathsYearProfileProbe','fixture-exit'],
            capture_output=True,text=True,check=True,timeout=30)
        rows=[years.decode(line[len(years.PREFIX):]) for line in result.stdout.splitlines() if line.startswith(years.PREFIX)]
        self.assertEqual(rows[-1]['kind'],'process_finish')
        self.assertEqual(sum(row['kind']=='process_finish' for row in rows),1)
        result=years.summarise(rows,population=1000,seeds=['606','607'],end_year=2021,milestones=(2020,2021))
        self.assertIn('finalisation',result['phases'])

    def test_engine_thread_failure_exits_even_when_another_non_daemon_thread_survives(self):
        for wrapper in ('SimPathsResourceProbe','SimPathsYearProfileProbe'):
            with self.subTest(wrapper=wrapper):
                result=subprocess.run(['java','-cp',str(self.classes),wrapper,'engine-failure'],
                    capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,1,result.stderr)
                self.assertIn('Scientific test thread failed: fictional-engine',result.stderr)
                self.assertIn('fictional engine failure',result.stderr)
                if wrapper=='SimPathsYearProfileProbe':
                    rows=[years.decode(line[len(years.PREFIX):]) for line in result.stdout.splitlines() if line.startswith(years.PREFIX)]
                    self.assertEqual(rows[-1]['kind'],'process_finish')
                    with self.assertRaises(ValueError):
                        years.summarise(rows,population=1000,seeds=['606'],end_year=2021,milestones=[2021])

    def test_changed_native_year_protocol_fails_instead_of_reporting_mislabelled_memory(self):
        result=subprocess.run(['java','-cp',str(self.classes),'SimPathsYearProfileProbe','wrong-year'],
            capture_output=True,text=True,timeout=30)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Native year message and model state differ',result.stderr)

    def test_probe_compiles_against_the_current_frozen_model_api_without_running_it(self):
        classes=self.root/'real-api';classes.mkdir()
        result=subprocess.run(['javac','--release','17','-cp',str(ROOT/'multirun.jar'),'-d',str(classes),
            str(ROOT/'deploy/acceptance/SimPathsResourceProbe.java'),
            str(ROOT/'deploy/acceptance/SimPathsYearProfileProbe.java')],capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse(any('$' in path.name for path in classes.rglob('*.class')))


if __name__=='__main__':unittest.main()
