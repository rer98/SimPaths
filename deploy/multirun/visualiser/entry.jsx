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

function Page(){
  const key=location.pathname.split('/').pop();
  const experiment=/^\/visualiser\/experiment\/[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(location.pathname);
  const onlineKey=experiment||/^[a-f0-9]{64}$/.test(key);
  const [catalogue,setCatalogue]=useState(null),[local,setLocal]=useState(null);
  const [mode,setMode]=useState('online');
  const [message,setMessage]=useState('Loading online results…');
  const [processing,setProcessing]=useState(false);
  const [experimentStatus,setExperimentStatus]=useState(null),[updateNotice,setUpdateNotice]=useState('');
  const generation=useRef(0),client=useRef(null),pending=useRef(null);
  const refreshTimer=useRef(null);
  function cancel(){clearTimeout(refreshTimer.current);pending.current?.abort();client.current?.close();client.current=null;}
  async function vm(){
    cancel();const serial=++generation.current,abort=new AbortController();pending.current=abort;
    setMode('online');
    setLocal(null);setCatalogue(null);setExperimentStatus(null);setUpdateNotice('');setProcessing(false);setMessage('Loading online results…');
    if(!onlineKey){setMessage('Open a saved comparison, or select locally saved simulation files.');return;}
    try{
      const options={onInvalidated:text=>{
        if(serial!==generation.current)return;
        setCatalogue(null);setExperimentStatus(null);setUpdateNotice('');setMessage(text);
      }};
      const source=experiment?new ExperimentSource(key,{...options,
        onUpdated:value=>{if(serial===generation.current)setCatalogue(value);},
        onStatus:(value,previous)=>{
          if(serial!==generation.current)return;
          setExperimentStatus(value);
          if(previous){
            const before=new Map(previous.configurations.map(c=>[c.id,c]));
            const charts=value.configurations.filter(c=>c.charts==='ready'&&before.get(c.id)?.charts!=='ready');
            const pairs=value.configurations.filter(c=>c.comparison==='ready'&&before.get(c.id)?.comparison!=='ready');
            if(charts.length)setUpdateNotice('Charts now available: '+charts.map(c=>c.name).join(', ')+'.');
            else if(pairs.length)setUpdateNotice('Baseline comparisons now available: '+pairs.map(c=>c.name).join(', ')+'.');
          }
        }}):new SectionSource(key,options);
      client.current=source;
      async function refresh(){
        if(serial!==generation.current||source.closed)return;
        try{
          const value=await source.refresh(abort.signal);
          if(serial===generation.current)setMessage(value?'':'Charts will appear here as configuration preparation finishes.');
        }catch(error){if(serial===generation.current&&error.name!=='AbortError')setMessage(error.message);}
        if(serial===generation.current&&!source.closed)
          refreshTimer.current=setTimeout(refresh,document.hidden?60000:15000);
      }
      const value=experiment?await source.refresh(abort.signal):await source.metadata(abort.signal);
      if(serial!==generation.current)return;
      setCatalogue(value);setMessage(value?'':'Charts will appear here as configuration preparation finishes.');
      if(experiment)refreshTimer.current=setTimeout(refresh,document.hidden?60000:15000);
    }catch(error){if(serial===generation.current&&error.name!=='AbortError')setMessage(error.message);}
  }
  useEffect(()=>{vm();return()=>{generation.current++;cancel();};},[]);
  async function localFiles(){
    if(!window.showDirectoryPicker){setMessage('This browser does not support local folder selection. Use a compatible browser.');return;}
    cancel();const serial=++generation.current;setCatalogue(null);setLocal(null);setProcessing(true);setMessage('Selecting locally saved data…');
    setMode('raw');
    try{
      const directory=await window.showDirectoryPicker();
      const rows=await parseLocalFolder(directory,text=>{if(serial===generation.current)setMessage(text);});
      if(serial===generation.current){setLocal(rows);setMessage('');}
    }catch(error){if(serial===generation.current)setMessage(error.name==='AbortError'?'Local selection cancelled.':error.message);}
    finally{if(serial===generation.current)setProcessing(false);}
  }
  async function savedComparison(){
    if(!window.showDirectoryPicker){setMessage('This browser does not support local folder selection. Use a compatible browser.');return;}
    cancel();const serial=++generation.current,abort=new AbortController();pending.current=abort;
    setMode('saved');setCatalogue(null);setLocal(null);setProcessing(true);setMessage('Selecting a saved comparison…');
    try{
      const directory=await window.showDirectoryPicker();
      if(serial!==generation.current)return;
      const source=await PortableSource.open(directory,abort.signal);
      if(serial!==generation.current){source.close();return;}
      client.current=source;setCatalogue(source.catalogue);setMessage('');
    }catch(error){if(serial===generation.current)setMessage(error.name==='AbortError'?'Saved comparison selection cancelled.':error.message);}
    finally{if(serial===generation.current)setProcessing(false);}
  }
  const names=useMemo(()=>Object.fromEntries((catalogue?.configurations||[]).map(c=>[c.key,c.name])),[catalogue]);
  const viewSource=useMemo(()=>catalogue?{
    scenarioNames:catalogue.configurations.filter(c=>c.role==='Scenario').map(c=>c.key),
    variables:catalogue.variables,load:client.current.load.bind(client.current),
    hint:client.current.hint?.bind(client.current),cancelHint:client.current.cancelHint?.bind(client.current),
  }:undefined,[catalogue,mode]);
  const alternatives=catalogue?.configurations.filter(c=>c.role==='Scenario').length||0;
  const controls=<>
    <p className="vm-source-help">{experiment?'This experiment updates as configurations and baseline comparisons become ready. Choose the scenarios to display; new results do not change your selections.':'View your selected online results, or select a local parent folder with a Baseline folder and one folder for each alternative scenario.'}</p>
    {onlineKey&&<button className="vm-source-button" onClick={vm}>View Online Results</button>}
    {mode==='online'&&catalogue&&<a className="vm-source-button" href={(experiment?'/api/visualiser-experiments/':'/api/visualiser/')+key+'/archive'} target="_blank" rel="noopener">Download comparison data</a>}
    {mode==='online'&&experimentStatus&&<details className="vm-experiment-status">
      <summary>{experimentStatus.ready} of {experimentStatus.total} configurations have charts ready</summary>
      <ul>{experimentStatus.configurations.map(c=><li key={c.id}>{c.name}: {c.charts==='ready'?'Charts ready':
        c.state==='active'?'Simulation running':c.state==='queued'?'Simulation queued':
        ['queued','processing'].includes(c.charts)?'Preparing charts':'Charts not yet available'}
        {c.charts==='ready'&&c.comparison!=='not_applicable'&&(c.comparison==='ready'?' · Baseline comparison ready':
          c.comparison==='waiting_baseline'?' · Waiting for baseline comparison':' · Baseline comparison not yet ready')}</li>)}</ul>
    </details>}
    {mode==='online'&&updateNotice&&<p className="vm-source-help" role="status">{updateNotice}</p>}
    <button className="vm-source-button" onClick={savedComparison} disabled={processing}>Open saved comparison</button>
    <button className="vm-source-button" onClick={localFiles} disabled={processing}>Visualise Locally Saved Data</button>
    <p className="vm-source-help">To reopen a downloaded comparison, extract its ZIP and select the folder containing manifest.json. All available variables and selected scenarios are included in the download.</p>
    <p className="vm-source-help">Local files are processed in this browser and are never uploaded.</p>
    {mode==='online'&&catalogue&&<p className="vm-source-help">Recently viewed online charts can be reused on this device for up to 12 hours. Access is checked again before they are shown. <button className="vm-source-button" onClick={async()=>{await client.current?.clear();vm();}}>Clear saved data for these charts</button></p>}
    {message&&<p className="vm-source-message" role="status">{message} <a href="/">Return to SimPaths Online</a></p>}
    {alternatives>0&&<p className="vm-source-help">Use the scenario buttons to show or hide any of the {alternatives} selected alternatives. Charts load the data for your current selection.</p>}
  </>;
  return <App dataSource={{rows:local||[],viewSource,controls,key:local?'local':key,
    label:mode==='saved'?'saved comparison':local?'locally saved data':'Online results',names:local?undefined:names,
    description:mode==='saved'?'Saved comparison sections are read on this device and are never uploaded.':'Online results are aggregated on the server. Locally saved files are processed in this browser.',
    navigation:<a className="vm-return" href="/">Return to SimPaths Online</a>,
    showDelta:local!=null||catalogue?.comparison_available===true,notice:catalogue?.notice}}/>;
}
createRoot(document.getElementById('root')).render(<Page/>);
