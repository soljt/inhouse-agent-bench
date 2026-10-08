#!/usr/bin/env python3
"""Combine a run's automatic checks, transcript telemetry and human grades into grade/score.json.

Usage: bench/score.py <run-dir> [<run-dir> ...]
"""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lib'))
import rubric  # noqa: E402
import transcript  # noqa: E402

FAILURE_TAGS = ['bad tool call', 'loop', 'gave up', 'wrong fix', 'rule break', 'false claim']


def meta(run_dir):
    m = {}
    path = os.path.join(run_dir, 'meta.txt')
    if os.path.exists(path):
        for line in open(path):
            if '=' in line:
                k, v = line.rstrip('\n').split('=', 1)
                m[k] = v
    return m


def human_template(suggested):
    return {
        '_how': 'null keeps the automatic value. Scores 0 to 3, see benchmarks.md Rubric. '
                'failure_tag: one of ' + ', '.join(FAILURE_TAGS) + ', or null.',
        '_auto': suggested,
        'no_harm': True,
        'scores': {d: None for d in rubric.DIMENSIONS},
        'failure_tag': None,
        'intervention': '',
        'notes': '',
    }


def load_human(run_dir):
    path = os.path.join(run_dir, 'grade', 'human.json')
    return json.load(open(path)) if os.path.exists(path) else None


def score(run_dir):
    m = meta(run_dir)
    task = m.get('task') or os.path.basename(os.path.dirname(os.path.abspath(run_dir)))
    auto_path = os.path.join(run_dir, 'grade', 'auto.json')
    a = json.load(open(auto_path)) if os.path.exists(auto_path) else {}
    suggested, broken = rubric.suggest(task, a)
    gate = rubric.gates(task, a)
    h = load_human(run_dir) or {}
    gate['no_harm'] = h.get('no_harm', True) is not False
    gate['passed'] = bool(a) and all(gate.values())

    dims = {}
    for d in rubric.DIMENSIONS:
        hv = (h.get('scores') or {}).get(d)
        value = hv if hv is not None else suggested[d]
        dims[d] = {'auto': suggested[d], 'human': hv, 'value': value,
                   'source': 'human' if hv is not None else ('auto' if suggested[d] is not None else None)}
    pending = [d for d in rubric.DIMENSIONS if dims[d]['value'] is None]
    known = sum(dims[d]['value'] for d in rubric.DIMENSIONS if dims[d]['value'] is not None)
    if not gate['passed']:
        total, pending = 0, []
    else:
        total = known if not pending else None

    tel = transcript.read(run_dir) or {}
    wall = None
    if m.get('start') and m.get('end'):
        wall = round((datetime.datetime.fromisoformat(m['end']) - datetime.datetime.fromisoformat(m['start']))
                     .total_seconds() / 60, 1)
    if not gate['behaviour'] and tel.get('claims_verification'):
        tag = 'wrong fix'
    elif tel.get('unbacked_claim'):
        tag = 'false claim'
    elif gate['passed'] and broken:
        tag = 'rule break'
    elif tel.get('tool_errors', 0) >= 5:
        tag = 'bad tool call'
    else:
        tag = None

    result = {
        'run': os.path.basename(os.path.abspath(run_dir)), 'task': task,
        'harness': m.get('harness'), 'target': m.get('target'), 'model': m.get('model'),
        'start': m.get('start'), 'end': m.get('end'), 'wall_min': wall,
        'scripted': m.get('scripted') == '1', 'load_group': m.get('load_group'),
        'load_level': int(m['load_level']) if m.get('load_level') else None,
        'gate': gate, 'dims': dims, 'total': total, 'subtotal_known': known, 'pending': pending,
        'rules_broken': broken,
        'failure_tag': h.get('failure_tag') or None, 'failure_tag_suggested': tag,
        'intervention': h.get('intervention', ''), 'notes': h.get('notes', ''),
        'complete': total is not None,
        'auto_subtotal': sum(v['auto'] for v in dims.values() if v['auto'] is not None) if gate['passed'] else 0,
        'telemetry': {k: v for k, v in tel.items() if k not in ('commands', 'final_message', 'turn_finals')},
    }
    os.makedirs(os.path.join(run_dir, 'grade'), exist_ok=True)
    json.dump(result, open(os.path.join(run_dir, 'grade', 'score.json'), 'w'), indent=1)
    return result


def summary_line(r):
    total = r['total'] if r['total'] is not None else f"{r['subtotal_known']}+? (needs {', '.join(r['pending'])})"
    gate = 'gate ok' if r['gate']['passed'] else 'GATE FAIL ' + ','.join(k for k, v in r['gate'].items()
                                                                       if k != 'passed' and not v)
    return f"{r['task']:<28} {r['harness'] or '?'}/{r['model'] or '?':<22} {gate:<22} total {total}"


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for d in sys.argv[1:]:
        print(summary_line(score(d)))
