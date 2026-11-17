/* (C) Copyright 2026, by Ross Richardson
 * Remember bounded display choices without credentials, settings or scientific data.
 * Every restored reference is rechecked by the owner-authorised server.
 * @author ross richardson
 */
export const MAX_SELECTION=9;
const identifier=value=>typeof value==='string'&&value.length>0&&value.length<=200&&!/[\u0000-\u001f]/.test(value);
export function validateSelection(value){
  if(!value||!Array.isArray(value.configurations)||!value.configurations.length||value.configurations.length>MAX_SELECTION||
      value.configurations.some(id=>!identifier(id))||new Set(value.configurations).size!==value.configurations.length||
      !(value.baseline===null||identifier(value.baseline)&&value.configurations.includes(value.baseline)))
    throw Error('Choose up to 9 different configurations, including the baseline.');
  return {configurations:[...value.configurations],baseline:value.baseline};
}
export function selectionQuery(value){
  const selected=validateSelection(value),query=new URLSearchParams();
  selected.configurations.forEach(id=>query.append('chosen',id));query.set('baseline',selected.baseline||'');return query;
}
export function fromQuery(query){
  if(!query.has('chosen'))return null;
  if(query.getAll('baseline').length!==1)throw Error('Choose a baseline for this view.');
  return validateSelection({configurations:query.getAll('chosen'),baseline:query.get('baseline')||null});
}
export function restoreSelection(experiment,storage){
  try{return validateSelection(JSON.parse(storage.getItem('simpaths-visualiser-choice:'+experiment)));}catch{return null;}
}
export function saveSelection(experiment,value,storage){
  try{storage.setItem('simpaths-visualiser-choice:'+experiment,JSON.stringify(validateSelection(value)));}catch{}
}
