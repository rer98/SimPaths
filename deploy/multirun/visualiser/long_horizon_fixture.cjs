/* (C) Copyright 2026, by Ross Richardson
 * Generate bounded fictional long-horizon summaries using the pinned scientific calculations.
 * No model simulation or real user data; scientific calculation code is unchanged.
 * @author ross richardson
 */
const fs=require('node:fs'),path=require('node:path');
const build=process.env.SIMPATHS_VISUALISER_TEST_BUILD;if(!build)throw Error('Set the pinned test build');
const c=require(path.join(build,'calculation.cjs'));
const maps={'Region':c.REGION_MAP,'Disability Status':c.DISABILITY_MAP,'Household Type':c.HOUSEHOLD_MAP,
 'Employment status':c.EMPLOYMENT_MAP,'Financial distress flag':c.FINANCIAL_MAP,
 'Need of social care':c.SOCIAL_CARE_MAP,'Universal Credit Benefits Flag':c.UC_BENEFITS_MAP};
function random(i,j){let x=Math.imul(i+1,2654435761)^Math.imul(j+11,2246822519);x^=x>>>16;return x>>>0;}
function raw(display,i,j){
 const n=random(i,j);
 if(display==='Age')return [16,21,30,40,50,60,75][n%7];
 if(display==='Gender')return n%2?'Male':'Female';
 const def=display==='Region'?c.getStratifierDef(display):c.getVariableDef(display);
 if(def.type==='numeric')return display==='Hourly earnings'?(n%160):100+(n%60000);
 const values=(def.order||[]).filter(x=>x!=='Missing');let label=values[n%values.length]||'';
 if(maps[display])label=Object.entries(maps[display]).find(([key,value])=>value===label&&!key.includes(','))?.[0]??label;
 if(String(label).includes(','))throw Error('Use an unquoted native category for this fixture');
 return label;
}
function execute(){
 const fields=Object.keys(c.COLUMN_MAP),p=['run,time,id_Person,idBu,wgt,'+fields.join(',')],b=['run,time,id_BenefitUnit,wgt,'+fields.join(',')];
 for(let i=0;i<1000;i++){
  const values=Object.values(c.COLUMN_MAP).map((display,j)=>raw(display,i,j)).join(','),unit=9007199254740993n+BigInt(i);
  p.push(`1,2019.0,${70000000000000000n+BigInt(i)},${unit},1,${values}`);
  b.push(`1,2019.0,${unit},1,${values}`);
 }
 const annual=c.processRunTexts(p.join('\n'),b.join('\n'),'baseline','606');

 const [directory,endText,countText]=process.argv.slice(2),end=Number(endText),count=Number(countText);
 if(!directory||![2026,2035,2070].includes(end)||![2,10].includes(count))throw Error('Use a bounded fictional horizon and seed count');
 const refs=[];
 for(let index=0;index<count;index++){
  const seed=String(606+index),metrics=[];
  for(let year=2019;year<=end;year++)for(const row of annual)metrics.push({...row,year,run:seed});
  const file=path.join(directory,seed+'.json');fs.writeFileSync(file,JSON.stringify(metrics));
  refs.push({path:file,seed,role:'baseline',canonical:true});
 }
 console.log(JSON.stringify({annual_rows:annual.length,rows:annual.length*(end-2019+1),metrics:refs}));
}
execute();
