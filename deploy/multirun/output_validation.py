"""(C) Copyright 2026, by Ross Richardson

Trusted output validation entrypoint for a retained, enforced model allocation.
Only bounded completion fingerprints leave the helper; scientific rows, private
paths and diagnostic exception text are never included in its response.
@author ross richardson
"""
import json
from pathlib import Path
import sys

# The platform invokes this exact trusted script with isolated Python. Resolve
# model imports from its own installed tree, without inheriting PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from deploy.multirun.configuration import normalise
from deploy.multirun.queue_adapter import validate_outputs

REQUEST_LIMIT = 1024**2
RESPONSE_LIMIT = 16 * 1024


def validate_request(value):
    if (type(value) is not dict
            or set(value) != {'output', 'configuration', 'run_set_id', 'workers'}
            or type(value['output']) is not str or len(value['output']) > 4096
            or not Path(value['output']).is_absolute()
            or type(value['run_set_id']) is not str or len(value['run_set_id']) > 128
            or type(value['workers']) is not int or value['workers'] not in (1, 2)):
        raise ValueError('Invalid private output validation request')
    configuration = normalise(value['configuration'])
    return validate_outputs(Path(value['output']), configuration, value['run_set_id'],
                            workers=value['workers'])


def main():
    try:
        body = sys.stdin.buffer.read(REQUEST_LIMIT + 1)
        if len(body) > REQUEST_LIMIT:
            raise ValueError('Private output validation request exceeds its limit')
        result = validate_request(json.loads(body))
        response = json.dumps(result, allow_nan=False, separators=(',', ':')).encode('utf-8')
        if len(response) > RESPONSE_LIMIT:
            raise ValueError('Private output validation response exceeds its limit')
    except Exception:
        # Model-owned normalisation and file checks may mention values or paths.
        # The supervisor only needs a failed result, never this private detail.
        sys.stdout.write('{"error":"Output validation failed"}\n')
        return 1
    sys.stdout.buffer.write(response + b'\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
