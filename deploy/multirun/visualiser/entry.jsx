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
  const generation=useRef(0);
  async function vm(){
    const serial=++generation.current;setLocal(null);setResult(null);setProcessing(false);setMessage('Loading online results…');
    try{
      const response=await fetch('/api/visualiser/'+key+'/data',{credentials:'same-origin',cache:'no-store'});
      const data=await response.json();
      if(!response.ok)throw Error(data.error||'Online results are unavailable.');
      if(serial!==generation.current)return;
      displayedComparison(data);
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
  const comparison=useMemo(()=>displayedComparison(result),[result]);
  const data=local||comparison.rows;
  const rows=useMemo(()=>data.map(row=>Object.fromEntries(Object.entries(row).map(([k,v])=>
    [k,v===null?NaN:v]))),[data]);
  const controls=<>
    <p className="vm-source-help">View your selected online results, or select a local parent folder with a Baseline folder and one folder for each alternative scenario.</p>
    <button className="vm-source-button" onClick={vm}>View Online Results</button>
    <button className="vm-source-button" onClick={localFiles} disabled={processing}>
      Visualise Locally Saved Data</button>
    <p className="vm-source-help">Local files are processed in this browser and are never uploaded.</p>
    {message&&<p className="vm-source-message" role="status">{message} <a href="/">Return to SimPaths Online</a></p>}
    {result&&comparison.alternatives.length>0&&<p className="vm-source-help">
      The charts include the baseline and all {comparison.alternatives.length} selected alternatives. Use the scenario buttons to show or hide individual alternatives.
    </p>}
  </>;
  return <App dataSource={{rows,controls,key:local?'local':key,
    label:local?'locally saved data':'Online results',names:local?undefined:comparison.names,
    description:'Online results are aggregated on the server. Locally saved files are processed in this browser.',
    navigation:<a className="vm-return" href="/">Return to SimPaths Online</a>,
    showDelta:local!=null||result?.data.comparison_available===true,notice:result?.data.notice}}/>;
}
createRoot(document.getElementById('root')).render(<Page/>);
