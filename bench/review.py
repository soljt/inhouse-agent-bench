#!/usr/bin/env python3
"""Walk through runs for human grading and record the grades in grade/human.json.

  bench/review.py next [--blind] [--pager]    grade the tasks that still have runs needing you, one task after the other
  bench/review.py task <task> [--blind]       grade every run of this task side by side and rank them
  bench/review.py <run-dir> [--pager]         grade this run on its own (no ranking)
  bench/review.py pending                     list runs that still need a human score or a rank

Load test runs (bench/load.sh) and runs with "exclude": true in grade/human.json are never pending.

With a display this runs a small server on 127.0.0.1 and opens one page per task in the browser: a Task tab
(prompt, rubric, reference solution), one tab per run (the agent's final message, the automatic checks with
what each one means, telemetry, the commands it ran, its diff, the grader logs and any sections the task adds,
e.g. T12's review findings) and a Grade tab with every run's scores side by side and their ranking.
"Save and next task" moves on to the next task with pending runs. Ctrl+C stops it.

A run is pending when a dimension has neither a human nor an automatic score, or when its task has several
runs and they have not all been ranked together (a new run of the task, e.g. from another model, reopens the
ranking of all of them). Runs that failed a gate score 0 and need no scores, but are ranked with the others.

--pager, or no display, shows one run in $PAGER and opens the grade file in $EDITOR instead (no ranking).
--blind hides harness and model, and gives the runs of a task letters in a random order.
"""
import datetime
import glob
import hashlib
import json
import os
import random
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BENCH = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BENCH)
import score as scorer  # noqa: E402
sys.path.insert(0, os.path.join(BENCH, 'lib'))
import rubric  # noqa: E402
import taskset  # noqa: E402
import transcript  # noqa: E402

RESULTS = os.environ.get('BENCH_RESULTS', os.path.join(BENCH, 'results'))


def runs():
    return sorted(os.path.dirname(m) for m in glob.glob(os.path.join(RESULTS, '*', '*', 'meta.txt')))


def gradable(run_dir, s):
    """A run that takes part in grading: graded automatically, not a load test run, not excluded, and from the
    current task set (or one that did not record its task set)."""
    if not os.path.exists(os.path.join(run_dir, 'grade', 'auto.json')) or s['load_group'] or s['excluded']:
        return False
    return not s['taskset'] or os.path.abspath(s['taskset']) == os.path.abspath(taskset.root())


def task_runs(task):
    """The gradable runs of a task, oldest first, with their scores: [(run_dir, score)]."""
    out = []
    for meta_path in sorted(glob.glob(os.path.join(RESULTS, task, '*', 'meta.txt'))):
        run_dir = os.path.dirname(meta_path)
        s = scorer.score(run_dir)
        if gradable(run_dir, s):
            out.append((run_dir, s))
    return out


def needs_rank(s, n):
    """True when the run's task has n > 1 runs and this one was not ranked among all n."""
    return n > 1 and (not s['rank'] or s['rank_n'] != n)


def pending():
    """Runs that still need a human score or a rank."""
    out = []
    for task in sorted({os.path.basename(os.path.dirname(r)) for r in runs()}):
        group = task_runs(task)
        out += [r for r, s in group if not s['complete'] or needs_rank(s, len(group))]
    return out


def pending_tasks():
    """Tasks with at least one pending run, in task order."""
    return sorted({os.path.basename(os.path.dirname(r)) for r in pending()})


def blind_id(run_dir):
    """Short stable id for a run that does not reveal harness or model. Task and run folder, so two tasks
    started in the same second still get different ids."""
    run_dir = os.path.abspath(run_dir)
    key = os.path.join(os.path.basename(os.path.dirname(run_dir)), os.path.basename(run_dir))
    return hashlib.sha1(key.encode()).hexdigest()[:6]


def read(path, limit=None):
    if not os.path.exists(path):
        return f'({os.path.basename(path)} missing)\n'
    text = open(path, encoding='utf-8', errors='replace').read()
    return text if limit is None or len(text) <= limit else text[:limit] + f'\n... ({len(text) - limit} more characters)\n'


