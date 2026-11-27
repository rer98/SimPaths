/* (C) Copyright 2026, by Ross Richardson
 * Connect the maintained Visualiser to bounded, authenticated chart sections.
 * VM calculations remain server-side; selected local files stay in the browser.
 * @author ross richardson
 */
import React,{useEffect,useState,useRef,useMemo} from 'react';
import {createRoot} from 'react-dom/client';
import App from './App';
import HistoricalApp from './historical-reader';
import {parseLocalFolder} from './localFolderParser';
import {boundedJSON,SectionSource} from './section_source.mjs';
import {ExperimentSource,preparationLabel} from './experiment_source.mjs';
import {PortableSource} from './portable_source.mjs';
import {ExperimentChooser} from './experiment_chooser';

const experimentComplete=status=>status?.total>0&&status.ready===status.total&&
  status.configurations.every(config=>config.charts==='ready'&&['ready','not_applicable'].includes(config.comparison));

function CalculationNotice({info,directKey,onInvalidated}){
  const [direct,setDirect]=useState(null),[sending,setSending]=useState(false),[feedback,setFeedback]=useState('');
  const action=useRef(null),invalidated=useRef(onInvalidated);
  invalidated.current=onInvalidated;
  useEffect(()=>()=>action.current?.abort(),[]);
  useEffect(()=>{
    if(!directKey)return;
    let timer;
    const controller=new AbortController();
    async function observe(){
      try{
        const response=await window.fetch('/api/visualiser/'+directKey,{credentials:'same-origin',cache:'no-store',signal:controller.signal});
        const {value}=await boundedJSON(response,65536,controller.signal);
        setDirect(value.recalculation);
      }catch(error){
        if(controller.signal.aborted)return;
        setDirect(null);
        if([401,403,404].includes(error.status)){invalidated.current();return;}
      }
      timer=setTimeout(observe,document.hidden?60000:15000);
    }
    observe();
    return ()=>{controller.abort();clearTimeout(timer);};
  },[directKey]);
  const data=directKey?direct:info;
  async function recalculate(){
    if(action.current||!data?.available)return;
    const controller=new AbortController();action.current=controller;
    const signal=controller.signal;
    setSending(true);setFeedback('');
    try{
      const session=await boundedJSON(await window.fetch('/api/session',{credentials:'same-origin',cache:'no-store',signal}),65536,signal);
      if(!session.value.signed_in)throw Object.assign(Error('Sign in again from My Jobs.'),{status:403});
      const endpoint=directKey?'/api/visualiser/'+directKey:'/api/visualiser-experiments/'+data.experiment;
      const response=await window.fetch(endpoint+'/recalculate',{
        method:'POST',credentials:'same-origin',cache:'no-store',signal,
        headers:{'Content-Type':'application/json','X-CSRF-Token':session.value.csrf},
        body:JSON.stringify(directKey?{}:{configurations:data.configurations,baseline:data.baseline})});
      await boundedJSON(response,65536,signal);
      setFeedback('Recalculation requested. You can leave this page while your charts are prepared.');
    }catch(error){
      if(signal.aborted)return;
      if([401,403,404].includes(error.status)){invalidated.current();return;}
      setFeedback(error.message);
    }finally{if(!signal.aborted)setSending(false);action.current=null;}
  }
  return <aside className="vm-calculation-notice" aria-label="Calculation method">
    <strong>These charts use an earlier calculation method.</strong>{' '}
    This experiment’s summaries were prepared with that method. Results calculated with the current method may differ.
    {data?.deleted>0&&(data.deleted===data.configurations.length
      ?' The raw simulation outputs have been deleted, so these summaries can no longer be recalculated.'
      :' Raw simulation outputs have been deleted for some selected configurations, so the full selection cannot be recalculated.')}
    {data?.state==='ready'&&directKey&&/^\/visualiser\/[a-f0-9]{64}$/.test(data.url)&&<p><a href={data.url}>Open recalculated charts</a></p>}
    {data?.available&&!['pending','ready'].includes(data.state)&&<p><button className="vm-source-button" disabled={sending}
      onClick={recalculate}>Recalculate with the latest method</button></p>}
    {data?.state==='retry'&&<p role="status">Recalculation is incomplete. You can retry while the raw outputs are available.</p>}
    <div role="status">{data?.state==='pending'?'Recalculation is in progress. You can leave this page.':feedback}</div>
  </aside>;
}

