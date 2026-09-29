"""(C) Copyright 2026, by Ross Richardson

Preview bounded parameter sweeps as fixed browser cards with explicit matches.
The existing normaliser expands exact numeric ranges and all combinations.

@author ross richardson
"""
from copy import deepcopy
from dataclasses import replace
import json
import re

from .configuration import normalise
from .schema import ConfigurationError, MODEL_FIELDS
from .yaml_input import check_tree


def _number(text, field, path):
    if type(text) is not str or len(text)>64:
        raise ConfigurationError(path, 'enter a number')
    text=text.strip()
    if field.kind=='boolean':
        if text not in ('true','false'):
            raise ConfigurationError(path, 'use true or false')
        return text=='true'
    pattern=r'[+-]?[0-9]+' if field.kind=='int' else r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?'
    if not re.fullmatch(pattern,text):
        raise ConfigurationError(path, 'enter an integer' if field.kind=='int' else 'enter a finite number')
    value=int(text) if field.kind=='int' else float(text)
    return field.validate(value,path)


def _effective(run, document):
    return json.dumps(dict(dataset=run.get('dataset_revision',document['dataset_revision']),
        common=run.get('common',document['common']),model=run['model_args'],collector=run['collector_args']),sort_keys=True)


class BrowserSweep:
    def browser_sweep(self, dataset, form, recipe):
        check_tree(recipe,self.submission_limits)
        if (type(recipe) is not dict or set(recipe)!={'base','dimensions'} or
                type(recipe['base']) is not str or type(recipe['dimensions']) is not list or
                not 1<=len(recipe['dimensions'])<=len(MODEL_FIELDS)):
            raise ConfigurationError('sweep','choose a starting configuration and at least one parameter')
        document=self.browser_configuration(dataset,form)['configuration']
        runs=document['run_sets']
        base=next((r for r in runs if r['id']==recipe['base']),None)
        if base is None:
            raise ConfigurationError('sweep.base','choose an existing configuration')
        parameters={}
        for i,row in enumerate(recipe['dimensions']):
            path=f'sweep.parameters[{i}]'
            if type(row) is not dict or type(row.get('field')) is not str or row['field'] not in MODEL_FIELDS:
                raise ConfigurationError(path,'choose a supported model parameter')
            key='model_args.'+row['field']
            if key in parameters:
                raise ConfigurationError(path,'each parameter may appear only once')
            field=MODEL_FIELDS[row['field']]
            if row.get('mode')=='values' and set(row)=={'field','mode','values'} and type(row['values']) is str and len(row['values'])<=2048:
                parts=re.split(r'[,\n]',row['values'])
                if not 1<=len(parts)<=10 or any(not p.strip() for p in parts):
                    raise ConfigurationError(path,'enter 1–10 values separated by commas or new lines, without empty entries')
                parameters[key]=[_number(v,field,path) for v in parts]
            elif row.get('mode')=='range' and set(row)=={'field','mode','start','end','step'} and field.kind in ('int','double'):
                parameters[key]={k:_number(row[k],field,path+'.'+k) for k in ('start','end','step')}
            else:
                raise ConfigurationError(path,'use a value list, or start/end/step for a numeric parameter')
        limits=replace(self.submission_limits,max_run_sets=10)
        expanded=normalise({**document,'run_sets':[],
            'sweep':dict(id_prefix='sweep',combination='all',
                base={k:base[k] for k in ('model_args','collector_args')},parameters=parameters)},limits=limits).as_dict()
        existing={_effective(r,document):r for r in runs}
        used={r['id'] for r in runs}
        additions,rows=[],[]
        origin=dict(version='simpaths.sweep.v1',combination='all',
            base=dict(id=base['id'],name=base['name'],dataset_revision=base.get('dataset_revision',dataset),
                common=base.get('common',document['common']),model_args=base['model_args'],collector_args=base['collector_args']),
            parameters=expanded['sweep']['parameters'])
        ordinal=0
        for candidate,generated in zip(expanded['run_sets'],expanded['sweep']['generated']):
            candidate.update({k:deepcopy(base[k]) for k in ('dataset_revision','common') if k in base})
            match=existing.get(_effective(candidate,document))
            if match:
                rows.append(dict(values=generated['values'],configuration=match['id'],name=match['name'],existing=True))
                continue
            ordinal+=1
            while 'sweep-'+str(ordinal) in used:
                ordinal+=1
            candidate['id']='sweep-'+str(ordinal);used.add(candidate['id'])
            candidate['name']=f"{base['name'][:95]} — sweep {ordinal}"
            candidate['generation']=dict(deepcopy(origin),values=generated['values'])
            additions.append(candidate)
            rows.append(dict(values=generated['values'],configuration=candidate['id'],name=candidate['name'],existing=False))
        total=len(runs)+len(additions)
        if total>10:
            raise ConfigurationError('sweep',f'this would create {total} configurations including existing cards; the deployment limit is 10')
        # Validate final fixed cards, never submit a recipe for a worker to expand.
        self.browser_configuration(dataset,{**form,'run_sets':[*runs,*additions]})
        return dict(run_sets=additions,rows=rows,parameters=origin['parameters'],
            combinations=len(rows),matches=len(rows)-len(additions),added=len(additions),
            configurations=total,simulations=total*form['repetitions'],
            added_simulations=len(additions)*form['repetitions'])
