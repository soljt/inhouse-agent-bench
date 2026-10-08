#!/usr/bin/env python3
"""Walk through one run for human grading, then record the grades in grade/human.json.

  bench/review.py <run-dir>          review this run
  bench/review.py next [--blind]     review the next run that still needs a human score
  bench/review.py pending            list runs that still need a human score

Shows the prompt, the agent's final message, the automatic checks and suggestions, telemetry, the
commands it ran and its diff (T12: its REVIEW.md and the findings that matched nothing) in a pager,
then opens the grade file in $EDITOR. --blind hides harness and model and picks a random pending run.
"""
import glob
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile

BENCH = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BENCH)
import score as scorer  # noqa: E402
sys.path.insert(0, os.path.join(BENCH, 'lib'))
import transcript  # noqa: E402

RESULTS = os.environ.get('BENCH_RESULTS', os.path.join(BENCH, 'results'))


def runs():
    return sorted(os.path.dirname(m) for m in glob.glob(os.path.join(RESULTS, '*', '*', 'meta.txt')))


def pending():
    out = []
    for r in runs():
        if not os.path.exists(os.path.join(r, 'grade', 'auto.json')):
            continue
        s = scorer.score(r)
        if not s['complete']:
            out.append(r)
    return out


def blind_id(run_dir):
    return hashlib.sha1(os.path.basename(run_dir).encode()).hexdigest()[:6]


def read(path, limit=None):
    if not os.path.exists(path):
        return f'({os.path.basename(path)} missing)\n'
    text = open(path, encoding='utf-8', errors='replace').read()
    return text if limit is None or len(text) <= limit else text[:limit] + f'\n... ({len(text) - limit} more characters)\n'


def section(title, body):
    return f'\n\033[1;36m==== {title} ====\033[0m\n{body.rstrip()}\n'


def page(run_dir, blind):
    s = scorer.score(run_dir)
    tel = transcript.read(run_dir) or {}
    g = os.path.join(run_dir, 'grade')
    who = f"run {blind_id(run_dir)}" if blind else f"{s['harness']}/{s['model']}  ({os.path.basename(run_dir)})"
    out = [f"\033[1m{s['task']}  |  {who}\033[0m  wall {s['wall_min']} min"]
    out.append(section('Prompt', read(os.path.join(run_dir, 'prompt.md'))))
    out.append(section('Agent final message', tel.get('final_message') or '(no transcript found)'))
    gate = '  '.join(f"{k}={'ok' if v else 'FAIL'}" for k, v in s['gate'].items() if k != 'passed')
    rows = [f'gate: {gate}', f"rules broken: {', '.join(s['rules_broken']) or 'none'}", '',
            f"{'dimension':<14}{'auto':>6}{'human':>7}"]
    rows += [f"{d:<14}{str(v['auto']):>6}{str(v['human']):>7}" for d, v in s['dims'].items()]
    rows += ['', f"suggested failure tag: {s['failure_tag_suggested']}"]
    out.append(section('Scores', '\n'.join(rows)))
    out.append(section('Automatic checks (auto.json)', read(os.path.join(g, 'auto.json'))))
    t = s['telemetry']
    flag = '  <-- claims verification but ran no build or test command' if t.get('unbacked_claim') else ''
    out.append(section('Telemetry', '\n'.join(f'{k}: {v}' for k, v in t.items()) + flag))
    cmds = tel.get('commands') or []
    out.append(section(f'Commands run ({len(cmds)}; build/test marked >)',
                       '\n'.join(('> ' if transcript.VERIFY_CMD.search(c) else '  ') + c.replace('\n', ' ')[:200]
                                 for c in cmds)))
    if s['task'].startswith('t12-'):
        out.append(section('REVIEW.md', read(os.path.join(g, 'REVIEW.md'))))
        out.append(section('Planted defects', read(os.path.join(g, 'review-matched.txt'))))
        out.append(section('Findings that matched no planted defect (false alarm, or a real find?)',
                           read(os.path.join(g, 'review-unmatched.txt')) or '(none)'))
    out.append(section('Diff', read(os.path.join(g, 'diff.patch'), 200000)))
    pager = os.environ.get('PAGER', 'less -R')
    subprocess.run(pager, shell=True, input='\n'.join(out).encode())
    return s


def edit(run_dir, s, blind):
    path = os.path.join(run_dir, 'grade', 'human.json')
    if not os.path.exists(path):
        auto = {d: v['auto'] for d, v in s['dims'].items()}
        auto['failure_tag'] = s['failure_tag_suggested']
        json.dump(scorer.human_template(auto), open(path, 'w'), indent=1)
    editor = os.environ.get('EDITOR', 'vi')
    work = path
    if blind:
        work = os.path.join(tempfile.mkdtemp(), f'grade-{blind_id(run_dir)}.json')
        shutil.copy(path, work)
    while True:
        subprocess.run(f'{editor} "{work}"', shell=True)
        try:
            json.load(open(work))
            break
        except ValueError as e:
            input(f'Not valid JSON ({e}). Enter to edit again. ')
    if work != path:
        shutil.copy(work, path)
    print(scorer.summary_line(scorer.score(run_dir)) if not blind else
          f"run {blind_id(run_dir)}: total {scorer.score(run_dir)['total']}")


def main(args):
    if not args:
        sys.exit(__doc__)
    blind = '--blind' in args
    args = [a for a in args if a != '--blind']
    if args[0] == 'pending':
        for r in pending():
            print(os.path.relpath(r, RESULTS))
        return
    if args[0] == 'next':
        todo = pending()
        if not todo:
            print('Nothing pending.')
            return
        run_dir = random.choice(todo) if blind else todo[0]
        print(f'{len(todo)} runs pending.')
    else:
        run_dir = os.path.abspath(args[0])
    s = page(run_dir, blind)
    edit(run_dir, s, blind)


if __name__ == '__main__':
    main(sys.argv[1:])
