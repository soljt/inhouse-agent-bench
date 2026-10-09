#!/usr/bin/env python3
"""Walk through runs for human grading and record the grades in grade/human.json.

  bench/review.py next [--blind] [--pager]    review the runs that still need a human score, one after the other
  bench/review.py <run-dir> [--pager]         review this run
  bench/review.py pending                     list runs that still need a human score

Load test runs (bench/load.sh) and runs with "exclude": true in grade/human.json are never pending.

Shows the prompt, the agent's final message, the automatic checks and suggestions, telemetry, the commands it
ran, its diff, the grader logs and any sections the task adds (T12: its REVIEW.md and the findings). With a display
this runs a small server on 127.0.0.1 and opens the run in the browser, with the grade form at the bottom;
"Save and next" moves on to the next pending run. Ctrl+C stops it. --pager, or no display, shows the run in
$PAGER and opens the grade file in $EDITOR instead.
--blind hides harness and model and picks pending runs in random order.
"""
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
import taskset  # noqa: E402
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
    # A task can add its own sections: review_sections() in its grade.py gives (title, kind, file in grade/)
    hooks = taskset.task_module(s['task'], s.get('taskset'))
    for title, kind, name in (hooks.review_sections() if hasattr(hooks, 'review_sections') else []):
        out.append((title, kind, read(os.path.join(g, name)) or '(none)'))
    # Most tasks record diff.patch. T2 has none: its changes are in several repos, one patch each.
    patches = ['diff.patch'] if os.path.exists(os.path.join(g, 'diff.patch')) else \
        sorted(os.path.basename(p) for p in glob.glob(os.path.join(g, '*.patch')))
    for name in patches:
        title = 'Diff' if name == 'diff.patch' else f'Diff: {name}'
        out.append((title, 'diff', read(os.path.join(g, name), 2_000_000)))
    if not patches:
        out.append(('Diff', 'text', '(no diff recorded)'))
    logs = sorted(glob.glob(os.path.join(g, '*.log')) + glob.glob(os.path.join(g, '*.txt')))
    out.append(('Grader logs', 'logs', [(os.path.basename(f), read(f, 100_000)) for f in logs]))
    return out


def as_text(kind, body):
    if kind == 'table':
        return '\n'.join(f'{k}: {v}' for k, v in body)
    if kind == 'logs':
        return '\n'.join(name for name, _ in body) + '\n(see grade/ in the run folder)'
    return body


def header_line(run_dir, s, blind):
    who = f"run {blind_id(run_dir)}" if blind else f"{s['harness']}/{s['model']}  ({os.path.basename(run_dir)})"
    return f"{s['task']}  |  {who}  |  wall {s['wall_min']} min"


def show_in_pager(run_dir, blind):
    s = scorer.score(run_dir)
    out = [f'\033[1m{header_line(run_dir, s, blind)}\033[0m']
    out += [f'\n\033[1;36m==== {title} ====\033[0m\n{as_text(kind, body).rstrip()}\n'
            for title, kind, body in sections(run_dir, s, blind)]
    subprocess.run(shlex.split(os.environ.get('PAGER', 'less -R')), input='\n'.join(out).encode())
    return s


def human_grades(run_dir, s):
    """The run's human.json, or a fresh template prefilled with the automatic suggestions."""
    path = os.path.join(run_dir, 'grade', 'human.json')
    if os.path.exists(path):
        return json.load(open(path))
    auto = {d: v['auto'] for d, v in s['dims'].items()}
    auto['failure_tag'] = s['failure_tag_suggested']
    return scorer.human_template(auto)


def save_grades(run_dir, form):
    """Validates the posted form and merges it into human.json. Returns an error message or None."""
    s = scorer.score(run_dir)
    h = human_grades(run_dir, s)
    scores = form.get('scores') or {}
    for d in s['dims']:
        v = scores.get(d)
        if v not in (None, 0, 1, 2, 3):
            return f'{d}: score must be 0 to 3 or empty'
    if form.get('failure_tag') not in [None] + scorer.FAILURE_TAGS:
        return 'unknown failure tag'
    h['scores'] = {d: scores.get(d) for d in s['dims']}
    h['failure_tag'] = form.get('failure_tag')
    h['no_harm'] = form.get('no_harm') is not False
    h['exclude'] = form.get('exclude') is True
    h['intervention'] = str(form.get('intervention') or '')
    h['notes'] = str(form.get('notes') or '')
    json.dump(h, open(os.path.join(run_dir, 'grade', 'human.json'), 'w'), indent=1)
    return None


