/* (C) Copyright 2026, by Ross Richardson
 * Reopen saved aggregate comparison folders with bounded, checksum-verified reads.
 * No uploads, raw parsing, persistent chart cache or server authority is involved.
 * @author ross richardson
 */
import {validateCatalogue,validateRows} from './section_source.mjs';

export const PACKAGE_FORMAT='simpaths.visualiser.package.v1';
export const EXPERIMENT_PACKAGE_FORMAT='simpaths.visualiser.experiment-package.v1';
const MIB=1024*1024,MANIFEST_LIMIT=MIB,INDEX_LIMIT=16*MIB,VIEW_LIMIT=8*MIB,VIEW_ROWS=20000;
const hash=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const object=(v,fields)=>v&&typeof v==='object'&&!Array.isArray(v)&&
  Object.keys(v).sort().join('|')===[...fields].sort().join('|');
const label=v=>typeof v==='string'&&v.length>0&&v.length<=160&&!/[\u0000-\u001f]/.test(v);
const integer=(v,min,max)=>Number.isSafeInteger(v)&&v>=min&&v<=max;
const receipt=(v,max)=>object(v,['bytes','sha256'])&&integer(v.bytes,1,max)&&hash(v.sha256);
const abort=signal=>{if(signal?.aborted)throw new DOMException('Saved comparison read cancelled.','AbortError');};
async function sha(bytes){
  if(!globalThis.crypto?.subtle)throw Error('This browser cannot verify saved data. Use a current browser over HTTPS or localhost.');
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');
}
async function read(directory,name,maximum,expected,signal){
  abort(signal);
  const file=await (await directory.getFileHandle(name)).getFile();abort(signal);
  if(!integer(file.size,1,maximum)||expected&&file.size!==expected.bytes)throw Error('Saved comparison file exceeds its limit or has changed.');
  const bytes=await file.arrayBuffer();abort(signal);
  if(bytes.byteLength!==file.size||expected&&await sha(bytes)!==expected.sha256)throw Error('Saved comparison checksum does not match.');
  const text=new TextDecoder('utf-8',{fatal:true}).decode(bytes);
  const value=JSON.parse(text);abort(signal);return value;
}

export function validatePackage(value){
  const experiment=value?.format===EXPERIMENT_PACKAGE_FORMAT;
  if(!object(value,['format','catalogue','parts','provenance',...(experiment?['mapping']:[])])||
      ![PACKAGE_FORMAT,EXPERIMENT_PACKAGE_FORMAT].includes(value.format)||
      !Array.isArray(value.parts)||value.parts.length<1||value.parts.length>100)
    throw Error('This folder is not a supported saved comparison. Extract the downloaded ZIP and select its comparison folder.');
  const catalogue=validateCatalogue(value.catalogue),ids=catalogue.configurations.map(c=>c.id),seen=new Set();
  const covered=new Set();
  for(const [ordinal,part] of value.parts.entries()){
    if(!object(part,['directory','publication','index','configurations'])||part.directory!==`parts/${String(ordinal).padStart(3,'0')}`||
        !hash(part.publication)||!receipt(part.index,INDEX_LIMIT)||!Array.isArray(part.configurations)||
        part.configurations.length<1||part.configurations.length>100||new Set(part.configurations).size!==part.configurations.length||
        part.configurations.some(id=>!ids.includes(id))||seen.has(part.directory))throw Error('Invalid saved comparison index.');
    seen.add(part.directory);
    if(!experiment&&ordinal&&JSON.stringify(part.configurations)!==JSON.stringify([ids[0],ids[ordinal+1]]))
      throw Error('Saved comparison alternatives do not match.');
    for(const id of part.configurations)covered.add(id);
  }
  if(!experiment&&value.parts.length===1&&JSON.stringify(value.parts[0].configurations)!==JSON.stringify(ids))
    throw Error('Saved comparison configurations do not match.');
  if(covered.size!==ids.length||!experiment&&value.parts.length>1&&(
      value.parts.length!==ids.length-1||JSON.stringify(value.parts[0].configurations)!==JSON.stringify(ids.slice(0,2))))
    throw Error('Saved comparison is incomplete.');
  if(experiment){
    const mapping=value.mapping;
    if(!Array.isArray(mapping)||mapping.length!==ids.length||mapping.some((m,i)=>
      !object(m,['id','name','role','key','part','paired'])||
      ['id','name','role','key'].some(k=>m[k]!==catalogue.configurations[i][k])||
      !integer(m.part,0,value.parts.length-1)||!value.parts[m.part].configurations.includes(m.id)||
      typeof m.paired!=='boolean'||m.role==='Baseline'&&m.paired)||
      new Set(mapping.map(m=>m.part)).size!==value.parts.length||
      catalogue.comparison_available!==mapping.some(m=>m.paired))throw Error('Saved experiment configurations do not match.');
  }
  const provenance=value.provenance;
  if(!object(provenance,['visualiser_revision','application_sha256','calculation_files','configurations'])||
      provenance.application_sha256!==null&&!hash(provenance.application_sha256)||
      provenance.visualiser_revision!==null&&(typeof provenance.visualiser_revision!=='string'||provenance.visualiser_revision.length>200)||
      !provenance.calculation_files||typeof provenance.calculation_files!=='object'||Array.isArray(provenance.calculation_files)||
      Object.entries(provenance.calculation_files).some(([name,digest])=>!['runner.cjs','calculation.cjs'].includes(name)||!hash(digest))||
      !Array.isArray(provenance.configurations)||provenance.configurations.length!==ids.length||
      provenance.configurations.some((c,i)=>!object(c,['configuration','model','prepared'])||c.configuration!==ids[i]||
        ['model','prepared'].some(k=>typeof c[k]!=='string'||!c[k].length||c[k].length>256||/[\u0000-\u001f]/.test(c[k]))))
    throw Error('Saved comparison provenance is invalid.');
  return value;
}