def auto_checks(run_dir, s):
    """auto.json as rows for the checks table: key, value, the good value and what the key measures."""
    path = os.path.join(run_dir, 'grade', 'auto.json')
    auto = json.load(open(path)) if os.path.exists(path) else {}
    docs = taskset.auto_keys(s['task'], s.get('taskset'))
    rows = []
    for key, value in auto.items():
        base = key[:-len('_summary')] if key.endswith('_summary') else None
        if key in docs:
            good, meaning = docs[key]
        elif base in docs:
            good, meaning = None, f'Summary of {base}'
        else:
            good, meaning = None, ''
        rows.append({'key': key, 'value': value, 'good': good_text(good), 'meaning': meaning,
                     'verdict': rubric.verdict(value, good)})
    return rows


GOOD_TEXT = {'high': 'higher', 'low': 'lower'}


def good_text(good):
    """How the checks table shows a key's good value: true, false, 0, higher, lower or nothing."""
    if isinstance(good, bool):
        return str(good).lower()
    if good == 0:
        return '0'
    return GOOD_TEXT.get(good, '')


def sections(run_dir, s, blind, with_prompt=True):
    """What a reviewer sees of one run, as (title, kind, body, options). kind tells the renderers how to show
    body: md, text and findings are strings, table is a list of rows, checks a list of dicts, tsv a string with
    options['columns'], diff a patch, logs a list of (name, text)."""
    tel = transcript.read(run_dir) or {}
    g = os.path.join(run_dir, 'grade')
    out = [('Prompt', 'md', read(os.path.join(run_dir, 'prompt.md')), {})] if with_prompt else []
    finals = tel.get('turn_finals') or []
    if len(finals) > 1:
        out += [(f'Agent final message, turn {i} of {len(finals)}', 'md', f or '(empty)', {})
                for i, f in enumerate(finals, 1)]
    else:
        out.append(('Agent final message', 'md', tel.get('final_message') or '(no transcript found)', {}))
    rows = [['gate ' + k, 'ok' if v else 'FAIL'] for k, v in s['gate'].items() if k != 'passed']
    rows += [['rules broken', ', '.join(s['rules_broken']) or 'none'],
             ['suggested failure tag', s['failure_tag_suggested'] or 'none']]
    rows += [[f'suggested {d}', '-' if v['auto'] is None else v['auto']] for d, v in s['dims'].items()]
    out.append(('Gates and suggestions', 'table', rows, {}))
    out.append(('Automatic checks (auto.json)', 'checks', auto_checks(run_dir, s), {}))
    t = s['telemetry']
    rows = [[k, v] for k, v in t.items() if not (blind and k in ('harness', 'model'))]
    if t.get('unbacked_claim'):
        rows.append(['WARNING', 'claims verification but ran no build or test command'])
    out.append(('Telemetry', 'table', rows, {}))
    cmds = tel.get('commands') or []
    out.append((f'Commands run ({len(cmds)}; build/test marked >)', 'text',
                '\n'.join(('> ' if transcript.VERIFY_CMD.search(c) else '  ') + c.replace('\n', ' ')[:200] for c in cmds),
                {}))
    # A task can add its own sections: review_sections() in its grade.py gives (title, kind, file in grade/[, options])
    hooks = taskset.task_module(s['task'], s.get('taskset'))
    for title, kind, name, *opts in (hooks.review_sections() if hasattr(hooks, 'review_sections') else []):
        out.append((title, kind, read(os.path.join(g, name)) or '(none)', opts[0] if opts else {}))
    # Most tasks record diff.patch. T2 has none: its changes are in several repos, one patch each.
    patches = ['diff.patch'] if os.path.exists(os.path.join(g, 'diff.patch')) else \
        sorted(os.path.basename(p) for p in glob.glob(os.path.join(g, '*.patch')))
    for name in patches:
        title = 'Diff' if name == 'diff.patch' else f'Diff: {name}'
        out.append((title, 'diff', read(os.path.join(g, name), 2_000_000), {}))
    if not patches:
        out.append(('Diff', 'text', '(no diff recorded)', {}))
    logs = sorted(glob.glob(os.path.join(g, '*.log')) + glob.glob(os.path.join(g, '*.txt')))
    out.append(('Grader logs', 'logs', [(os.path.basename(f), read(f, 100_000)) for f in logs], {}))
    return out


