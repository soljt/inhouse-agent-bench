#!/usr/bin/env python3
"""Walk through one run for human grading, then record the grades in grade/human.json.

  bench/review.py <run-dir> [--pager]         review this run
  bench/review.py next [--blind] [--pager]    review the next run that still needs a human score
  bench/review.py pending                     list runs that still need a human score

Load test runs (bench/load.sh) and runs with "exclude": true in grade/human.json are never pending.

Shows the prompt, the agent's final message, the automatic checks and suggestions, telemetry, the
commands it ran, its diff and the grader logs (T12: its REVIEW.md and the findings that matched nothing),
then opens the grade file in $EDITOR. With a display the run opens as a local page in the browser
(rendered markdown, side by side diff); --pager or no display shows it in $PAGER instead.
--blind hides harness and model and picks a random pending run.
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
import webbrowser

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
        if not s['complete'] and not s['load_group'] and not s['excluded']:
            out.append(r)
    return out


def blind_id(run_dir):
    return hashlib.sha1(os.path.basename(run_dir).encode()).hexdigest()[:6]


def read(path, limit=None):
    if not os.path.exists(path):
        return f'({os.path.basename(path)} missing)\n'
    text = open(path, encoding='utf-8', errors='replace').read()
    return text if limit is None or len(text) <= limit else text[:limit] + f'\n... ({len(text) - limit} more characters)\n'


def sections(run_dir, s, blind):
    """What a reviewer sees, as (title, kind, body). kind tells the renderers how to show body:
    md and text are strings, table is a list of rows, diff is a patch, logs is a list of (name, text)."""
    tel = transcript.read(run_dir) or {}
    g = os.path.join(run_dir, 'grade')
    out = [('Prompt', 'md', read(os.path.join(run_dir, 'prompt.md')))]
    finals = tel.get('turn_finals') or []
    if len(finals) > 1:
        out += [(f'Agent final message, turn {i} of {len(finals)}', 'md', f or '(empty)') for i, f in enumerate(finals, 1)]
    else:
        out.append(('Agent final message', 'md', tel.get('final_message') or '(no transcript found)'))
    rows = [['gate ' + k, 'ok' if v else 'FAIL'] for k, v in s['gate'].items() if k != 'passed']
    rows += [['rules broken', ', '.join(s['rules_broken']) or 'none'],
             ['suggested failure tag', s['failure_tag_suggested']]]
    dash = lambda x: '-' if x is None else x
    rows += [[d, f"auto {dash(v['auto'])}, human {dash(v['human'])}"] for d, v in s['dims'].items()]
    out.append(('Scores', 'table', rows))
    auto_path = os.path.join(g, 'auto.json')
    auto = json.load(open(auto_path)) if os.path.exists(auto_path) else {}
    out.append(('Automatic checks (auto.json)', 'table', [[k, v] for k, v in auto.items()]))
    t = s['telemetry']
    rows = [[k, v] for k, v in t.items() if not (blind and k in ('harness', 'model'))]
    if t.get('unbacked_claim'):
        rows.append(['WARNING', 'claims verification but ran no build or test command'])
    out.append(('Telemetry', 'table', rows))
    cmds = tel.get('commands') or []
    out.append((f'Commands run ({len(cmds)}; build/test marked >)', 'text',
                '\n'.join(('> ' if transcript.VERIFY_CMD.search(c) else '  ') + c.replace('\n', ' ')[:200] for c in cmds)))
    if s['task'].startswith('t12-'):
        out.append(('REVIEW.md', 'md', read(os.path.join(g, 'REVIEW.md'))))
        out.append(('Planted defects', 'text', read(os.path.join(g, 'review-matched.txt'))))
        out.append(('Findings that matched no planted defect (false alarm, or a real find?)', 'text',
                    read(os.path.join(g, 'review-unmatched.txt')) or '(none)'))
    out.append(('Diff', 'diff', read(os.path.join(g, 'diff.patch'), 2_000_000)))
    logs = sorted(glob.glob(os.path.join(g, '*.log')) + glob.glob(os.path.join(g, '*.txt')))
    out.append(('Grader logs', 'logs', [(os.path.basename(f), read(f, 100_000)) for f in logs]))
    return out


def as_text(kind, body):
    if kind == 'table':
        return '\n'.join(f'{k}: {v}' for k, v in body)
    if kind == 'logs':
        return '\n'.join(name for name, _ in body) + '\n(see grade/ in the run folder)'
    return body


def show_in_pager(header, secs):
    out = [f'\033[1m{header}\033[0m']
    out += [f'\n\033[1;36m==== {title} ====\033[0m\n{as_text(kind, body).rstrip()}\n' for title, kind, body in secs]
    subprocess.run(os.environ.get('PAGER', 'less -R'), shell=True, input='\n'.join(out).encode())


def show_in_browser(header, secs, name):
    """Writes a self contained page to a temp folder and opens it. The page renders markdown and the diff itself."""
    data = json.dumps({'header': header, 'sections': [{'title': t, 'kind': k, 'body': b} for t, k, b in secs]},
                      default=str).replace('</', '<\\/')
    html = open(os.path.join(BENCH, 'lib', 'review_page.html')).read().replace('/*DATA*/null', data)
    path = os.path.join(tempfile.mkdtemp(prefix='bench-review-'), f'{name}.html')
    open(path, 'w').write(html)
    webbrowser.open('file://' + path)
    print(f'Opened {path} in the browser.')


def page(run_dir, blind, pager):
    s = scorer.score(run_dir)
    who = f"run {blind_id(run_dir)}" if blind else f"{s['harness']}/{s['model']}  ({os.path.basename(run_dir)})"
    header = f"{s['task']}  |  {who}  |  wall {s['wall_min']} min"
    secs = sections(run_dir, s, blind)
    if pager or not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
        show_in_pager(header, secs)
    else:
        show_in_browser(header, secs, f"review-{blind_id(run_dir)}" if blind else os.path.basename(run_dir))
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
    pager = '--pager' in args
    args = [a for a in args if a not in ('--blind', '--pager')]
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
    s = page(run_dir, blind, pager)
    edit(run_dir, s, blind)


if __name__ == '__main__':
    main(sys.argv[1:])
