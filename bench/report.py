#!/usr/bin/env python3
"""Compare models across all graded runs. Writes bench/results/report.md, report.html and report.csv.

Usage: bench/report.py [--task <prefix>] [--out <dir>] [--load <group>] [--include-load]

Load test runs (bench/load.sh) are left out of the model tables unless --include-load, and summarised per
concurrency level in "Load tests" (all groups, or only --load <group>).

Rows are harness, target and model, plus the run's BENCH_VARIANT label and "yolo" (BENCH_YOLO=1) when set.
Runs with "exclude": true in grade/human.json are left out entirely.

A cell in the main table reads `mean (min-max) g/n`: mean total (1 to 10, 0 for a failed gate) over the complete runs, the
spread, and how many of the n runs passed the gates. `+k?` means k more runs still need a human score.
report.html shows the same in the browser: a colour scale per task, charts for the load tests.
"""
import csv
import datetime
import glob
import os
import statistics
import sys
from collections import Counter, defaultdict

BENCH = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BENCH)
import score as scorer  # noqa: E402
from lib.rubric import DIMENSIONS  # noqa: E402
from lib import report_html  # noqa: E402

RESULTS = os.environ.get('BENCH_RESULTS', os.path.join(BENCH, 'results'))
LOCAL_NAMES = ('local',)


def label(s):
    """A run's row label: harness, target (local shown as local, its new name), model, variant, yolo."""
    target = 'local' if s['target'] in LOCAL_NAMES else s['target']
    parts = [s['harness'] or '?', target or '?', s['model'] or '?', s['variant'], 'yolo' if s['yolo'] else None]
    return ' '.join(p for p in parts if p)


def fmt(xs, digits=1):
    if not xs:
        return ''
    m = statistics.mean(xs)
    return f'{m:.{digits}f}' if len(xs) == 1 or min(xs) == max(xs) else f'{m:.{digits}f} ({min(xs):g}-{max(xs):g})'


def table(header, rows):
    out = ['| ' + ' | '.join(header) + ' |', '|' + '---|' * len(header)]
    out += ['| ' + ' | '.join(str(c) for c in r) + ' |' for r in rows]
    return '\n'.join(out)


def collect(task_prefix, load_only, include_load):
    """Scores of the graded runs for the model tables, and the load test runs."""
    scores, load_runs = [], []
    for meta in sorted(glob.glob(os.path.join(RESULTS, '*', '*', 'meta.txt'))):
        run = os.path.dirname(meta)
        if not os.path.exists(os.path.join(run, 'grade', 'auto.json')):
            continue
        s = scorer.score(run)
        if not s['task'].startswith(task_prefix) or s['excluded']:
            continue
        s['_dir'] = os.path.relpath(run, RESULTS)
        if s.get('load_group'):
            if not load_only or s['load_group'] == load_only:
                load_runs.append(s)
            if not include_load:
                continue
        scores.append(s)
    return scores, load_runs


def task_cell(rs):
    """One model's runs of one task: complete totals, gates passed, runs, failed gates and runs still pending."""
    failed = Counter(g for r in rs if r['gate']['passed'] is False
                     for g in ('builds', 'behaviour', 'no_harm') if r['gate'][g] is False)
    return {'done': [r['total'] for r in rs if r['complete']], 'gates': sum(1 for r in rs if r['gate']['passed']),
            'n': len(rs), 'pending': sum(1 for r in rs if not r['complete']), 'failed': dict(failed)}


