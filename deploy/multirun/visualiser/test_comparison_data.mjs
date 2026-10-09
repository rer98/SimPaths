/* (C) Copyright 2026, by Ross Richardson
 * Verify aggregate comparison selection, isolation, names and legacy compatibility.
 * @author ross richardson
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {displayedComparison} from './comparison_data.mjs';

const fixture=()=>({format:'simpaths.visualiser.v2',
  comparison:{baseline:'base',scenarios:['a','b']},
  configurations:[{id:'base',role:'Baseline',name:'Same name'},{id:'a',role:'Scenario',name:'Same name'},
    {id:'b',role:'Scenario',name:'<b>Third name</b>'}],
  data:{series:[{configuration:'base',rows:[{scenario:'baseline',mean_value:10}]},
    {configuration:'a',rows:[{scenario:'scenario',mean_value:20}]},
    {configuration:'b',rows:[{scenario:'scenario',mean_value:30}]}]}});

test('all alternatives retain separate identities and include the baseline once',()=>{
  const source=fixture(),snapshot=JSON.stringify(source);
  const selected=displayedComparison(source);
  assert.deepEqual(selected.rows.map(r=>r.mean_value),[10,20,30]);
  assert.deepEqual(selected.rows.map(r=>r.scenario),['baseline','scenario_1','scenario_2']);
  assert.deepEqual(selected.configurations.map(c=>c.id),['base','a','b']);
  assert.equal(selected.names.scenario_2,'<b>Third name</b>');
  assert.equal(selected.names.scenario_1,selected.names.baseline);
  assert.equal(JSON.stringify(source),snapshot);
});
test('former saved choices cannot omit alternatives from the new simultaneous view',()=>{
  for(const id of [undefined,'deleted','base']) assert.equal(displayedComparison(fixture(),id).rows.length,3);
  assert.deepEqual(displayedComparison(null).rows,[]);
});
test('legacy pair rows and metadata are preserved',()=>{
  const data={format:'simpaths.visualiser.v1',configurations:[{id:'a',role:'Scenario'}],data:{rows:[{scenario:'scenario'}]}};
  assert.equal(displayedComparison(data).rows,data.data.rows);
  assert.deepEqual(displayedComparison(data).alternatives,[]);
});
test('wrong identities, missing series and cross-role rows cannot be presented as a comparison',()=>{
  for(const alter of [d=>{d.format='unknown';},d=>{d.data.series.pop();},
    d=>{d.data.series[2].configuration='a';},d=>{d.data.series[2].rows[0].scenario='baseline';},
    d=>{d.configurations[2].id='a';},d=>{d.comparison.scenarios.reverse();},
    d=>{d.data.series[1].rows=[];}]) {
    const value=fixture();alter(value);assert.throws(()=>displayedComparison(value,'b'));
  }
});
