/* (C) Copyright 2026, by Ross Richardson
 * Present all selected alternatives with stable identities and plain-text names.
 * @author ross richardson
 */
export function displayedComparison(result) {
  if (!result) return {rows:[],configurations:[],alternatives:[],names:{}};
  const configs=result.configurations;
  if (!Array.isArray(configs)||!configs.length) throw Error('Online comparison metadata is unavailable.');
  if (result.format==='simpaths.visualiser.v1') {
    if (!Array.isArray(result.data?.rows)) throw Error('Online aggregate results are unavailable.');
    return {rows:result.data.rows,configurations:configs,alternatives:[],
      names:Object.fromEntries(configs.map(c=>[c.role.toLowerCase(),c.name||c.role]))};
  }
  if (result.format!=='simpaths.visualiser.v2'||configs.length<2||configs.length>100||
      !Array.isArray(result.data?.series)||result.data.series.length!==configs.length||
      result.comparison?.baseline!==configs[0].id||configs[0].role!=='Baseline') {
    throw Error('Online comparison metadata is unavailable.');
  }
  const ids=configs.map(c=>c.id),alternatives=configs.slice(1);
  if (new Set(ids).size!==ids.length||JSON.stringify(result.comparison.scenarios)!==JSON.stringify(ids.slice(1))||
      alternatives.some(c=>c.role!=='Scenario')) throw Error('Online comparison selections do not match.');
  const series=result.data.series;
  for(let i=0;i<configs.length;i++) {
    if(series[i].configuration!==ids[i]||!Array.isArray(series[i].rows)||!series[i].rows.length||
        series[i].rows.some(row=>row.scenario!==(i===0?'baseline':'scenario'))) {
      throw Error('Online aggregate series do not match their configurations.');
    }
  }
  const roles=configs.map((_,index)=>index===0?'baseline':'scenario_'+index);
  return {rows:series.flatMap((item,index)=>item.rows.map(row=>({...row,scenario:roles[index]}))),
    configurations:configs,alternatives,names:Object.fromEntries(configs.map((c,index)=>[roles[index],c.name||c.role]))};
}
