/* (C) Copyright 2026, by Ross Richardson
 * Private one-run calculations and final aggregation using the pinned upstream module.
 * Inputs are verified inherited file descriptors; results never go to stdout.
 * @author ross richardson
 */
const fs=require('fs'),path=require('path'),core=require('./calculation.cjs');
// The pinned parser and dashboard use different labels for these same UK
// regions. Translate only the known aliases; arbitrary categories still fail.
const REGION_LABELS=Object.freeze({
  'North East (England)':'North East','North West (England)':'North West',
  'Yorkshire and The Humber':'Yorkshire and the Humber',
  'East Midlands (England)':'East Midlands','West Midlands (England)':'West Midlands',
  'South East (England)':'South East','South West (England)':'South West',
});
const request=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
let value;
if(request.operation==='run'){
  const person=fs.readFileSync(request.person_fd,'utf8'),benefit=fs.readFileSync(request.benefit_fd,'utf8');
  // Raw columns must exist; the old parser otherwise silently manufactures missingness.
  const header=text=>new Set(text.split(/\r?\n/,1)[0].split(',').map(x=>x.replace(/^"|"$/g,'')));
  const p=header(person),b=header(benefit);
  if(!p.has('time')||!p.has('idBu')||!p.has('wgt')||!b.has('time')||!b.has('id_BenefitUnit'))
    throw Error('Incompatible raw output schema');
  value=core.processRunTexts(person,benefit,request.role,request.run);
}else if(request.operation==='aggregate'){
  const metrics=request.metrics.flatMap(file=>JSON.parse(fs.readFileSync(file,'utf8')));
  value=core.performCrossRunAggregation(metrics);
  // Only the explicit variable/group vocabulary can leave the private processor.
  for(const row of value){
    // Native Les_c4 enum spelling differs from the dashboard's display labels.
    // This is presentation only: no regrouping or numerical calculation changes.
    if(row.variable==='Employment status')row.variable_value=({
      EmployedOrSelfEmployed:'Employed or self employed',NotEmployed:'Not employed',
    })[row.variable_value]||row.variable_value;
    if(row.stratifier==='Region')row.stratifier_value=REGION_LABELS[row.stratifier_value]||row.stratifier_value;
    const variable=core.getVariableDef(row.variable);
    const allowed=variable.type==='numeric'?['Mean','Missing']:[...variable.order,'Missing'];
    const stratifier=core.getStratifierDef(row.stratifier);
    const groups=row.stratifier==='Overall'?['Overall']:[...stratifier.order,'Missing'];
    if(!allowed.includes(row.variable_value)||!groups.includes(row.stratifier_value))
      throw Error('Unexpected category in private calculation');
  }
}else throw Error('Unknown private operation');
const text=JSON.stringify(value);
if(Buffer.byteLength(text)>16*1024*1024) throw Error('Result size limit');
const temp=request.output+'.pending';
fs.writeFileSync(temp,text,{mode:0o600});
fs.renameSync(temp,request.output);
