/* (C) Copyright 2026, by Ross Richardson
 * Re-label bounded verified summaries for private aggregation capacity fixtures.
 * This creates synthetic repetition identities, never independent random seeds.
 * @author ross richardson
 */
'use strict';
const fs=require('node:fs');
const MAXIMUM=64*1024*1024;
function replay(request){
  if(!request||!Array.isArray(request.runs)||request.runs.length<2||request.runs.length>1000)
    throw Error('Invalid replay request');
  const outputs=new Set();
  for(const run of request.runs){
    if(!/^[0-9]{1,20}$/.test(String(run.seed))||!/^baseline$|^scenario_[1-9][0-9]*$/.test(run.role)||
       !/^[0-9]{1,20}$/.test(String(run.source_seed))||typeof run.output!=='string'||outputs.has(run.output))
      throw Error('Invalid replay identity');
    outputs.add(run.output);
    const fd=fs.openSync(run.summary,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);
    let rows;
    try{
      const before=fs.fstatSync(fd);
      if(!before.isFile()||before.size<2||before.size>MAXIMUM)throw Error('Invalid summary size');
      rows=JSON.parse(fs.readFileSync(fd,'utf8'));
      const after=fs.fstatSync(fd);
      if(before.size!==after.size||before.mtimeMs!==after.mtimeMs||before.ctimeMs!==after.ctimeMs)
        throw Error('Summary changed');
    }finally{fs.closeSync(fd);}
    if(!Array.isArray(rows)||!rows.length||rows.length>200000)throw Error('Invalid summary rows');
    for(const row of rows){
      if(!row||typeof row!=='object'||Array.isArray(row)||String(row.run)!==String(run.source_seed)||
         typeof row.scenario!=='string')throw Error('Summary identity differs');
      // Preserve every measured value. Only synthetic repetition and role labels change.
      row.run=String(run.seed);row.scenario=run.role;
    }
    const encoded=JSON.stringify(rows,(_key,value)=>{
      if(typeof value==='number'&&!Number.isFinite(value))throw Error('Nonfinite summary value');
      return value;
    });
    if(Buffer.byteLength(encoded)>MAXIMUM)throw Error('Replayed summary exceeds bound');
    fs.writeFileSync(run.output,encoded,{flag:'wx',mode:0o600});
  }
}
module.exports={replay};
if(require.main===module){
  try{replay(JSON.parse(fs.readFileSync(process.argv[2],'utf8')));}
  catch(error){console.error('Capacity replay failed: '+error.message);process.exitCode=1;}
}
