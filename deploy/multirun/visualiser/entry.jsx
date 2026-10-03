/* (C) Copyright 2026, by Ross Richardson
 * Data-source adapter for the maintained Policy Impacts Visualiser application.
 * VM data is authenticated aggregates; selected local files stay in the browser.
 * @author ross richardson
 */
import React,{useEffect,useState,useRef,useMemo} from 'react';
import {createRoot} from 'react-dom/client';
import App from './App';
import {parseLocalFolder} from './localFolderParser';
import {displayedComparison} from './comparison_data.mjs';

function Page(){
  const key=location.pathname.split('/').pop();
  const [result,setResult]=useState(null),[local,setLocal]=useState(null);
  const [message,setMessage]=useState('Loading online results…');
  const [processing,setProcessing]=useState(false);
  const [scenario,setScenario]=useState(()=>new URLSearchParams(location.search).get('scenario')||'');
  const generation=useRef(0);
  async function vm(){
    const serial=++generation.current;setLocal(null);setResult(null);setProcessing(false);setMessage('Loading online results…');
    try{
      const response=await fetch('/api/visualiser/'+key+'/data',{credentials:'same-origin',cache:'no-store'});
      const data=await response.json();
      if(!response.ok)throw Error(data.error||'Online results are unavailable.');
      if(serial!==generation.current)return;
      const selected=displayedComparison(data,scenario);
      setScenario(selected.selected);
      setResult(data);setMessage('');
    }catch(error){
      if(serial===generation.current)setMessage(error.message);
    }
  }
  useEffect(()=>{vm();return()=>{generation.current++;};},[]);
  async function localFiles(){
    if(!window.showDirectoryPicker){setMessage('This browser does not support local folder selection. Use a compatible browser.');return;}
    const serial=++generation.current;setResult(null);setLocal(null);setProcessing(true);setMessage('Selecting locally saved data…');
    try{
      const directory=await window.showDirectoryPicker();
      const rows=await parseLocalFolder(directory,text=>{if(serial===generation.current)setMessage(text);});
      if(serial===generation.current){setLocal(rows);setMessage('');}
    }catch(error){if(serial===generation.current)setMessage(error.name==='AbortError'?'Local selection cancelled.':error.message);}
    finally{if(serial===generation.current)setProcessing(false);}
  }
  const comparison=useMemo(()=>displayedComparison(result,scenario),[result,scenario]);
  const data=local||comparison.rows;
  const rows=useMemo(()=>data.map(row=>Object.fromEntries(Object.entries(row).map(([k,v])=>
    [k,v===null?NaN:v]))),[data]);
  const controls=<>
    <p className="vm-source-help">View your selected online results, or select a local parent folder with runs in Baseline and Scenario subfolders.</p>
    <button className="vm-source-button" onClick={vm}>View Online Results</button>
    <button className="vm-source-button" onClick={localFiles} disabled={processing}>
      Visualise Locally Saved Data</button>
    <p className="vm-source-help">Local files are processed in this browser and are never uploaded.</p>
    {message&&<p className="vm-source-message" role="status">{message} <a href="/">Return to SimPaths Online</a></p>}
    {result&&comparison.alternatives.length>0&&<label className="vm-source-help">
      Alternative scenario
      <select className="vm-scenario-select" aria-label="Alternative scenario" value={comparison.selected}
        onChange={event=>{const id=event.target.value;setScenario(id);
          history.replaceState(null,'',location.pathname+'?scenario='+encodeURIComponent(id));}}>
        {comparison.alternatives.map(c=><option key={c.id} value={c.id}>{c.name||'Scenario'}</option>)}
      </select>
      <p>Current charts show the baseline and one selected alternative. All {comparison.alternatives.length} alternatives are retained in this comparison set.</p>
    </label>}
    {data.length>0&&<section className="vm-source" aria-label="Displayed data source">
      <strong>Data source: {local?'locally saved data':'Online results'}</strong>
      {result&&comparison.configurations.map(c=><p key={c.id}>{c.role}{c.name?' - '+c.name:''}</p>)}
    </section>}
  </>;
  return <App dataSource={{rows,controls,kind:local?'local':'vm-'+comparison.selected,vmMode:!local,notice:result?.data.notice,
    comparison:result?.comparison,configurations:result?.configurations,series:result?.data.series}}/>;
}
createRoot(document.getElementById('root')).render(<Page/>);
