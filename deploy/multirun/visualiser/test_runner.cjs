/* (C) Copyright 2026, by Ross Richardson
 * Native annual buffering parity and bounded failure checks against pinned calculations.
 * @author ross richardson
 */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {test}=require('node:test');
const build=process.env.SIMPATHS_VISUALISER_TEST_BUILD;
if(!build)throw Error('Set SIMPATHS_VISUALISER_TEST_BUILD to the reviewed annual build');
const core=require(path.join(build,'calculation.cjs'));
const {processAnnual,main}=require(path.join(build,'runner.cjs'));
function texts(years=[2019,2020,2070]){
  const p=['run,time,id_Person,idBu,wgt,demAge,demMaleFlag,demEthnC6,labC4,healthMentalMcs,labWageHrly'];
  const b=['run,time,id_BenefitUnit,wgt,yDispEquivYear,yBenAmountMonth,yHhQuintilesMonthC5,region'];
  for(const year of years){
    b.push(`1,${year}.0,9007199254740993,1,${year*12},120,Q3,UKI`);
    for(let i=0;i<24;i++)p.push(`1,${year}.0,${70000000000000000n+BigInt(i)},9007199254740993,${i%2+1},${i+20},${i%2?'Male':'Female'},White,EmployedOrSelfEmployed,${i+year-2000},12`);
  }
  return [p.join('\r\n')+'\r\n',b.join('\n')];
}
async function fixture(callback,values=texts()){
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'simpaths-annual-test-'));
  const descriptors=[];
  try{
    for(const [i,text] of values.entries()){
      const file=path.join(root,i+'.csv');fs.writeFileSync(file,text);descriptors.push(fs.openSync(file,'r'));
    }
    return await callback(descriptors,root);
  }finally{for(const fd of descriptors)fs.closeSync(fd);fs.rmSync(root,{recursive:true});}
}
test('each complete year uses the unchanged parser, joins and calculations in original row order',async()=>{
  const values=texts();
  await fixture(async([p,b])=>{
    const actual=await processAnnual(p,b,'scenario_1','606');
    assert.deepEqual(actual,core.processRunTexts(...values,'scenario_1','606'));
    assert.deepEqual([...new Set(actual.map(r=>r.year))],[2019,2020,2070]);
  },values);
});
test('paired means, sample counts, suppression and confidence intervals retain exact parity',async()=>{
  const old=core.createGroupedAccumulator(),streamed=core.createGroupedAccumulator();
  for(const role of ['baseline','scenario_1'])for(const seed of ['606','607']){
    const values=texts().map(s=>role==='baseline'?s:s.replaceAll('120,Q3','240,Q3'));
    core.accumulateRunMetrics(old,core.processRunTexts(...values,role,seed));
    await fixture(async([p,b])=>core.accumulateRunMetrics(streamed,await processAnnual(p,b,role,seed)),values);
  }
  assert.deepEqual(core.finaliseAggregation(streamed),core.finaliseAggregation(old));
});
test('year mismatches, repeated annual blocks and missing required headers fail closed',async()=>{
  for(const values of [[texts()[0],texts([2019,2021,2070])[1]],texts([2019,2020,2019]),
      [texts()[0].replace('idBu','private_column'),texts()[1]],[texts()[0],texts()[1].replace('time','private_time')]]){
    await fixture(async([p,b])=>assert.rejects(processAnnual(p,b,'baseline','606')),values);
  }
});
test('a complete annual block has explicit byte and row limits',async()=>{
  for(const options of [{maximumBytes:100,maximumRows:500000},{maximumBytes:128*1024**2,maximumRows:5}]){
    await fixture(async([p,b])=>assert.rejects(processAnnual(p,b,'baseline','606',options),/processing bound/));
  }
});
test('small existing outputs keep arbitrary year ordering and the original parser path',async()=>{
  const values=texts([2020,2019]);
  await fixture(async([p,b],root)=>{
    const output=path.join(root,'metrics.json');
    await main({operation:'run',person_fd:p,benefit_fd:b,role:'baseline',run:'606',output});
    assert.deepEqual(JSON.parse(fs.readFileSync(output,'utf8')),JSON.parse(JSON.stringify(core.processRunTexts(...values,'baseline','606'))));
  },values);
});
