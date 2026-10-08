#!/usr/bin/env python3
"""Compare models across all graded runs. Writes bench/results/report.md and report.csv.

Usage: bench/report.py [--task <prefix>] [--out <dir>] [--load <group>] [--include-load]

Load test runs (bench/load.sh) are left out of the model tables unless --include-load, and summarised per
concurrency level in "Load tests" (all groups, or only --load <group>).

A cell in the main table reads `mean (min-max) g/n`: mean total out of 18 over the complete runs, the
spread, and how many of the n runs passed the gates. `+k?` means k more runs still need a human score.
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

RESULTS = os.environ.get('BENCH_RESULTS', os.path.join(BENCH, 'results'))


def label(s):
    return f"{s['harness'] or '?'} {s['target'] or '?'} {s['model'] or '?'}"


def fmt(xs, digits=1):
    if not xs:
        return ''
    m = statistics.mean(xs)
    return f'{m:.{digits}f}' if len(xs) == 1 or min(xs) == max(xs) else f'{m:.{digits}f} ({min(xs):g}-{max(xs):g})'


def table(header, rows):
    out = ['| ' + ' | '.join(header) + ' |', '|' + '---|' * len(header)]
    out += ['| ' + ' | '.join(str(c) for c in r) + ' |' for r in rows]
    return '\n'.join(out)


def main(argv):
    task_prefix = argv[argv.index('--task') + 1] if '--task' in argv else ''
    out_dir = argv[argv.index('--out') + 1] if '--out' in argv else RESULTS
    load_only = argv[argv.index('--load') + 1] if '--load' in argv else None
    include_load = '--include-load' in argv
    scores, load_runs = [], []
    for meta in sorted(glob.glob(os.path.join(RESULTS, '*', '*', 'meta.txt'))):
        run = os.path.dirname(meta)
        if os.path.exists(os.path.join(run, 'grade', 'auto.json')):
            s = scorer.score(run)
            if s['task'].startswith(task_prefix):
                s['_dir'] = os.path.relpath(run, RESULTS)
                if s.get('load_group'):
                    if not load_only or s['load_group'] == load_only:
                        load_runs.append(s)
                    if not include_load:
                        continue
                scores.append(s)
    by = defaultdict(lambda: defaultdict(list))
    for s in scores:
        by[label(s)][s['task']].append(s)
    tasks = sorted({s['task'] for s in scores}, key=lambda t: (len(t.split('-')[0]), t))
    short = {t: t.split('-')[0].upper() for t in tasks}
    models = sorted(by)

    md = [f'# Bench report', '', f"Generated {datetime.datetime.now():%Y-%m-%d %H:%M}. {len(scores)} runs, "
          f"{sum(1 for s in scores if not s['complete'])} still need a human score "
          f"(`bench/review.py next`).", '',
          '## Totals per task', '',
          'Cell: mean total out of 18 over complete runs (min-max), then runs passing the gates / runs. '
          '`+k?` = k runs not yet human graded.', '']
    rows = []
    for m in models:
        row, task_means = [m], []
        for t in tasks:
            rs = by[m].get(t, [])
            if not rs:
                row.append(''); continue
            done = [r['total'] for r in rs if r['complete']]
            gates = sum(1 for r in rs if r['gate']['passed'])
            cell = f"{fmt(done)} {gates}/{len(rs)}" if done else f"- {gates}/{len(rs)}"
            if len(done) < len(rs):
                cell += f" +{len(rs) - len(done)}?"
            if done:
                task_means.append(statistics.mean(done))
            row.append(cell)
        row.append(f'{statistics.mean(task_means):.1f}' if task_means else '')
        rows.append(row)
    md.append(table(['harness target model'] + [short[t] for t in tasks] + ['mean of tasks'], rows))

    md += ['', '## Dimensions', '', 'Mean score 0 to 3 over runs that passed the gates (auto and human values).', '']
    rows = []
    for m in models:
        rs = [r for t in by[m].values() for r in t if r['gate']['passed']]
        row = [m]
        for d in DIMENSIONS:
            vals = [r['dims'][d]['value'] for r in rs if r['dims'][d]['value'] is not None]
            row.append(f'{statistics.mean(vals):.2f}' if vals else '')
        row.append(f"{sum(1 for t in by[m].values() for r in t if r['gate']['passed'])}"
                   f"/{sum(len(t) for t in by[m].values())}")
        rows.append(row)
    md.append(table(['model'] + DIMENSIONS + ['gates'], rows))

    md += ['', '## Cost and behaviour', '', 'Means per run. Unbacked claims: the final message claims a build or '
           'test result but no build or test command was run.', '']
    rows = []
    for m in models:
        rs = [r for t in by[m].values() for r in t]
        tel = [r['telemetry'] for r in rs if r['telemetry']]
        passed = sum(1 for r in rs if r['gate']['passed'])
        out_tok = [x.get('output_tokens', 0) for x in tel]
        rows.append([m, fmt([r['wall_min'] for r in rs if r['wall_min'] is not None]),
                     fmt([x.get('model_requests', 0) for x in tel], 0),
                     fmt([round(x.get('input_tokens', 0) / 1000) for x in tel], 0),
                     fmt([round(x.get('output_tokens', 0) / 1000, 1) for x in tel]),
                     fmt([x.get('peak_context', 0) // 1000 for x in tel], 0),
                     fmt([x.get('tool_errors', 0) for x in tel]),
                     sum(1 for x in tel if x.get('unbacked_claim')),
                     f'{sum(out_tok) / passed / 1000:.1f}' if passed and out_tok else ''])
    md.append(table(['model', 'wall min', 'requests', 'input k', 'output k', 'peak ctx k', 'tool errors',
                     'unbacked claims', 'output k per passing run'], rows))

    md += ['', '## Failure tags', '', 'Human tag where given, else the suggestion (marked *).', '']
    rows = []
    for m in models:
        c = Counter()
        for t in by[m].values():
            for r in t:
                tag = r['failure_tag'] or (r['failure_tag_suggested'] and r['failure_tag_suggested'] + '*')
                if tag:
                    c[tag] += 1
        rows.append([m, ', '.join(f'{k} {v}' for k, v in c.most_common()) or 'none'])
    md.append(table(['model', 'tags'], rows))

    if load_runs:
        md += ['', '## Load tests', '', 'Per concurrency level: runs passing the gates, automatic subtotal (correctness, '
               'procedure, reuse, scope; max 12, no human scores needed), wall time, time per model request and output '
               'tokens per second per agent (model time only, prefill included), and aggregate output tokens per '
               'second over the level\'s wall clock (all agents, tool time included).', '']
        groups = defaultdict(lambda: defaultdict(list))
        for r in load_runs:
            groups[(r['load_group'], label(r), r['task'])][r['load_level']].append(r)
        for (g, m, t), levels in sorted(groups.items()):
            md += [f'**{g}**: {m}, {t}', '']
            rows = []
            for lvl in sorted(levels):
                rs = levels[lvl]
                tel = [r['telemetry'] for r in rs if r['telemetry']]
                starts = [datetime.datetime.fromisoformat(r['start']) for r in rs if r.get('start')]
                ends = [datetime.datetime.fromisoformat(r['end']) for r in rs if r.get('end')]
                span = (max(ends) - min(starts)).total_seconds() if starts and ends else 0
                agg = sum(x.get('output_tokens', 0) for x in tel) / span if span else 0
                rows.append([lvl, f"{sum(1 for r in rs if r['gate']['passed'])}/{len(rs)}",
                             fmt([r['auto_subtotal'] for r in rs]), fmt([r['wall_min'] for r in rs if r['wall_min'] is not None]),
                             fmt([x['request_s_mean'] for x in tel if x.get('request_s_mean') is not None]),
                             fmt([x['output_tps'] for x in tel if x.get('output_tps') is not None]),
                             f'{agg:.0f}', sum(1 for x in tel if x.get('unbacked_claim')),
                             sum(1 for r in rs if r.get('timed_out'))])
            md.append(table(['agents', 'gates', 'auto /12', 'wall min', 's per request', 'out tok/s per agent',
                             'aggregate out tok/s', 'unbacked claims', 'timed out'], rows))
            md.append('')

    pend = [s for s in scores if not s['complete']]
    if pend:
        md += ['', '## Needs a human score', '']
        md += [f"- `{s['_dir']}`: {', '.join(s['pending'])}" for s in pend]
    if not scores and '## Load tests' in md:
        # Only load runs: the model tables would be empty
        md = md[:md.index('## Totals per task')] + md[md.index('## Load tests'):]
    os.makedirs(out_dir, exist_ok=True)
    open(os.path.join(out_dir, 'report.md'), 'w').write('\n'.join(md) + '\n')

    fields = ['run', 'task', 'harness', 'target', 'model', 'start', 'end', 'wall_min', 'load_group', 'load_level', 'auto_subtotal', 'gate_passed', 'builds', 'behaviour',
              'no_harm'] + DIMENSIONS + [d + '_source' for d in DIMENSIONS] + \
             ['total', 'complete', 'rules_broken', 'failure_tag', 'failure_tag_suggested', 'model_requests',
              'input_tokens', 'output_tokens', 'peak_context', 'tool_calls', 'tool_errors', 'nonzero_exits',
              'request_s_mean', 'output_tps', 'verify_commands', 'unbacked_claim', 'intervention', 'notes']
    with open(os.path.join(out_dir, 'report.csv'), 'w', newline='') as f:
        w = csv.DictWriter(f, fields)
        w.writeheader()
        for s in scores + [r for r in load_runs if r not in scores]:
            row = {k: s.get(k) for k in fields if k in s}
            row.update(gate_passed=s['gate']['passed'], builds=s['gate']['builds'], behaviour=s['gate']['behaviour'],
                       no_harm=s['gate']['no_harm'], rules_broken=' '.join(s['rules_broken']))
            for d in DIMENSIONS:
                row[d] = s['dims'][d]['value']; row[d + '_source'] = s['dims'][d]['source']
            row.update({k: v for k, v in s['telemetry'].items() if k in fields})
            w.writerow(row)
    print('\n'.join(md))
    print(f"\nWritten {os.path.join(out_dir, 'report.md')} and report.csv")


if __name__ == '__main__':
    main(sys.argv[1:])
