/* (C) Copyright 2026, by Ross Richardson
 * Private one-run calculations and seed-paired aggregation using pinned upstream code.
 * Inputs are verified inherited file descriptors; results never go to stdout.
 * @author ross richardson
 */
const fs=require('fs'),core=require('./calculation.cjs');
const REGION_LABELS=Object.freeze({
  'North East (England)':'North East','North West (England)':'North West',
  'Yorkshire and The Humber':'Yorkshire and the Humber',
  'East Midlands (England)':'East Midlands','West Midlands (England)':'West Midlands',
  'South East (England)':'South East','South West (England)':'South West',
});
const request=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
function write(output,value){
  const text=JSON.stringify(value);
  if(Buffer.byteLength(text)>16*1024*1024)throw Error('Result size limit');
  fs.writeFileSync(output+'.pending',text,{mode:0o600});
  fs.renameSync(output+'.pending',output);
}
if(request.operation==='run'){
  const person=fs.readFileSync(request.person_fd,'utf8'),benefit=fs.readFileSync(request.benefit_fd,'utf8');
  const header=text=>new Set(text.split(/\r?\n/,1)[0].split(',').map(x=>x.replace(/^"|"$/g,'')));
  const p=header(person),b=header(benefit);
  if(!p.has('time')||!p.has('idBu')||!p.has('wgt')||!b.has('time')||!b.has('id_BenefitUnit'))
    throw Error('Incompatible raw output schema');
  write(request.output,core.processRunTexts(person,benefit,request.role,request.run));
}else if(request.operation==='aggregate'){
  const grouped=core.createGroupedAccumulator();
  // Feed and release one run's private metrics at a time. Match by actual seed.
  for(const file of request.metrics)core.accumulateRunMetrics(grouped,JSON.parse(fs.readFileSync(file,'utf8')));
  const rows=core.finaliseAggregation(grouped);
  for(const row of rows){
    if(row.variable==='Employment status')row.variable_value=({
      EmployedOrSelfEmployed:'Employed or self employed',NotEmployed:'Not employed',
    })[row.variable_value]||row.variable_value;
    if(row.stratifier==='Region')row.stratifier_value=REGION_LABELS[row.stratifier_value]||row.stratifier_value;
    const variable=core.getVariableDef(row.variable);
    const allowed=row.metric_type==='wage_bin'&&row.variable==='Hourly earnings'
      ? [...core.WAGE_BINS.map(bin=>bin[2]),'Missing']
      : row.metric_type==='income_bin'&&core.INCOME_BIN_VARS[row.variable]
        ? [...core.INCOME_BIN_VARS[row.variable].labels,'Missing']
        : row.metric_type==='pyramid_bin'&&row.variable==='Age'
          ? [...core.getStratifierDef('Age').order,'Missing']
        : variable.type==='numeric'?['Mean','Continuous Mean','Missing']:[...variable.order,'Missing'];
    const stratifier=core.getStratifierDef(row.stratifier);
    const groups=row.stratifier==='Overall'?['Overall']:[...stratifier.order,'Missing'];
    if(!allowed.includes(row.variable_value)||!groups.includes(row.stratifier_value))
      throw Error('Unexpected category in private calculation');
  }
  if(request.outputs){
    for(const output of request.outputs){
      const selected=rows.filter(row=>row.scenario===output.role).map(row=>({...row,scenario:output.role==='baseline'?'baseline':'scenario'}));
      if(!selected.length)throw Error('Missing configuration aggregates');
      write(output.path,selected);
    }
  }else write(request.output,rows);
}else throw Error('Unknown private operation');