function Page({selection=null,receipt=null,onChoose=()=>{}}){
  const key=location.pathname.split('/').pop();
  const experiment=/^\/visualiser\/experiment\/[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(location.pathname);
  const onlineKey=experiment||/^[a-f0-9]{64}$/.test(key);
  const [catalogue,setCatalogue]=useState(null),[local,setLocal]=useState(null);
  const [mode,setMode]=useState('online');
  const [message,setMessage]=useState('Loading online results…');
  const [processing,setProcessing]=useState(false);
  const [connectionIssue,setConnectionIssue]=useState(null),[reload,setReload]=useState(0);
  const [experimentStatus,setExperimentStatus]=useState(null),[updateNotice,setUpdateNotice]=useState('');
  const generation=useRef(0),client=useRef(null),pending=useRef(null);
  const refreshTimer=useRef(null);
  function cancel(){clearTimeout(refreshTimer.current);pending.current?.abort();client.current?.close();client.current=null;}
  function onlineError(error,source,chart=false){
    if(error.name==='AbortError'||source!==client.current)return;
    if(source.closed||[401,403,404].includes(error.status)){
      setConnectionIssue(null);setMessage(error.message);
    }else{setMessage('');setConnectionIssue({message:error.message,chart});}
  }
  async function vm(){
    cancel();const serial=++generation.current,abort=new AbortController();pending.current=abort;
    setMode('online');setConnectionIssue(null);
    setLocal(null);setCatalogue(null);setExperimentStatus(null);setUpdateNotice('');setProcessing(false);setMessage('Loading online results…');
    if(!onlineKey){setMessage('Open a saved comparison, or select locally saved simulation files.');return;}
    let refresh;
    try{
      const options={onInvalidated:text=>{
        if(serial!==generation.current)return;
        setCatalogue(null);setExperimentStatus(null);setUpdateNotice('');setConnectionIssue(null);setMessage(text);
      }};
      const source=experiment?new ExperimentSource(key,{...options,selection,onChooseRequired:onChoose,
        onUpdated:value=>{if(serial===generation.current)setCatalogue(value);},
        onStatus:(value,previous)=>{
          if(serial!==generation.current)return;
          setExperimentStatus(value);
          if(experimentComplete(value))setUpdateNotice('');
          else if(previous){
            const before=new Map(previous.configurations.map(c=>[c.id,c]));
            const charts=value.configurations.filter(c=>c.charts==='ready'&&before.get(c.id)?.charts!=='ready');
            const pairs=value.configurations.filter(c=>c.comparison==='ready'&&before.get(c.id)?.comparison!=='ready');
            if(charts.length)setUpdateNotice('Charts now available: '+charts.map(c=>c.name).join(', ')+'.');
            else if(pairs.length)setUpdateNotice('Baseline comparisons now available: '+pairs.map(c=>c.name).join(', ')+'.');
          }
        }}):new SectionSource(key,options);
      client.current=source;
      refresh=async()=>{
        if(serial!==generation.current||source.closed)return;
        try{
          const value=await source.refresh(abort.signal);
          if(serial===generation.current){
            setMessage(value?'':'Charts will appear here as configuration preparation finishes.');
            setConnectionIssue(issue=>issue?.chart?issue:null);
          }
        }catch(error){if(serial===generation.current)onlineError(error,source);}
        if(serial===generation.current&&!source.closed)
          refreshTimer.current=setTimeout(refresh,document.hidden?60000:15000);
      };
      const value=experiment?await source.refresh(abort.signal):await source.metadata(abort.signal);
      if(serial!==generation.current)return;
      setCatalogue(value);setMessage(value?'':'Charts will appear here as configuration preparation finishes.');
    }catch(error){if(serial===generation.current){
      if(client.current)onlineError(error,client.current);
      else if(error.name!=='AbortError')setMessage(error.message);
    }}
    if(experiment&&refresh&&serial===generation.current&&!client.current?.closed)
      refreshTimer.current=setTimeout(refresh,document.hidden?60000:15000);
  }
  async function retryOnline(clear=false){
    const source=client.current,serial=generation.current;
    if(!source||source.closed){await vm();return;}
    setProcessing(true);setConnectionIssue(null);
    setMessage(clear?'Reloading charts from server…':'Retrying connection…');
    try{
      if(clear)await source.clear();
      const value=experiment?await source.refresh(pending.current?.signal):await source.metadata(pending.current?.signal);
      if(serial!==generation.current||source!==client.current)return;
      setCatalogue(value);setReload(value=>value+1);
      setMessage(value?'':'Charts will appear here as configuration preparation finishes.');
    }catch(error){if(serial===generation.current){setMessage('');onlineError(error,source);}}
    finally{if(serial===generation.current)setProcessing(false);}
  }
  useEffect(()=>{vm();return()=>{generation.current++;cancel();};},[]);
  async function localFiles(){
    if(!window.showDirectoryPicker){setMessage('This browser does not support local folder selection. Use a compatible browser.');return;}
    let serial=generation.current;setProcessing(true);
    try{
      const directory=await window.showDirectoryPicker();
      if(serial!==generation.current)return;
      cancel();serial=++generation.current;setCatalogue(null);setLocal(null);setMessage('Reading locally saved data…');
      setMode('raw');setConnectionIssue(null);
      const rows=await parseLocalFolder(directory,text=>{if(serial===generation.current)setMessage(text);});
      if(serial===generation.current){setLocal(rows);setMessage('');}
    }catch(error){if(serial===generation.current&&error.name!=='AbortError')setMessage(error.message);}
    finally{if(serial===generation.current)setProcessing(false);}
  }
  async function savedComparison(){
    if(!window.showDirectoryPicker){setMessage('This browser does not support local folder selection. Use a compatible browser.');return;}
    let serial=generation.current;setProcessing(true);
    try{
      const directory=await window.showDirectoryPicker();
      if(serial!==generation.current)return;
      cancel();serial=++generation.current;const abort=new AbortController();pending.current=abort;
      setMode('saved');setConnectionIssue(null);setCatalogue(null);setLocal(null);setMessage('Reading saved comparison…');
      const source=await PortableSource.open(directory,abort.signal);
      if(serial!==generation.current){source.close();return;}
      client.current=source;setCatalogue(source.catalogue);setMessage('');
    }catch(error){if(serial===generation.current&&error.name!=='AbortError')setMessage(error.message);}
    finally{if(serial===generation.current)setProcessing(false);}
  }
  const names=useMemo(()=>Object.fromEntries((catalogue?.configurations||[]).map(c=>[c.key,c.name])),[catalogue]);
  const viewSource=useMemo(()=>{const source=client.current;return catalogue?{
    scenarioNames:catalogue.configurations.filter(c=>c.role==='Scenario').map(c=>c.key),
    variables:catalogue.variables,load:async(selection,signal)=>{
      try{
        const rows=await source.load(selection,signal);
        if(source===client.current&&mode==='online')setConnectionIssue(issue=>issue?.chart?null:issue);
        return rows;
      }catch(error){if(mode==='online')onlineError(error,source,true);throw error;}
    },
    hint:client.current.hint?.bind(client.current),cancelHint:client.current.cancelHint?.bind(client.current),
  }:undefined;},[catalogue,mode,reload]);
  const alternatives=catalogue?.configurations.filter(c=>c.role==='Scenario').length||0;
  const controls=<>
    <p className="vm-source-help">{experiment?'Scenarios appear as their charts become ready. Scenarios you hide stay hidden. Paired differences appear when the baseline comparison is ready.':'View your selected online results, or select a local parent folder with a Baseline folder and one folder for each alternative scenario.'}</p>
    {onlineKey&&mode!=='online'&&<button className="vm-source-button" onClick={vm}>View Online Results</button>}
    {mode==='online'&&selection&&receipt&&!experimentStatus?.configurations.some(c=>c.charts==='incompatible'||c.comparison==='incompatible')&&!experimentComplete(experimentStatus)&&<p role="status">
      Your chart preparation continues if you leave this page.
      {receipt.email&&' We’ll email you when your selection is ready.'}
    </p>}
    {mode==='online'&&experimentStatus&&!experimentComplete(experimentStatus)&&<details open={!!selection} className="vm-experiment-status">
      <summary>{experimentStatus.ready} of {experimentStatus.total} configurations have charts ready</summary>
      {selection?<ul>{experimentStatus.configurations.map(c=><li key={c.id}>{c.name}: {preparationLabel(c,false,!!receipt)}
        {c.comparison!=='not_applicable'&&' · '+preparationLabel(c,true,!!receipt)}</li>)}</ul>:<ul>{experimentStatus.configurations.map(c=><li key={c.id}>{c.name}: {c.charts==='ready'?'Charts ready':
        c.charts==='incompatible'?preparationLabel(c):c.charts==='queued'?'Waiting for chart preparation':
        c.state==='active'?(c.phase==='validating'?'Validating outputs':'Simulation running'):c.state==='queued'?'Simulation queued':
        c.state==='cancelling'?'Stopping':
        c.state==='review'&&c.validation?'Output validation needs review':
        c.charts==='processing'?'Preparing charts':'Charts not yet available'}
        {c.charts==='ready'&&c.comparison!=='not_applicable'&&(c.comparison==='ready'?' · Baseline comparison ready':
          c.comparison==='incompatible'?' · '+preparationLabel(c,true):c.comparison==='waiting_baseline'?' · Waiting for baseline comparison':
          c.comparison==='processing'?' · Preparing baseline comparison':' · Baseline comparison not yet ready')}</li>)}</ul>}
    </details>}
    {mode==='online'&&!experimentComplete(experimentStatus)&&updateNotice&&<p className="vm-source-help" role="status">{updateNotice}</p>}
    {experiment&&mode==='online'&&<button className="vm-source-button" onClick={onChoose}>Change configurations</button>}
    {mode==='online'&&catalogue&&<a className="vm-source-button" href={experiment?client.current.archiveURL:'/api/visualiser/'+key+'/archive'} target="_blank" rel="noopener">Download comparison data</a>}
    <button className="vm-source-button" onClick={savedComparison} disabled={processing}>Open saved comparison</button>
    <button className="vm-source-button" onClick={localFiles} disabled={processing}>Visualise Locally Saved Data</button>
    <p className="vm-source-help">To reopen a downloaded comparison, extract its ZIP and select the folder containing manifest.json. All available variables and selected scenarios are included in the download.</p>
    {mode==='online'&&connectionIssue&&<p className="vm-source-message" role="status">{connectionIssue.message} <button className="vm-source-button" onClick={()=>retryOnline()} disabled={processing}>Retry connection</button></p>}
    {mode==='online'&&onlineKey&&<details className="vm-troubleshooting"><summary>Troubleshooting</summary>
      <p className="vm-source-help">Recently viewed online charts are cached on this device for up to 12 hours. If charts appear incomplete or outdated, clear this comparison’s cached chart data and fetch it again. Server results and downloaded files are unchanged.</p>
      <button className="vm-source-button" onClick={()=>retryOnline(true)} disabled={processing}>Reload charts from server</button>
    </details>}
    {message&&<p className="vm-source-message" role="status">{message} <a href="/">Return to SimPaths Online</a></p>}
    {alternatives>0&&<p className="vm-source-help">Use the scenario buttons to show or hide any of the {alternatives} selected alternatives. Charts load the data for your current selection.</p>}
  </>;
  if(experiment&&!selection&&mode==='online'&&!experimentStatus&&message==='Loading online results…'&&!connectionIssue)
    return <main className="vm-chooser"><p role="status">{message}</p></main>;
  const archivedRevision='f6384fa6ea856802dadd7a115aca02c719142bdc';
  const savedFiles=mode==='saved'?client.current?.manifest?.provenance?.calculation_files:null;
  const archivedRunner=savedFiles?.['runner.cjs']==='2efdc92bc9f96f8cce07c9fbe885a7ec26fb129d2bcf1e095b2b6a45cdcc6b32';
  const archivedCalculation=savedFiles?.['calculation.cjs']==='20bf2115262bf16cdd2aee537eaf4d25ddbdc5a02bd6ec803f5152499ba734f5';
  const historical=catalogue?.calculator_revision===archivedRevision || archivedRunner&&archivedCalculation;
  if(historical&&!HistoricalApp || savedFiles&&(historical||archivedCalculation)&&!(archivedRunner&&archivedCalculation))
    return <main><p>This saved comparison needs a reader that matches its calculator.</p>
      <a href="/">Return to SimPaths Online</a></main>;
  const Reader=historical?HistoricalApp:App;
  return <>{historical&&<CalculationNotice info={mode==='online'?experimentStatus?.recalculation:null}
    directKey={mode==='online'&&!experiment?key:null} onInvalidated={()=>{cancel();setCatalogue(null);setExperimentStatus(null);setMessage('Return to My Jobs and sign in again to open these results.');}}/>}
    <Reader dataSource={{rows:local||[],viewSource,controls,key:local?'local':key,
    label:mode==='saved'?'saved comparison':local?'locally saved data':'Online results',names:local?undefined:names,
    description:mode==='saved'?'Saved comparison sections are read on this device and are never uploaded.':'Online results are produced on the remote server. Locally saved files are processed entirely in your browser and are not uploaded.',
    navigation:<a className="vm-return" href="/">Return to SimPaths Online</a>,
    showDelta:local!=null||catalogue?.comparison_available===true,notice:catalogue?.notice}}/></>;
}
function ExperimentPage(){
  const [choice,setChoice]=useState(null),[receipt,setReceipt]=useState(null);
  const [choosing,setChoosing]=useState(()=>new URLSearchParams(location.search).has('chosen'));
  const forceChooser=useRef(false);
  const experiment=location.pathname.split('/').pop();
  return choosing?<ExperimentChooser experiment={experiment} current={choice} force={forceChooser.current} onOpen={(selected,confirmation=null)=>{
    setChoice(selected);setReceipt(confirmation);setChoosing(false);
  }}/>:<Page selection={choice} receipt={receipt} onChoose={()=>{
    // Keep the chosen members in memory, but make the URL reopen the chooser.
    history.replaceState(null,'',location.pathname);forceChooser.current=true;setChoosing(true);
  }}/>;
}
createRoot(document.getElementById('root')).render(location.pathname.startsWith('/visualiser/experiment/')?<ExperimentPage/>:<Page/>);
