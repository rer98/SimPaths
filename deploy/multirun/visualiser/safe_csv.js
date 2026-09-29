/* (C) Copyright 2026, by Ross Richardson
 * CSP-compatible CSV object conversion for local browser parsing.
 * D3 still handles CSV quoting and row parsing; dynamic code generation is avoided.
 * @author ross richardson
 */
import * as d3 from 'd3';
export * from 'd3';
export function csvParse(text,convert){
  const parsed=d3.csvParseRows(text),columns=parsed.shift()||[];
  const rows=parsed.map((values,index)=>{
    const row=Object.fromEntries(columns.map((name,i)=>[name,values[i]||'']));
    return convert?convert(row,index,columns):row;
  }).filter(row=>row!=null);
  rows.columns=columns;
  return rows;
}
