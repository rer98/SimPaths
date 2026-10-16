/* (C) Copyright 2026, by Ross Richardson
 * Tiny in-memory directory handles for saved-comparison reader and bundle tests.
 * @author ross richardson
 */
import {createHash} from 'node:crypto';
export const digest=value=>createHash('sha256').update(value).digest('hex');
export const bytes=value=>Buffer.from(JSON.stringify(value));
export const row=(scenario,value,variable='Mental Component Summary (MCS)')=>({year:2019,scenario,module:'Health',variable,
  variable_value:'Continuous Mean',stratifier:'Overall',stratifier_value:'Overall',metric_type:'mean',
  n_runs:2,total_sample:200,min_sample:100,mean_sample:100,mean_value:value,sd_value:1,
  lower_ci:value==null?null:value-1,upper_ci:value==null?null:value+1,
  paired_mean_delta:scenario==='baseline'?null:5,paired_lower_ci:scenario==='baseline'?null:3,
  paired_upper_ci:scenario==='baseline'?null:7,paired_n_runs:scenario==='baseline'?0:2});
export function fixture(){
  const configurations=[{id:'base',key:'baseline',role:'Baseline',name:'Reference'},
    {id:'alt1',key:'scenario_1',role:'Scenario',name:'Policy A'},
    {id:'alt2',key:'scenario_2',role:'Scenario',name:'Policy B <img src="unsafe">'}];
  const catalogue={format:'simpaths.visualiser.catalogue.v1',publication:'a'.repeat(64),configurations,seeds:['606','607'],
    notice:'Fictional paired results',comparison_available:true,limits:{response_bytes:8*1024**2,response_rows:20000},
    variables:['Mental Component Summary (MCS)','Physical Component Summary (PCS)'].map(name=>({name,module:'Health',years:[2019],
      views:[{stratifier:'Overall',kind:'levels',bytes:5000,rows:3}]}))};
  const files=new Map(),parts=[];
  for(let i=1;i<=2;i++){
    const configs=[configurations[0],{...configurations[i],key:'scenario_1'}];
    const cat={...catalogue,publication:String(i).repeat(64),configurations:configs};
    const index={format:'simpaths.visualiser.index.v1',publication:cat.publication,catalogue:cat,sections:{}};
    const directory=`parts/${String(i-1).padStart(3,'0')}`;
    for(const config of configs)for(const {name} of catalogue.variables){
      const key=digest(JSON.stringify([config.id,name,'Overall','levels']));
      const data=bytes([row(config.role==='Baseline'?'baseline':'scenario',config.id==='base'?40:45,name)]);
      files.set(directory+'/'+key+'.json',data);index.sections[key]={bytes:data.length,sha256:digest(data),rows:1};
    }
    const data=bytes(index);files.set(directory+'/index.json',data);
    parts.push({directory,publication:cat.publication,index:{bytes:data.length,sha256:digest(data)},configurations:configs.map(c=>c.id)});
  }
  const manifest={format:'simpaths.visualiser.package.v1',catalogue,parts,provenance:{visualiser_revision:'a'.repeat(40),application_sha256:'f'.repeat(64),
    calculation_files:{'runner.cjs':'b'.repeat(64),'calculation.cjs':'c'.repeat(64)},
    configurations:configurations.map(c=>({configuration:c.id,model:'sha256:'+'d'.repeat(64),prepared:'e'.repeat(64)}))}};
  files.set('manifest.json',bytes(manifest));
  const reads=[];
  function directory(prefix=''){
    return {getDirectoryHandle:async name=>{if(!/^(parts|[0-9]{3})$/.test(name))throw Error('Unsafe directory');return directory(prefix+name+'/');},
      getFileHandle:async name=>({getFile:async()=>{
        const path=prefix+name,data=files.get(path);if(!data)throw Error('Saved file unavailable');
        return {size:data.length,arrayBuffer:async()=>{reads.push(path);return Uint8Array.from(data).buffer;}};
      }})};
  }
  return {files,manifest,catalogue,reads,directory:directory()};
}
