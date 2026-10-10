/* (C) Copyright 2026, by Ross Richardson
 * Private one-run calculations and seed-paired aggregation using pinned upstream code.
 * Inputs are verified inherited file descriptors; results never go to stdout.
 * @author ross richardson
 */
const fs=require('fs'),core=require('./calculation.cjs');
const readline=require('node:readline');
const MIB=1024*1024;
// Small/older outputs keep their existing parser path. Large native outputs
// arrive in annual blocks; each complete year is passed to the same upstream
// calculation. aggregateSingleRun groups independently by year, so this changes
// buffering only. Noncontiguous years are rejected before any publication.
async function* annualCsv(fd,required,{maximumBytes=128*MIB,maximumRows=500000}={}){
  // Own a separate handle to the verified inode. Destroying a Node read stream
  // must not close the inherited descriptor held by the platform's source guard.
  const source=fs.createReadStream('/proc/self/fd/'+fd,{encoding:'utf8',highWaterMark:256*1024});
  const lines=readline.createInterface({input:source,crlfDelay:Infinity});
  let header,position,year,parts=[],bytes=0,count=0,seen=false;
  try{
    for await(const raw of lines){
      if(Buffer.byteLength(raw)>MIB)throw Error('Native CSV record exceeds the processing bound');
      if(!raw.trim())continue;
      if(header===undefined){
        header=raw.replace(/^\uFEFF/,'');
        const columns=header.split(',').map(value=>value.trim().replace(/^"|"$/g,''));
        if(new Set(columns).size!==columns.length||required.some(name=>!columns.includes(name)))
          throw Error('Incompatible raw output schema');
        position=columns.indexOf('time');continue;
      }
      const value=raw.split(',',position+1)[position]?.trim().replace(/^"|"$/g,'');
      if(!/^[0-9]{4}(?:\.0+)?$/.test(value||''))throw Error('Invalid native annual CSV time');
      const current=Number(value);
      if(year!==undefined&&current<year)throw Error('Large native CSV years must be contiguous and increasing');
      if(year!==undefined&&current!==year){
        yield {year,text:header+'\n'+parts.join('\n')+'\n',rows:count};
        parts=[];bytes=0;count=0;
      }
      year=current;seen=true;bytes+=Buffer.byteLength(raw)+1;count++;
      if(bytes>maximumBytes||count>maximumRows)throw Error('Native annual block exceeds the processing bound');
      parts.push(raw);
    }
    if(!seen)throw Error('Native CSV contains no annual records');
    yield {year,text:header+'\n'+parts.join('\n')+'\n',rows:count};
  }finally{lines.close();source.destroy();}
}
async function processAnnual(personFd,benefitFd,role,run,options){
  const people=annualCsv(personFd,['time','idBu','wgt'],options);
  const benefits=annualCsv(benefitFd,['time','id_BenefitUnit'],options);
  const metrics=[];
  try{
    while(true){
      const p=await people.next(),b=await benefits.next();
      if(p.done||b.done){
        if(p.done!==b.done)throw Error('Person and benefit annual years differ');
        return metrics;
      }
      if(p.value.year!==b.value.year)throw Error('Person and benefit annual years differ');
      const rows=core.processRunTexts(p.value.text,b.value.text,role,run);
      if(rows.some(row=>row.year!==p.value.year))throw Error('Calculated annual year differs from its input');
      metrics.push(...rows);
    }
  }finally{await people.return();await benefits.return();}
}
const REGION_LABELS=Object.freeze({
  'North East (England)':'North East','North West (England)':'North West',
  'Yorkshire and The Humber':'Yorkshire and the Humber',
  'East Midlands (England)':'East Midlands','West Midlands (England)':'West Midlands',
  'South East (England)':'South East','South West (England)':'South West',
});
function write(output,value,maximum=16*MIB){
  const text=JSON.stringify(value);
  if(Buffer.byteLength(text)>maximum)throw Error('Result size limit');
  fs.writeFileSync(output+'.pending',text,{mode:0o600});
  fs.renameSync(output+'.pending',output);
}
async function main(request){
if(request.operation==='run'){
  let metrics;
  if(Math.max(fs.fstatSync(request.person_fd).size,fs.fstatSync(request.benefit_fd).size)>64*MIB){
    metrics=await processAnnual(request.person_fd,request.benefit_fd,request.role,request.run);
  }else{
    const person=fs.readFileSync(request.person_fd,'utf8'),benefit=fs.readFileSync(request.benefit_fd,'utf8');
    const header=text=>new Set(text.replace(/^\uFEFF/,'').split(/\r?\n/,1)[0].split(',').map(x=>x.replace(/^"|"$/g,'')));
    const p=header(person),b=header(benefit);
    if(!p.has('time')||!p.has('idBu')||!p.has('wgt')||!b.has('time')||!b.has('id_BenefitUnit'))
      throw Error('Incompatible raw output schema');
    metrics=core.processRunTexts(person,benefit,request.role,request.run);
  }
  // Private metrics may exceed the public single/pair envelope. The platform
  // validates aggregate fields, rows and delivery size separately afterward.
  write(request.output,metrics,64*MIB);
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
      write(output.path,selected,96*MIB);
    }
  }else write(request.output,rows);
}else throw Error('Unknown private operation');
}
module.exports={annualCsv,processAnnual,main};
if(require.main===module){
  main(JSON.parse(fs.readFileSync(process.argv[2],'utf8'))).catch(error=>{
    console.error(error.stack);process.exitCode=1;
  });
}