def model_tables(scores):
    """Everything the model tables show, keyed by row label, for the markdown and the HTML report."""
    by = defaultdict(lambda: defaultdict(list))
    for s in scores:
        by[label(s)][s['task']].append(s)
    tasks = sorted({s['task'] for s in scores}, key=lambda t: (len(t.split('-')[0]), t))
    models = sorted(by)
    cells = {(m, t): task_cell(by[m][t]) for m in models for t in tasks if by[m].get(t)}
    dims, cost, tags = {}, {}, {}
    for m in models:
        rs = [r for t in by[m].values() for r in t]
        passed = [r for r in rs if r['gate']['passed']]
        dims[m] = {d: [r['dims'][d]['value'] for r in passed if r['dims'][d]['value'] is not None] for d in DIMENSIONS}
        tel = [r['telemetry'] for r in rs if r['telemetry']]
        out_tok = [x.get('output_tokens', 0) for x in tel]
        cost[m] = {'wall min': [r['wall_min'] for r in rs if r['wall_min'] is not None],
                   'requests': [x.get('model_requests', 0) for x in tel],
                   'input k': [round(x.get('input_tokens', 0) / 1000) for x in tel],
                   'output k': [round(x.get('output_tokens', 0) / 1000, 1) for x in tel],
                   'peak ctx k': [x.get('peak_context', 0) // 1000 for x in tel],
                   'tool errors': [x.get('tool_errors', 0) for x in tel],
                   'unbacked claims': sum(1 for x in tel if x.get('unbacked_claim')),
                   'output k per passing run': sum(out_tok) / len(passed) / 1000 if passed and out_tok else None,
                   'gates': f'{len(passed)}/{len(rs)}'}
        c = Counter()
        for r in rs:
            tag = r['failure_tag'] or (r['failure_tag_suggested'] and r['failure_tag_suggested'] + '*')
            if tag:
                c[tag] += 1
        tags[m] = c
    return {'models': models, 'tasks': tasks, 'cells': cells, 'dims': dims, 'cost': cost, 'tags': tags}


def aggregate_tps(rs):
    """Output tokens of these runs per second of the level's wall clock (first start to last end)."""
    starts = [datetime.datetime.fromisoformat(r['start']) for r in rs if r.get('start')]
    ends = [datetime.datetime.fromisoformat(r['end']) for r in rs if r.get('end')]
    span = (max(ends) - min(starts)).total_seconds() if starts and ends else 0
    return sum((r['telemetry'] or {}).get('output_tokens', 0) for r in rs) / span if span else None


def load_groups(load_runs):
    """Per load test group and concurrency level: the numbers of the "Load tests" tables and charts."""
    groups = defaultdict(lambda: defaultdict(list))
    for r in load_runs:
        groups[(r['load_group'], label(r), r['task'])][r['load_level']].append(r)
    out = []
    for (g, m, t), levels in sorted(groups.items()):
        rows = []
        for lvl in sorted(levels, key=int):
            rs = levels[lvl]
            tel = [r['telemetry'] for r in rs if r['telemetry']]
            finished = [r for r in rs if not r.get('timed_out')]
            rows.append({'agents': int(lvl), 'gates': sum(1 for r in rs if r['gate']['passed']), 'n': len(rs),
                         'auto': [r['auto_mean'] for r in rs],
                         'wall': [r['wall_min'] for r in rs if r['wall_min'] is not None],
                         's_req': [x['request_s_mean'] for x in tel if x.get('request_s_mean') is not None],
                         'tps': [x['output_tps'] for x in tel if x.get('output_tps') is not None],
                         'agg': aggregate_tps(rs),
                         # A timed-out run stretches the level's wall clock; this leaves such runs out
                         'agg_finished': aggregate_tps(finished) if finished and len(finished) < len(rs) else None,
                         'unbacked': sum(1 for x in tel if x.get('unbacked_claim')),
                         'timed_out': len(rs) - len(finished)})
        out.append({'group': g, 'label': m, 'task': t, 'levels': rows})
    return out


def markdown(scores, data, loads):
    """The report as markdown."""
    models, tasks, cells = data['models'], data['tasks'], data['cells']
    short = {t: t.split('-')[0].upper() for t in tasks}
    md = ['# Bench report', '', f"Generated {datetime.datetime.now():%Y-%m-%d %H:%M}. {len(scores)} runs, "
          f"{sum(1 for s in scores if not s['complete'])} still need a human score "
          f"(`bench/review.py next`).", '',
          '## Totals per task', '',
          'Cell: mean total out of 10 over complete runs (min-max), then runs passing the gates / runs. '
          '`+k?` = k runs not yet human graded.', '']
    rows = []
    for m in models:
        row, task_means = [m], []
        for t in tasks:
            c = cells.get((m, t))
            if not c:
                row.append('')
                continue
            cell = f"{fmt(c['done'])} {c['gates']}/{c['n']}" if c['done'] else f"- {c['gates']}/{c['n']}"
            if c['pending']:
                cell += f" +{c['pending']}?"
            if c['done']:
                task_means.append(statistics.mean(c['done']))
            row.append(cell)
        row_pending = sum(cells[(m, t)]['pending'] for t in tasks if (m, t) in cells)
        mean_cell = f'{statistics.mean(task_means):.1f}' if task_means else ''
        # Until every run is graded the mean covers only some tasks (often just the failed gates)
        row.append(f'{mean_cell} +{row_pending}?' if row_pending else mean_cell)
        rows.append(row)
    md.append(table(['harness target model'] + [short[t] for t in tasks] + ['mean of tasks'], rows))

    md += ['', '## Dimensions', '', 'Mean score 1 to 10 over runs that passed the gates (auto and human values).', '']
    rows = [[m] + [f'{statistics.mean(v):.2f}' if v else '' for v in data['dims'][m].values()]
            + [data['cost'][m]['gates']] for m in models]
    md.append(table(['model'] + DIMENSIONS + ['gates'], rows))

    md += ['', '## Cost and behaviour', '', 'Means per run. Unbacked claims: the final message claims a build or '
           'test result but no build or test command was run.', '']
    rows = []
    for m in models:
        c = data['cost'][m]
        per_pass = c['output k per passing run']
        rows.append([m, fmt(c['wall min']), fmt(c['requests'], 0), fmt(c['input k'], 0), fmt(c['output k']),
                     fmt(c['peak ctx k'], 0), fmt(c['tool errors']), c['unbacked claims'],
                     f'{per_pass:.1f}' if per_pass is not None else ''])
    md.append(table(['model', 'wall min', 'requests', 'input k', 'output k', 'peak ctx k', 'tool errors',
                     'unbacked claims', 'output k per passing run'], rows))

    md += ['', '## Failure tags', '', 'Human tag where given, else the suggestion (marked *).', '']
    md.append(table(['model', 'tags'], [[m, ', '.join(f'{k} {v}' for k, v in data['tags'][m].most_common()) or 'none']
                                        for m in models]))

    if loads:
        md += ['', '## Load tests', '', 'Per concurrency level: runs passing the gates, automatic mean (correctness, '
               'procedure, reuse, scope; out of 10, no human scores needed), wall time, time per model request and output '
               'tokens per second per agent (model time only, prefill included), and aggregate output tokens per '
               'second over the level\'s wall clock (all agents, tool time included). A timed-out run stretches that '
               'wall clock, so the next column repeats it without such runs.', '']
        for lg in loads:
            md += [f"**{lg['group']}**: {lg['label']}, {lg['task']}", '']
            rows = [[r['agents'], f"{r['gates']}/{r['n']}", fmt(r['auto']), fmt(r['wall']), fmt(r['s_req']),
                     fmt(r['tps']), f"{r['agg']:.0f}" if r['agg'] is not None else '',
                     f"{r['agg_finished']:.0f}" if r['agg_finished'] is not None else '',
                     r['unbacked'], r['timed_out']] for r in lg['levels']]
            md.append(table(['agents', 'gates', 'auto /10', 'wall min', 's per request', 'out tok/s per agent',
                             'aggregate out tok/s', 'aggregate without timed out', 'unbacked claims', 'timed out'],
                            rows))
            md.append('')

    overrides = [s for s in scores if s['gate_override']]
    if overrides:
        md += ['', '## Gate overrides', '', 'Runs that failed a gate the reviewer judged wrong for them; scored as passed.', '']
        md += [f"- `{s['_dir']}` ({', '.join(k for k, v in s['gate'].items() if k != 'passed' and not v)}): "
               f"{s['gate_override']}" for s in overrides]
    pend = [s for s in scores if not s['complete']]
    if pend:
        md += ['', '## Needs a human score', '']
        md += [f"- `{s['_dir']}`: {', '.join(s['pending'])}" for s in pend]
    if not scores and '## Load tests' in md:
        # Only load runs: the model tables would be empty
        md = md[:md.index('## Totals per task')] + md[md.index('## Load tests'):]
    return md


def write_csv(path, scores, load_runs):
    """One row per run."""
    fields = ['run', 'task', 'harness', 'target', 'model', 'variant', 'yolo', 'start', 'end', 'wall_min', 'load_group', 'load_level', 'auto_mean', 'gate_passed', 'builds', 'behaviour',
              'no_harm'] + DIMENSIONS + [d + '_source' for d in DIMENSIONS] + \
             ['total', 'rank', 'rank_n', 'gate_override', 'complete', 'rules_broken', 'failure_tag', 'failure_tag_suggested', 'model_requests',
              'input_tokens', 'output_tokens', 'peak_context', 'tool_calls', 'tool_errors', 'nonzero_exits',
              'request_s_mean', 'output_tps', 'verify_commands', 'unbacked_claim', 'intervention', 'notes']
    with open(path, 'w', newline='') as f:
        w = csv.DictWriter(f, fields)
        w.writeheader()
        for s in scores + [r for r in load_runs if r not in scores]:
            row = {k: s.get(k) for k in fields if k in s}
            row.update(gate_passed=s['gate']['passed'], builds=s['gate']['builds'], behaviour=s['gate']['behaviour'],
                       no_harm=s['gate']['no_harm'], rules_broken=' '.join(s['rules_broken']))
            for d in DIMENSIONS:
                row[d] = s['dims'][d]['value']
                row[d + '_source'] = s['dims'][d]['source']
            row.update({k: v for k, v in s['telemetry'].items() if k in fields})
            w.writerow(row)


def main(argv):
    task_prefix = argv[argv.index('--task') + 1] if '--task' in argv else ''
    out_dir = argv[argv.index('--out') + 1] if '--out' in argv else RESULTS
    load_only = argv[argv.index('--load') + 1] if '--load' in argv else None
    scores, load_runs = collect(task_prefix, load_only, '--include-load' in argv)
    data = model_tables(scores)
    loads = load_groups(load_runs)
    md = markdown(scores, data, loads)
    os.makedirs(out_dir, exist_ok=True)
    open(os.path.join(out_dir, 'report.md'), 'w').write('\n'.join(md) + '\n')
    pending = [(s['_dir'], s['pending']) for s in scores if not s['complete']]
    overrides = [(s['_dir'], s['gate_override']) for s in scores if s['gate_override']]
    open(os.path.join(out_dir, 'report.html'), 'w').write(report_html.render(data, loads, pending, len(scores), overrides))
    write_csv(os.path.join(out_dir, 'report.csv'), scores, load_runs)
    print('\n'.join(md))
    print(f"\nWritten {os.path.join(out_dir, 'report.md')}, report.html and report.csv")


if __name__ == '__main__':
    main(sys.argv[1:])