def as_text(kind, body):
    """A section body for the pager."""
    if kind == 'table':
        return '\n'.join(f'{k}: {v}' for k, v in body)
    if kind == 'checks':
        return '\n'.join(f"{r['key']}: {r['value']}" + (f"  (good: {r['good']})" if r['good'] else '')
                         + (f"  {r['meaning']}" if r['meaning'] else '') for r in body)
    if kind == 'logs':
        return '\n'.join(name for name, _ in body) + '\n(see grade/ in the run folder)'
    return body


def header_line(run_dir, s, blind):
    who = f"run {blind_id(run_dir)}" if blind else f"{s['harness']}/{s['model']}  ({os.path.basename(run_dir)})"
    return f"{s['task']}  |  {who}  |  wall {s['wall_min']} min"


def show_in_pager(run_dir, blind):
    s = scorer.score(run_dir)
    out = [f'\033[1m{header_line(run_dir, s, blind)}\033[0m', rubric_text()]
    out += [f'\n\033[1;36m==== {title} ====\033[0m\n{as_text(kind, body).rstrip()}\n'
            for title, kind, body, _ in sections(run_dir, s, blind)]
    subprocess.run(shlex.split(os.environ.get('PAGER', 'less -R')), input='\n'.join(out).encode())
    return s


def rubric_text():
    """The rubric as plain text, for the pager."""
    lines = [f'\nScores 1 to {rubric.SCALE}:']
    for d in rubric.DIMENSIONS:
        h = rubric.DIMENSION_HELP[d]
        lines.append(f"  {d}: {h['question']}")
        lines += [f'      {r}: {b}' for r, b in zip(rubric.BANDS, h['bands'])]
    return '\n'.join(lines)


def human_grades(run_dir, s):
    """The run's human.json, or a fresh template prefilled with the automatic suggestions."""
    path = os.path.join(run_dir, 'grade', 'human.json')
    if os.path.exists(path):
        h = json.load(open(path))
        if h.get('scale') == rubric.SCALE:
            return h
    auto = {d: v['auto'] for d, v in s['dims'].items()}
    auto['failure_tag'] = s['failure_tag_suggested']
    return scorer.human_template(auto)


def check_form(s, form):
    """Validates one run's posted grades. Returns an error message or None."""
    scores = form.get('scores') or {}
    for d in s['dims']:
        v = scores.get(d)
        if v is not None and (not isinstance(v, int) or not 1 <= v <= rubric.SCALE):
            return f'{d}: score must be 1 to {rubric.SCALE} or empty'
    if form.get('failure_tag') not in [None] + scorer.FAILURE_TAGS:
        return 'unknown failure tag'
    return None


KEEP_RANK = 'keep'


def save_grades(run_dir, form, rank=KEEP_RANK, rank_n=None):
    """Merges one run's checked form into human.json. rank: its place among rank_n runs, None to clear it,
    or KEEP_RANK (a run graded on its own)."""
    s = scorer.score(run_dir)
    h = human_grades(run_dir, s)
    scores = form.get('scores') or {}
    h['scale'] = rubric.SCALE
    h['scores'] = {d: scores.get(d) for d in s['dims']}
    h['failure_tag'] = form.get('failure_tag')
    h['no_harm'] = form.get('no_harm') is not False
    h['exclude'] = form.get('exclude') is True
    h['intervention'] = str(form.get('intervention') or '')
    h['notes'] = str(form.get('notes') or '')
    if rank != KEEP_RANK:
        h['rank'], h['rank_n'] = rank, rank_n
        h['ranked_at'] = datetime.datetime.now().isoformat(timespec='seconds')
    json.dump(h, open(os.path.join(run_dir, 'grade', 'human.json'), 'w'), indent=1)