def page_html(run_dir, blind, save_url):
    s = scorer.score(run_dir)
    h = human_grades(run_dir, s)
    form = {
        'save_url': save_url,
        'dims': [{'name': d, 'auto': v['auto'], 'value': (h.get('scores') or {}).get(d)} for d, v in s['dims'].items()],
        'failure_tags': scorer.FAILURE_TAGS, 'failure_tag': h.get('failure_tag'),
        'failure_tag_suggested': s['failure_tag_suggested'],
        'no_harm': h.get('no_harm', True) is not False, 'exclude': h.get('exclude') is True,
        'intervention': h.get('intervention', ''), 'notes': h.get('notes', ''),
    }
    data = {'header': header_line(run_dir, s, blind), 'form': form,
            'sections': [{'title': t, 'kind': k, 'body': b} for t, k, b in sections(run_dir, s, blind)]}
    data = json.dumps(data, default=str).replace('</', '<\\/')
    return open(os.path.join(BENCH, 'lib', 'review_page.html')).read().replace('/*DATA*/null', data)


def serve(first_run, blind):
    """Serves one run per page on 127.0.0.1 until Ctrl+C. Every URL carries a random token, so other pages in
    the browser cannot post grades. A run is addressed by its blind id, which hides harness and model."""
    token = secrets.token_urlsafe(16)
    order = {}  # blind: a fixed random position per run, so "next" does not repeat itself

    def run_by_id(rid):
        return next((r for r in runs() if blind_id(r) == rid), None)

    def next_pending():
        todo = pending()
        if not todo:
            return None
        if not blind:
            return todo[0]
        for r in todo:
            order.setdefault(r, random.random())
        return min(todo, key=order.get)

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
            return parts[1], parts[2] if len(parts) > 2 else None

        def do_GET(self):
            action, rid = self.route()
            if action == 'next':
                r = next_pending()
                if r is None:
                    return self.reply(200, '<!doctype html><title>Done</title><p>Nothing pending. '
                                           'Stop the server with Ctrl+C.</p>')
                return self.reply(303, '', location=f'/{token}/run/{blind_id(r)}')
            run_dir = run_by_id(rid) if action == 'run' else None
            if run_dir is None:
                return self.reply(404, 'not found', 'text/plain')
            self.reply(200, page_html(run_dir, blind, f'/{token}/run/{rid}'))

        def do_POST(self):
            action, rid = self.route()
            run_dir = run_by_id(rid) if action == 'run' else None
            if run_dir is None or self.headers.get('Content-Type') != 'application/json':
                return self.reply(404, 'not found', 'text/plain')
            try:
                form = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
            except ValueError:
                return self.reply(400, json.dumps({'error': 'not JSON'}), 'application/json')
            error = save_grades(run_dir, form)
            if error:
                return self.reply(400, json.dumps({'error': error}), 'application/json')
            r = scorer.score(run_dir)
            summary = f"run {rid}: total {r['total']}" if blind else scorer.summary_line(r)
            print('saved', summary)
            self.reply(200, json.dumps({'summary': summary, 'next': f'/{token}/next'}), 'application/json')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    url = f'http://127.0.0.1:{server.server_port}/{token}/' + (f'run/{blind_id(first_run)}' if first_run else 'next')
    print(f'Review at {url}\nCtrl+C to stop.')
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()


def edit(run_dir, s, blind):
    path = os.path.join(run_dir, 'grade', 'human.json')
    if not os.path.exists(path):
        json.dump(human_grades(run_dir, s), open(path, 'w'), indent=1)
    editor = os.environ.get('EDITOR', 'vi')
    work = path
    if blind:
        work = os.path.join(tempfile.mkdtemp(), f'grade-{blind_id(run_dir)}.json')
        shutil.copy(path, work)
    while True:
        subprocess.run(shlex.split(editor) + [work])
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
    pager = '--pager' in args or not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))
    args = [a for a in args if a not in ('--blind', '--pager')]
    if args[0] == 'pending':
        for r in pending():
            print(os.path.relpath(r, RESULTS))
        return
    run_dir = None
    if args[0] != 'next':
        run_dir = os.path.abspath(args[0])
    else:
        todo = pending()
        print(f'{len(todo)} runs pending.')
        if not todo:
            return
        if pager:
            run_dir = random.choice(todo) if blind else todo[0]
    if not pager:
        return serve(run_dir, blind)
    s = show_in_pager(run_dir, blind)
    edit(run_dir, s, blind)


if __name__ == '__main__':
    main(sys.argv[1:])
