/* (C) Copyright 2026, by Ross Richardson
 * Select a named baseline/alternative pair from an aggregate-only comparison set.
 * Future simultaneous charts can consume the same separately identified series.
 * @author ross richardson
 */
export function displayedComparison(result, selected) {
  if (!result) return {rows:[],configurations:[],alternatives:[],selected:''};
  const configs=result.configurations;
  if (!Array.isArray(configs)||!configs.length) throw Error('Online comparison metadata is unavailable.');
  if (result.format==='simpaths.visualiser.v1') {
    if (!Array.isArray(result.data?.rows)) throw Error('Online aggregate results are unavailable.');
    return {rows:result.data.rows,configurations:configs,alternatives:[],selected:''};
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
  const index=Math.max(1,ids.indexOf(selected));
  return {rows:[...series[0].rows,...series[index].rows],
    configurations:[configs[0],configs[index]],alternatives,selected:ids[index]};
}