def save_task(group, body):
    """Saves the posted grades of a task's runs (by blind id) and their ranking. Validates everything before
    writing anything. group: [(run_dir, score)]. Returns an error message or None."""
    by_id = {blind_id(r): (r, s) for r, s in group}
    forms = body.get('runs') or {}
    if set(forms) != set(by_id):
        return 'the page does not match the runs of this task any more; reload it'
    for rid, (r, s) in by_id.items():
        error = check_form(s, forms[rid])
        if error:
            return f'run {rid}: {error}'
    ranked = [rid for rid in body.get('ranking') or [] if not forms[rid].get('exclude')]
    kept = {rid for rid in by_id if not forms[rid].get('exclude')}
    if len(kept) > 1 and (len(ranked) != len(kept) or set(ranked) != kept):
        return 'the ranking must list every run that is not excluded, once'
    for rid, (r, s) in by_id.items():
        if len(by_id) == 1:
            save_grades(r, forms[rid])
        elif rid in kept and len(kept) > 1:
            save_grades(r, forms[rid], ranked.index(rid) + 1, len(kept))
        else:
            save_grades(r, forms[rid], None)  # excluded, or the only run left: no rank
    return None


def run_data(run_dir, s, blind, label):
    """One run for the page: its label, what it shows and its grade form."""
    h = human_grades(run_dir, s)
    gate_failed = [k for k, v in s['gate'].items() if k != 'passed' and not v]
    return {
        'id': blind_id(run_dir), 'label': label,
        'who': '' if blind else f"{s['harness']}/{s['model']}",
        'header': header_line(run_dir, s, blind), 'gate_failed': gate_failed,
        'sections': [{'title': t, 'kind': k, 'body': b, 'options': o}
                     for t, k, b, o in sections(run_dir, s, blind, with_prompt=False)],
        'form': {
            'dims': {d: {'auto': v['auto'], 'value': (h.get('scores') or {}).get(d)} for d, v in s['dims'].items()},
            'failure_tag': h.get('failure_tag'), 'failure_tag_suggested': s['failure_tag_suggested'],
            'no_harm': h.get('no_harm', True) is not False, 'exclude': h.get('exclude') is True,
            'intervention': h.get('intervention', ''), 'notes': h.get('notes', ''),
            'rank': h.get('rank') if h.get('scale') == rubric.SCALE else None,
        },
    }


def task_info(task, ts_path):
    """The Task tab: prompt, turns, reference solution and the rubric with the task's own notes."""
    tdir = taskset.task_dir(task, ts_path)
    turns = sorted(glob.glob(os.path.join(tdir, 'turns', '*.md')))
    ref = os.path.join(tdir, 'reference.md')
    return {
        'task': task,
        'prompt': read(os.path.join(tdir, 'prompt.md')),
        'turns': [{'name': os.path.basename(t), 'text': read(t)} for t in turns],
        'reference': read(ref) if os.path.exists(ref) else '',
        'scale': rubric.SCALE,
        'bands': rubric.BANDS,
        'dims': [{'name': d, **rubric.DIMENSION_HELP[d], 'note': taskset.rubric_notes(task, ts_path).get(d, '')}
                 for d in rubric.DIMENSIONS],
        'failure_tags': scorer.FAILURE_TAGS,
    }


def page_html(task, group, blind, order, save_url):
    """The review page for the given runs of one task. order: run_dir -> sort key (blind: random)."""
    group = sorted(group, key=lambda rs: order(rs[0]))
    letters = [chr(ord('A') + i) if i < 26 else f'R{i + 1}' for i in range(len(group))]
    data = {'info': task_info(task, group[0][1].get('taskset') if group else None),
            'runs': [run_data(r, s, blind, label) for (r, s), label in zip(group, letters)],
            'rank': len(group) > 1, 'blind': blind, 'save_url': save_url}
    data = json.dumps(data, default=str).replace('</', '<\\/')
    return open(os.path.join(BENCH, 'lib', 'review_page.html')).read().replace('/*DATA*/null', data)


