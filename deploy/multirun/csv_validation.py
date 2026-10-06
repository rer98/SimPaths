"""(C) Copyright 2026, by Ross Richardson

Verify annual scientific CSV structure without blocking hosted Python threads.
The private helper receives trusted file paths/years and returns no data rows.
@author ross richardson
"""
import csv
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import stat
import subprocess
import sys


ERRORS=frozenset({'Invalid scientific CSV header','Truncated scientific CSV',
    'Invalid scientific output year','Unexpected scientific output year',
    'CSV mixes native runs','Scientific output has missing years or no records',
    'Scientific output could not be read'})


def check_files(paths, years_expected):
    """Keep the existing header, row, year and shared-run checks unchanged."""
    run_id=None
    for path in paths:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'r',newline='',encoding='utf-8') as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ValueError('Scientific output could not be read')
            reader=csv.DictReader(source)
            columns=reader.fieldnames or []
            if len(set(columns))!=len(columns) or not {'run','time'}<=set(columns):
                raise ValueError('Invalid scientific CSV header')
            years,count=set(),0
            for row in reader:
                if None in row or None in row.values():
                    raise ValueError('Truncated scientific CSV')
                try:
                    year=Decimal(row['time'])
                except InvalidOperation as error:
                    raise ValueError('Invalid scientific output year') from error
                if not year.is_finite() or year!=int(year) or int(year) not in years_expected:
                    raise ValueError('Unexpected scientific output year')
                years.add(int(year))
                run_id=run_id or row['run']
                if not row['run'] or run_id!=row['run']:
                    raise ValueError('CSV mixes native runs')
                count+=1
            if not count or years!=years_expected:
                raise ValueError('Scientific output has missing years or no records')


def validate_annual_csv(paths, years):
    """A bounded private response; never expose parser rows, paths or stderr."""
    from .artifacts import ArtifactError
    request=dict(paths=[str(path) for path in paths],years=sorted(years))
    result=subprocess.run([sys.executable,str(Path(__file__).resolve())],
        input=json.dumps(request),text=True,capture_output=True)
    if result.returncode or len(result.stdout)>2048:
        raise ArtifactError('Scientific output could not be read')
    try:
        value=json.loads(result.stdout)
    except ValueError:
        raise ArtifactError('Scientific output could not be read') from None
    if type(value) is dict and set(value)=={'ok'} and value['ok'] is True:
        return
    if type(value) is dict and set(value)=={'error'} and type(value['error']) is str and value['error'] in ERRORS:
        raise ArtifactError(value['error'])
    raise ArtifactError('Scientific output could not be read')


def main():
    try:
        body=sys.stdin.read(65537)
        if len(body)>65536:raise ValueError()
        value=json.loads(body)
        if (type(value) is not dict or set(value)!={'paths','years'}
                or type(value['paths']) is not list or len(value['paths'])!=2
                or any(type(p) is not str or len(p)>4096 for p in value['paths'])
                or type(value['years']) is not list or not value['years'] or len(value['years'])>10000
                or any(type(y) is not int for y in value['years'])):
            raise ValueError()
        check_files(value['paths'],set(value['years']))
        result={'ok':True}
    except (ValueError,OSError,csv.Error) as error:
        message=str(error)
        result={'error':message if message in ERRORS else 'Scientific output could not be read'}
    print(json.dumps(result))


if __name__=='__main__':main()
