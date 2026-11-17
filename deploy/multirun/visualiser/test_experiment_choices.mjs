/* (C) Copyright 2026, by Ross Richardson
 * Selected configuration references survive reload without retaining private data.
 * @author ross richardson
 */
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {validateSelection,fromQuery,selectionQuery,saveSelection,restoreSelection} from './experiment_choices.mjs';

test('nine choices round trip in a link, with an independent baseline and escaped identifiers',()=>{
  const value={configurations:['base',...Array.from({length:8},(_,i)=>'policy '+i+'&')],baseline:'base'};
  assert.deepEqual(fromQuery(selectionQuery(value)),value);
  assert.equal(fromQuery(new URLSearchParams()),null);
  assert.deepEqual(fromQuery(selectionQuery({...value,baseline:null})),{...value,baseline:null});
});
test('foreign baselines, ten selections, duplicate and malformed references are rejected',()=>{
  for(const configurations of [[],Array(10).fill('a'),['a','a'],['a\n'],[null]])
    assert.throws(()=>validateSelection({configurations,baseline:null}));
  assert.throws(()=>validateSelection({configurations:['a'],baseline:'b'}));
  assert.throws(()=>fromQuery(new URLSearchParams('chosen=a&baseline=&baseline=a')));
});
test('saved preferences retain only references and recover from corruption or storage failure',()=>{
  const saved=new Map(),storage={getItem:key=>saved.get(key),setItem:(key,value)=>saved.set(key,value)};
  const value={configurations:['a','b'],baseline:'a',name:'PRIVATE_NAME',csrf:'PRIVATE_CREDENTIAL'};
  saveSelection('first',value,storage);
  assert.deepEqual(restoreSelection('first',storage),{configurations:['a','b'],baseline:'a'});
  assert.equal(restoreSelection('other-account-experiment',storage),null);
  assert.ok(!Array.from(saved.values()).join('').includes('PRIVATE_'));
  saved.set('simpaths-visualiser-choice:first','invalid');assert.equal(restoreSelection('first',storage),null);
  const blocked={getItem(){throw Error('blocked');},setItem(){throw Error('blocked');}};
  saveSelection('first',value,blocked);assert.equal(restoreSelection('first',blocked),null);
});
