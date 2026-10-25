/* (C) Copyright 2026, by Ross Richardson
 * Connect the maintained Visualiser to bounded, authenticated chart sections.
 * VM calculations remain server-side; selected local files stay in the browser.
 * @author ross richardson
 */
import React,{useEffect,useState,useRef,useMemo} from 'react';
import {createRoot} from 'react-dom/client';
import App from './App';
import {parseLocalFolder} from './localFolderParser';
import {SectionSource} from './section_source.mjs';
import {ExperimentSource} from './experiment_source.mjs';
import {PortableSource} from './portable_source.mjs';

const experimentComplete=status=>status?.total>0&&status.ready===status.total&&
  status.configurations.every(config=>config.charts==='ready'&&['ready','not_applicable'].includes(config.comparison));

function Page(){
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
      const source=experiment?new ExperimentSource(key,{...options,
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
    <p className="vm-source-help">{experiment?'This experiment updates as configurations and baseline comparisons become ready. Choose the scenarios to display; new results do not change your selections.':'View your selected online results, or select a local parent folder with a Baseline folder and one folder for each alternative scenario.'}</p>
    {onlineKey&&mode!=='online'&&<button className="vm-source-button" onClick={vm}>View Online Results</button>}
    {mode==='online'&&experimentStatus&&!experimentComplete(experimentStatus)&&<details className="vm-experiment-status">
      <summary>{experimentStatus.ready} of {experimentStatus.total} configurations have charts ready</summary>
      <ul>{experimentStatus.configurations.map(c=><li key={c.id}>{c.name}: {c.charts==='ready'?'Charts ready':
        c.state==='active'?'Simulation running':c.state==='queued'?'Simulation queued':
        ['queued','processing'].includes(c.charts)?'Preparing charts':'Charts not yet available'}
        {c.charts==='ready'&&c.comparison!=='not_applicable'&&(c.comparison==='ready'?' · Baseline comparison ready':
          c.comparison==='waiting_baseline'?' · Waiting for baseline comparison':' · Baseline comparison not yet ready')}</li>)}</ul>
    </details>}
    {mode==='online'&&!experimentComplete(experimentStatus)&&updateNotice&&<p className="vm-source-help" role="status">{updateNotice}</p>}
    {mode==='online'&&catalogue&&<a className="vm-source-button" href={(experiment?'/api/visualiser-experiments/':'/api/visualiser/')+key+'/archive'} target="_blank" rel="noopener">Download comparison data</a>}
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
  return <App dataSource={{rows:local||[],viewSource,controls,key:local?'local':key,
    label:mode==='saved'?'saved comparison':local?'locally saved data':'Online results',names:local?undefined:names,
    description:mode==='saved'?'Saved comparison sections are read on this device and are never uploaded.':'Online results are produced on the remote server. Locally saved files are processed entirely in your browser and are not uploaded.',
    navigation:<a className="vm-return" href="/">Return to SimPaths Online</a>,
    showDelta:local!=null||catalogue?.comparison_available===true,notice:catalogue?.notice}}/>;
}
createRoot(document.getElementById('root')).render(<Page/>);
