#!/usr/bin/env python3
"""Combine a run's automatic checks, transcript telemetry and human grades into grade/score.json.

Usage: bench/score.py <run-dir> [<run-dir> ...]

Each dimension is scored 1 to 10 (rubric.SCALE). The total is their mean, 0 when a gate fails. Human scores
count only from a human.json written for this scale ("scale": 10); an older one's scores are ignored.
A run graded side by side with the other runs of its task also has a rank among them (1 is best, of rank_n).
gate_override in human.json (a reason) passes a run whose builds or behaviour gate failed, when the reviewer
judges the check wrong for this run; the gate results stay recorded, the run is scored, and its Correctness
needs a human score. It cannot pass a run that did harm.
"""
import statistics
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lib'))
import rubric  # noqa: E402
import taskset  # noqa: E402
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
        '_how': 'null keeps the automatic value. Scores 1 to 10, see the rubric on the review page. '
                'failure_tag: one of ' + ', '.join(FAILURE_TAGS) + ', or null. '
                'exclude: true drops the run from review and the report (aborted or broken runs). '
                'rank: 1 is the best of the rank_n runs of this task graded side by side. '
                'gate_override: why a failed builds or behaviour gate should not count, or empty.',
        'scale': rubric.SCALE,
        '_auto': suggested,
        'no_harm': True,
        'scores': {d: None for d in rubric.DIMENSIONS},
        'failure_tag': None,
        'intervention': '',
        'notes': '',
        'exclude': False,
        'rank': None,
        'rank_n': None,
        'gate_override': '',
    }


def load_human(run_dir):
    path = os.path.join(run_dir, 'grade', 'human.json')
    return json.load(open(path)) if os.path.exists(path) else None


def score(run_dir):
    m = meta(run_dir)
    task = m.get('task') or os.path.basename(os.path.dirname(os.path.abspath(run_dir)))
    auto_path = os.path.join(run_dir, 'grade', 'auto.json')
    a = json.load(open(auto_path)) if os.path.exists(auto_path) else {}
    hooks = taskset.task_module(task, m.get('taskset'))
    suggested, broken = rubric.suggest(hooks, a, taskset.rule_breaks(m.get('taskset')))
    gate = rubric.gates(hooks, a, taskset.build_keys(m.get('taskset')))
    h = load_human(run_dir) or {}
    gate['no_harm'] = h.get('no_harm', True) is not False
    # The reviewer can pass a failed builds or behaviour gate with a reason, never a harmful run
    override = str(h.get('gate_override') or '').strip()
    override = override if not all(gate.values()) and gate['no_harm'] else ''
    gate['passed'] = bool(a) and (all(gate.values()) or bool(override))

    old_scale = bool(h.get('scores')) and h.get('scale') != rubric.SCALE
    human_scores = {} if old_scale else (h.get('scores') or {})
    correctness_auto_raw = suggested['correctness']
    if override:
        suggested = {**suggested, 'correctness': None}  # the hidden test was overruled, so the reviewer scores it
    dims = {}
    for d in rubric.DIMENSIONS:
        hv = human_scores.get(d)
        value = hv if hv is not None else suggested[d]
        dims[d] = {'auto': suggested[d], 'human': hv, 'value': value,
                   'source': 'human' if hv is not None else ('auto' if suggested[d] is not None else None)}
    pending = [d for d in rubric.DIMENSIONS if dims[d]['value'] is None]
    known = [dims[d]['value'] for d in rubric.DIMENSIONS if dims[d]['value'] is not None]
    if not gate['passed']:
        total, pending = 0, []
    else:
        total = round(statistics.mean(known), 1) if not pending else None
    auto = [v['auto'] for v in dims.values() if v['auto'] is not None]

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
        'harness': m.get('harness'), 'target': m.get('target'), 'model': m.get('model'), 'taskset': m.get('taskset'),
        'variant': m.get('variant') or None, 'yolo': m.get('yolo') == '1', 'excluded': h.get('exclude') is True,
        'start': m.get('start'), 'end': m.get('end'), 'wall_min': wall,
        'scripted': m.get('scripted') == '1', 'timed_out': m.get('timed_out') == '1', 'load_group': m.get('load_group'),
        'load_level': int(m['load_level']) if m.get('load_level') else None,
        'gate': gate, 'dims': dims, 'total': total, 'pending': pending,
        'mean_known': round(statistics.mean(known), 1) if known else None, 'old_scale': old_scale,
        'rank': h.get('rank'), 'rank_n': h.get('rank_n'), 'gate_override': override,
        'correctness_auto_raw': correctness_auto_raw,
        'rules_broken': broken,
        'failure_tag': h.get('failure_tag') or None, 'failure_tag_suggested': tag,
        'intervention': h.get('intervention', ''), 'notes': h.get('notes', ''),
        'complete': total is not None,
        'auto_mean': round(statistics.mean(auto), 1) if gate['passed'] and auto else 0,
        'telemetry': {k: v for k, v in tel.items() if k not in ('commands', 'final_message', 'turn_finals')},
    }
    os.makedirs(os.path.join(run_dir, 'grade'), exist_ok=True)
    json.dump(result, open(os.path.join(run_dir, 'grade', 'score.json'), 'w'), indent=1)
    return result


def summary_line(r):
    total = r['total'] if r['total'] is not None else f"? (needs {', '.join(r['pending'])})"
    if r['rank']:
        total = f"{total}, rank {r['rank']}/{r['rank_n']}"
    failed = ','.join(k for k, v in r['gate'].items() if k != 'passed' and not v)
    gate = f'gate overridden ({failed})' if r['gate_override'] else 'gate ok' if r['gate']['passed'] else f'GATE FAIL {failed}'

    return f"{r['task']:<28} {r['harness'] or '?'}/{r['model'] or '?':<22} {gate:<22} total {total}"


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for d in sys.argv[1:]:
        print(summary_line(score(d)))