def serve(first, blind):
    """Serves one task per page on 127.0.0.1 until Ctrl+C. first: ('task', name), ('run', run_dir) or None
    for the next pending task. Every URL carries a random token, so other pages in the browser cannot post
    grades. Runs are addressed by their blind id, which hides harness and model."""
    token = secrets.token_urlsafe(16)
    shuffle = {}  # blind: a fixed random position per run for this session

    def order(run_dir):
        if not blind:
            return os.path.basename(run_dir)
        return shuffle.setdefault(run_dir, random.random())

    def run_by_id(rid):
        return next((r for r in runs() if blind_id(r) == rid), None)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, body, ctype='text/html; charset=utf-8', location=None):
            data = body.encode()
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            if location:
                self.send_header('Location', location)
            self.end_headers()
            self.wfile.write(data)

        def route(self):
            parts = urllib.parse.urlparse(self.path).path.strip('/').split('/')
            if len(parts) < 2 or not secrets.compare_digest(parts[0], token):
                return None, None
            return parts[1], urllib.parse.unquote(parts[2]) if len(parts) > 2 else None

        def target(self, action, name):
            """(task, group) for a task or single run URL, or (None, None)."""
            if action == 'task' and name and os.path.isdir(os.path.join(RESULTS, name)):
                return name, task_runs(name)
            run_dir = run_by_id(name) if action == 'run' else None
            if run_dir:
                return scorer.score(run_dir)['task'], [(run_dir, scorer.score(run_dir))]
            return None, None

        def do_GET(self):
            action, name = self.route()
            if action == 'next':
                todo = pending_tasks()
                if not todo:
                    return self.reply(200, '<!doctype html><title>Done</title><p>Nothing pending. '
                                           'Stop the server with Ctrl+C.</p>')
                return self.reply(303, '', location=f'/{token}/task/{urllib.parse.quote(todo[0])}')
            task, group = self.target(action, name)
            if not group:
                return self.reply(404, 'not found', 'text/plain')
            self.reply(200, page_html(task, group, blind, order, f'/{token}/{action}/{urllib.parse.quote(name)}'))

        def do_POST(self):
            action, name = self.route()
            task, group = self.target(action, name)
            if not group or self.headers.get('Content-Type') != 'application/json':
                return self.reply(404, 'not found', 'text/plain')
            try:
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
            except ValueError:
                return self.reply(400, json.dumps({'error': 'not JSON'}), 'application/json')
            error = save_task(group, body)
            if error:
                return self.reply(400, json.dumps({'error': error}), 'application/json')
            for r, _ in group:
                s = scorer.score(r)
                print('saved', f"{s['task']} run {blind_id(r)}: total {s['total']}" if blind else scorer.summary_line(s), flush=True)
            left = len(pending_tasks())
            self.reply(200, json.dumps({'summary': f'{len(group)} runs saved; {left} tasks still pending',
                                        'next': f'/{token}/next'}), 'application/json')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    path = 'next'
    if first and first[0] == 'task':
        path = f'task/{urllib.parse.quote(first[1])}'
    elif first:
        path = f'run/{blind_id(first[1])}'
    url = f'http://127.0.0.1:{server.server_port}/{token}/{path}'
    print(f'Review at {url}\nCtrl+C to stop.', flush=True)
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()


def edit(run_dir, s, blind):
    path = os.path.join(run_dir, 'grade', 'human.json')
    # human_grades gives a fresh template for a missing file and for an old 0 to 3 one
    json.dump(human_grades(run_dir, s), open(path, 'w'), indent=1)
    editor = os.environ.get('EDITOR', 'vi')
    work = path
    if blind:
        work = os.path.join(tempfile.mkdtemp(), f'grade-{blind_id(run_dir)}.json')
        shutil.copy(path, work)
    while True:
        subprocess.run(shlex.split(editor) + [work])
        try:
            error = check_form(s, json.load(open(work)))
            if not error:
                break
            input(f'{error}. Enter to edit again. ')
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
    pager = '--pager' in args or not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))
    args = [a for a in args if a not in ('--blind', '--pager')]
    if args[0] == 'pending':
        for r in pending():
            print(os.path.relpath(r, RESULTS))
        return
    if args[0] == 'task':
        if len(args) < 2 or not os.path.isdir(os.path.join(RESULTS, args[1])):
            sys.exit(f'usage: review.py task <task>; tasks with runs are the folders in {RESULTS}')
        return serve(('task', args[1]), blind)
    run_dir = None
    if args[0] != 'next':
        run_dir = os.path.abspath(args[0])
    else:
        todo = pending()
        print(f'{len(todo)} runs pending in {len(pending_tasks())} tasks.')
        if not todo:
            return
        if pager:
            run_dir = random.choice(todo) if blind else todo[0]
    if not pager:
        return serve(('run', run_dir) if run_dir else None, blind)
    s = show_in_pager(run_dir, blind)
    edit(run_dir, s, blind)


if __name__ == '__main__':
    main(sys.argv[1:])