export class PortableSource{
  constructor(directory,manifest){
    this.directory=directory;this.manifest=manifest;this.catalogue=manifest.catalogue;this.closed=false;
    this.stats={indexReads:0,sectionReads:0};
  }
  static async open(directory,signal){
    const manifest=validatePackage(await read(directory,'manifest.json',MANIFEST_LIMIT,null,signal));
    return new PortableSource(directory,manifest);
  }
  close(){this.closed=true;this.directory=null;}
  async load({variable,stratifier,kind,scenarios},signal){
    abort(signal);if(this.closed)throw new DOMException('Saved comparison closed.','AbortError');
    const configurations=this.catalogue.configurations;
    if(!label(variable)||!label(stratifier)||!['levels','wage_bin','income_bin','pyramid_bin'].includes(kind)||
        !Array.isArray(scenarios)||new Set(scenarios).size!==scenarios.length||
        scenarios.some(s=>!configurations.some(c=>c.key===s)))throw Error('Invalid saved chart selection.');
    if(!this.catalogue.variables.some(v=>v.name===variable))return [];
    const selected=configurations.filter(c=>c.key==='baseline'||scenarios.includes(c.key));
    const rows=[];let bytes=0;
    for(const configuration of selected){
      abort(signal);if(this.closed)throw new DOMException('Saved comparison closed.','AbortError');
      const mapping=this.manifest.mapping?.find(c=>c.id===configuration.id);
      const part=mapping?this.manifest.parts[mapping.part]:this.manifest.parts.find(p=>p.configurations.includes(configuration.id));
      const parts=await this.directory.getDirectoryHandle('parts');
      const directory=await parts.getDirectoryHandle(part.directory.split('/')[1]);
      const index=await read(directory,'index.json',INDEX_LIMIT,part.index,signal);this.stats.indexReads++;
      if(!object(index,['format','publication','catalogue','sections'])||index.format!=='simpaths.visualiser.index.v1'||
          index.publication!==part.publication||!index.sections||typeof index.sections!=='object'||Array.isArray(index.sections)||
          Object.keys(index.sections).length>25000)throw Error('Invalid saved chart index.');
      const metadata=validateCatalogue(index.catalogue);
      if(metadata.publication!==part.publication||JSON.stringify(metadata.configurations.map(c=>c.id))!==JSON.stringify(part.configurations)||
          JSON.stringify(metadata.seeds)!==JSON.stringify(this.catalogue.seeds)||
          !mapping&&(metadata.comparison_available!==this.catalogue.comparison_available||
          metadata.configurations.some(c=>{const global=configurations.find(g=>g.id===c.id);return !global||c.role!==global.role||c.name!==global.name;})))
        throw Error('Saved chart metadata does not match the comparison.');
      const original=metadata.configurations.find(c=>c.id===configuration.id);
      if(mapping&&(!original||original.name!==configuration.name||
          configuration.role==='Baseline'&&original.role!=='Baseline'&&metadata.configurations.length!==1||
          mapping.paired&&(!metadata.comparison_available||original.role!=='Scenario'||
            !metadata.configurations.some(c=>c.role==='Baseline'&&configurations.some(g=>g.role==='Baseline'&&g.id===c.id)))||
          !mapping.paired&&configuration.role==='Scenario'&&metadata.configurations.length!==1))
        throw Error('Saved experiment roles do not match their chart parts.');
      const key=await sha(new TextEncoder().encode(JSON.stringify([configuration.id,variable,stratifier,kind])));
      const entry=index.sections[key];
      if(!entry)continue;
      if(!object(entry,['bytes','sha256','rows'])||!integer(entry.bytes,1,VIEW_LIMIT)||!hash(entry.sha256)||
          !integer(entry.rows,1,VIEW_ROWS)||bytes+entry.bytes>VIEW_LIMIT-65536||rows.length+entry.rows>VIEW_ROWS)
        throw Error('This saved chart is too large. Select fewer scenarios or a smaller population breakdown.');
      const values=await read(directory,key+'.json',VIEW_LIMIT,entry,signal);this.stats.sectionReads++;
      // Index sections use canonical baseline/scenario; global display identities
      // distinguish every alternative when several bounded pairs are combined.
      if(!Array.isArray(values)||values.length!==entry.rows||values.some(r=>r?.scenario!==(original.role==='Baseline'?'baseline':'scenario')))
        throw Error('Saved chart rows do not match their configuration.');
      validateRows(values,{variable,stratifier,kind},{...original,key:original.role==='Baseline'?'baseline':'scenario'});
      for(const row of values){
        const mapped={...row,scenario:configuration.key};
        if(mapping&&!mapping.paired)for(const field of ['paired_mean_delta','paired_lower_ci','paired_upper_ci','paired_n_runs'])delete mapped[field];
        rows.push(mapped);
      }
      bytes+=entry.bytes;
    }
    abort(signal);if(this.closed)throw new DOMException('Saved comparison closed.','AbortError');
    return rows;
  }
}
