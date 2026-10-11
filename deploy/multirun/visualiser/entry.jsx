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

function Page(){
  const key=location.pathname.split('/').pop();
  const [catalogue,setCatalogue]=useState(null),[local,setLocal]=useState(null);
  const [message,setMessage]=useState('Loading online results…');
  const [processing,setProcessing]=useState(false);
  const generation=useRef(0),client=useRef(null),pending=useRef(null);
  function cancel(){pending.current?.abort();client.current?.close();client.current=null;}
  async function vm(){
    cancel();const serial=++generation.current,abort=new AbortController();pending.current=abort;
    setLocal(null);setCatalogue(null);setProcessing(false);setMessage('Loading online results…');
    try{
      const source=new SectionSource(key);client.current=source;
      const value=await source.metadata(abort.signal);
      if(serial!==generation.current)return;
      setCatalogue(value);setMessage('');
    }catch(error){if(serial===generation.current&&error.name!=='AbortError')setMessage(error.message);}
  }
  useEffect(()=>{vm();return()=>{generation.current++;cancel();};},[]);
  async function localFiles(){
    if(!window.showDirectoryPicker){setMessage('This browser does not support local folder selection. Use a compatible browser.');return;}
    cancel();const serial=++generation.current;setCatalogue(null);setLocal(null);setProcessing(true);setMessage('Selecting locally saved data…');
    try{
      const directory=await window.showDirectoryPicker();
      const rows=await parseLocalFolder(directory,text=>{if(serial===generation.current)setMessage(text);});
      if(serial===generation.current){setLocal(rows);setMessage('');}
    }catch(error){if(serial===generation.current)setMessage(error.name==='AbortError'?'Local selection cancelled.':error.message);}
    finally{if(serial===generation.current)setProcessing(false);}
  }
  const names=useMemo(()=>Object.fromEntries((catalogue?.configurations||[]).map(c=>[c.key,c.name])),[catalogue]);
  const viewSource=useMemo(()=>catalogue?{
    scenarioNames:catalogue.configurations.filter(c=>c.role==='Scenario').map(c=>c.key),
    variables:catalogue.variables,load:client.current.load.bind(client.current),
  }:undefined,[catalogue]);
  const alternatives=catalogue?.configurations.filter(c=>c.role==='Scenario').length||0;
  const controls=<>
    <p className="vm-source-help">View your selected online results, or select a local parent folder with a Baseline folder and one folder for each alternative scenario.</p>
    <button className="vm-source-button" onClick={vm}>View Online Results</button>
    <button className="vm-source-button" onClick={localFiles} disabled={processing}>Visualise Locally Saved Data</button>
    <p className="vm-source-help">Local files are processed in this browser and are never uploaded.</p>
    {message&&<p className="vm-source-message" role="status">{message} <a href="/">Return to SimPaths Online</a></p>}
    {alternatives>0&&<p className="vm-source-help">Use the scenario buttons to show or hide any of the {alternatives} selected alternatives. Online charts load the data for your current selection.</p>}
  </>;
  return <App dataSource={{rows:local||[],viewSource,controls,key:local?'local':key,
    label:local?'locally saved data':'Online results',names:local?undefined:names,
    description:'Online results are aggregated on the server. Locally saved files are processed in this browser.',
    navigation:<a className="vm-return" href="/">Return to SimPaths Online</a>,
    showDelta:local!=null||catalogue?.comparison_available===true,notice:catalogue?.notice}}/>;
}
createRoot(document.getElementById('root')).render(<Page/>);
