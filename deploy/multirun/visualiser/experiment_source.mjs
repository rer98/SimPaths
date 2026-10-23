/* (C) Copyright 2026, by Ross Richardson
 * Follow a stable, authorised experiment while immutable chart parts become ready.
 * Status refreshes never request preparation or recalculate scientific results.
 * @author ross richardson
 */
import {SectionSource,boundedJSON,validateCatalogue} from './section_source.mjs';
const uuid=value=>typeof value==='string'&&/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(value);
const hash=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const text=value=>typeof value==='string'&&value.length>0&&value.length<=200&&!/[\u0000-\u001f]/.test(value);

export function validateExperiment(value,experiment){
  if(!value||value.format!=='simpaths.visualiser.experiment.v1'||value.experiment!==experiment||
      value.url!=='/visualiser/experiment/'+experiment||!Number.isInteger(value.ready)||
      !Number.isInteger(value.total)||value.ready<0||value.ready>value.total||value.total>100||
      !(value.publication===null||hash(value.publication))||typeof value.pending!=='boolean'||
      typeof value.retryable!=='boolean'||!Array.isArray(value.configurations)||value.configurations.length!==value.total)
    throw Error('Experiment chart status is unavailable.');
  const ids=new Set();
  for(const config of value.configurations){
    if(!uuid(config.id)||ids.has(config.id)||!text(config.configuration)||!text(config.name)||
        !text(config.state)||!text(config.charts)||!text(config.comparison))throw Error('Experiment chart status is unavailable.');
    ids.add(config.id);
  }
  if(value.ready!==value.configurations.filter(c=>c.charts==='ready').length||!!value.ready!==!!value.publication)
    throw Error('Experiment chart status is inconsistent.');
  return value;
}

export class ExperimentSource extends SectionSource{
  constructor(experiment,{onUpdated=()=>{},onStatus=()=>{},...options}={}){
    if(!uuid(experiment))throw Error('Invalid experiment.');
    super(experiment.replaceAll('-','').repeat(2),options);
    this.experiment=experiment;this.url='/api/visualiser-experiments/'+experiment;
    this.pinPublication=true;this.status=null;this.onUpdated=onUpdated;this.onStatus=onStatus;
    this.refreshing=null;
  }
  async refresh(signal){
    if(this.closed||signal?.aborted)throw new DOMException('Chart source closed.','AbortError');
    if(!this.refreshing){
      // A cancelled chart must not cancel another chart or the page's status
      // refresh. The shared read belongs to this source, not its first caller.
      const controller=new AbortController();this.refreshAbort=controller;
      const promise=this.refreshOnce(controller.signal).finally(()=>{
        if(this.refreshing===promise){this.refreshing=null;this.refreshAbort=null;}
      });this.refreshing=promise;
    }
    if(!signal)return this.refreshing;
    const promise=this.refreshing;
    return new Promise((resolve,reject)=>{
      const cancelled=()=>reject(new DOMException('Chart request cancelled.','AbortError'));
      signal.addEventListener('abort',cancelled,{once:true});
      const finish=(callback,value)=>{signal.removeEventListener('abort',cancelled);callback(value);};
      promise.then(value=>finish(resolve,value),error=>finish(reject,error));
    });
  }
  close(){this.refreshAbort?.abort();super.close();}
  async refreshOnce(signal,retry=true){
    try{
      const response=await this.fetcher(this.url,{credentials:'same-origin',cache:'no-store',signal});
      const {value,bytes}=await boundedJSON(response,256*1024,signal);
      const status=validateExperiment(value,this.experiment);this.stats.bytes+=bytes;
      if(this.closed||signal?.aborted)throw new DOMException('Chart source closed.','AbortError');
      const scope=response.headers?.get('x-visualiser-cache-scope');
      if(this.scope&&scope!==this.scope){
        await this.clear();this.invalidate();throw Error('Your sign-in has changed. Return to Results and reopen the Visualiser.');
      }
      this.scope=hash(scope)?scope:null;
      this.diskActive=!!(this.scope&&this.disk&&await this.disk.activate(this.scope));
      const previous=this.status;this.status=status;this.onStatus(status,previous);
      if(!status.publication){
        if(this.catalogue){await this.clear();this.catalogue=null;this.onUpdated(null);}
        return null;
      }
      if(this.catalogue?.publication===status.publication)return this.catalogue;
      const result=await this.fetcher(this.url+'/catalogue?publication='+status.publication,
        {credentials:'same-origin',cache:'no-store',signal});
      const parsed=await boundedJSON(result,512*1024,signal),catalogue=validateCatalogue(parsed.value);
      if(catalogue.publication!==status.publication)throw Error('Experiment chart metadata changed.');
      if(this.closed||signal?.aborted)throw new DOMException('Chart source closed.','AbortError');
      if(this.catalogue)await this.clear();
      this.catalogue=catalogue;this.stats.catalogueRequests++;this.stats.bytes+=parsed.bytes;
      this.onUpdated(catalogue);return catalogue;
    }catch(error){
      if(error.status===409&&retry)return this.refreshOnce(signal,false);
      if([401,403,404].includes(error.status))this.invalidate();
      throw error;
    }
  }
  async metadata(signal){
    const catalogue=await this.refresh(signal);
    if(!catalogue)throw Error('Charts will appear here as configuration preparation finishes.');
    return catalogue;
  }
  async load(selection,signal){
    const previous=this.catalogue?.publication;
    try{return await super.load(selection,signal);}
    catch(error){
      if(error.status!==409)throw error;
      await this.refresh(signal);
      if(this.catalogue?.publication===previous)throw error;
      return super.load(selection,signal);
    }
  }
}
