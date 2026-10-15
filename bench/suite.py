#!/usr/bin/env python3
"""Run every task of the task set with each harness, unattended, one run after the other.

Usage: bench/suite.py [--dry-run] [--runs N] [--harness oc,cx] [--target local|openai|chatgpt] [--timeout-min 45] [task ...]

Runs bench/run.sh with BENCH_SCRIPTED=1 for each task (all of the task set's, or the ones named) and harness.
A task and harness that already have --runs graded runs for the same target, model, task set, BENCH_VARIANT
and BENCH_YOLO are skipped, so the suite can be stopped and started again and carries on where it left off.
Load test runs and runs marked exclude in human.json don't count as done.

  --dry-run      list what would run and what is already done, run nothing
  --runs N       graded runs wanted per task and harness (default 1). Breadth first: every task gets its
                 first run before any gets its second, so a suite stopped early still covers all tasks
  --timeout-min  a run over this is stopped, graded as it stands and marked timed_out (default 45)

Stop it after the current run with: touch ~/suite.stop (removed when the suite starts). Ctrl+C stops at once;
the unfinished run has no grade, so it runs again next time. The suite also stops when run.sh refuses to
start a run (no model endpoint, isolation or gitguard problem). Output goes to the terminal and is appended
to <results>/suite-<yymmdd>.log.
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import threading
import time

BENCH = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(BENCH)
sys.path.insert(0, BENCH)
sys.path.insert(0, os.path.join(BENCH, 'lib'))
import score as scorer  # noqa: E402
import taskset  # noqa: E402

RUN_SH = os.path.join(BENCH, 'run.sh')
RESULTS = os.environ.get('BENCH_RESULTS', os.path.join(BENCH, 'results'))
STOP_FILE = os.path.expanduser('~/suite.stop')
LOCAL_NAMES = ('local',)
OPENAI_NAMES = ('openai', 'dryrun')


def read_env_file(path):
    """KEY=VALUE pairs of an env file, quotes stripped; empty if it does not exist."""
    out = {}
    if os.path.exists(path):
        for line in open(path):
            k, sep, v = line.strip().partition('=')
            if sep and not k.startswith('#'):
                out[k.strip()] = v.strip().strip('"\'')
    return out


def model_for(target):
    """The model run.sh will record: the env file wins over the environment, as in scripts/env.sh."""
    env = {**os.environ, **read_env_file(os.environ.get('BENCH_ENV', os.path.join(REPO, '.env')))}
    if target == 'chatgpt':
        return env.get('CHATGPT_MODEL', '')
    if target in OPENAI_NAMES:
        return env.get('OPENAI_MODEL') or env.get('DRYRUN_MODEL', '')
    return env.get('LOCAL_MODEL') or env.get('LOCAL_MODEL', '')


def same_target(a, b):
    """True for the same target; a target and its old name (local, dryrun) count as one."""
    return a == b or (a in LOCAL_NAMES and b in LOCAL_NAMES) or (a in OPENAI_NAMES and b in OPENAI_NAMES)


def done_runs(task, harness, target, model, ts_root):
    """How many graded, not excluded, non load test runs of this configuration the task has."""
    variant = os.environ.get('BENCH_VARIANT', '')
    yolo = os.environ.get('BENCH_YOLO', '0') == '1'
    count = 0
    for meta_path in glob.glob(os.path.join(RESULTS, task, '*', 'meta.txt')):
        run = os.path.dirname(meta_path)
        m = scorer.meta(run)
        if (m.get('harness') != harness or not same_target(m.get('target', ''), target)
                or m.get('model') != model or m.get('variant', '') != variant
                or (m.get('yolo') == '1') != yolo or m.get('load_group')):
            continue
        if m.get('taskset') and os.path.abspath(m['taskset']) != ts_root:
            continue
        if not os.path.exists(os.path.join(run, 'grade', 'auto.json')):
            continue
        human = scorer.load_human(run) or {}
        if not human.get('exclude'):
            count += 1
    return count


def newest_run_dir(task, harness, target, model, since):
    """The run folder run.sh created for this run (newest matching one started after since), or None."""
    prefix = f'{harness}-{target}-{model.replace("/", "_")}-'
    dirs = [d for d in glob.glob(os.path.join(RESULTS, task, prefix + '*')) if os.path.getmtime(d) >= since - 5]
    return max(dirs, key=os.path.getmtime) if dirs else None


class Log:
    """Writes each line to the terminal and appends it to the suite log."""

    def __init__(self):
        os.makedirs(RESULTS, exist_ok=True)
        self.path = os.path.join(RESULTS, f'suite-{time.strftime("%y%m%d")}.log')
        self.file = open(self.path, 'a')

    def write(self, text):
        sys.stdout.write(text)
        sys.stdout.flush()
        self.file.write(text)
        self.file.flush()

    def say(self, text):
        self.write(f'suite {time.strftime("%H:%M:%S")}: {text}\n')


def forward(stream, log):
    """Copies run.sh's output to the log line by line until it closes."""
    for line in iter(stream.readline, ''):
        log.write(line)


def run_one(task, harness, target, model, timeout_min, log):
    """Runs one task with run.sh, stopping its container after timeout_min. Returns run.sh's exit code."""
    start = time.time()
    proc = subprocess.Popen([RUN_SH, harness, target, task], env={**os.environ, 'BENCH_SCRIPTED': '1'},
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors='replace')
    reader = threading.Thread(target=forward, args=(proc.stdout, log), daemon=True)
    reader.start()
    try:
        proc.wait(timeout=timeout_min * 60)
    except subprocess.TimeoutExpired:
        log.say(f'{task} {harness} over {timeout_min} min: stopping its container, grading it as it stands')
        # run.sh names the container bench-<task>-<its pid>
        subprocess.run(['docker', 'stop', '-t', '10', f'bench-{task}-{proc.pid}'], capture_output=True)
        run_dir = newest_run_dir(task, harness, target, model, start)
        if run_dir:
            with open(os.path.join(run_dir, 'meta.txt'), 'a') as f:
                f.write('timed_out=1\n')
        proc.wait()
    except KeyboardInterrupt:
        # Ctrl+C reached run.sh and its container too; let them exit before the suite does
        proc.wait(timeout=60)
        raise
    reader.join()
    log.say(f'{task} {harness} finished in {(time.time() - start) / 60:.0f} min (run.sh exit {proc.returncode})')
    return proc.returncode


def plan(tasks, harnesses, target, model, ts_root, runs):
    """The (task, harness) runs still to do, breadth first (all first runs, then all second runs, ...), and
    how many of the wanted runs are already done."""
    have = {(t, h): done_runs(t, h, target, model, ts_root) for t in tasks for h in harnesses}
    todo = [(t, h) for n in range(runs) for t in tasks for h in harnesses if have[(t, h)] <= n]
    done = sum(min(n, runs) for n in have.values())
    return todo, done


def main(argv):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('tasks', nargs='*')
    p.add_argument('--harness', default='oc,cx')
    p.add_argument('--target', default='local', choices=['local', 'openai', 'dryrun', 'chatgpt'])
    p.add_argument('--timeout-min', type=float, default=45)
    p.add_argument('--runs', type=int, default=1)
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args(argv)

    ts_root = os.path.abspath(taskset.root())
    all_tasks = sorted(os.listdir(taskset.tasks_dir()))
    unknown = [t for t in a.tasks if t not in all_tasks]
    if unknown:
        sys.exit(f'not in {ts_root}: {", ".join(unknown)}')
    tasks = a.tasks or all_tasks
    harnesses = a.harness.split(',')
    if a.target == 'chatgpt' and harnesses != ['cx']:
        sys.exit('target chatgpt runs Codex only: pass --harness cx')
    model = model_for(a.target)
    if not model:
        sys.exit(f'no model set for target {a.target} (LOCAL_MODEL/LOCAL_MODEL, OPENAI_MODEL or CHATGPT_MODEL in .env)')

    todo, done = plan(tasks, harnesses, a.target, model, ts_root, a.runs)
    variant = os.environ.get('BENCH_VARIANT')
    header = (f'{len(todo)} to run, {done} already done ({a.runs} per task and harness): {a.target} {model}{f" variant={variant}" if variant else ""},'
              f' task set {ts_root}')
    if a.dry_run:
        print(header)
        for task, harness in todo:
            print(f'  to run: {harness} {task}')
        return 0

    log = Log()
    if os.path.exists(STOP_FILE):
        os.remove(STOP_FILE)
    log.say(f'{header}; per-run timeout {a.timeout_min:g} min; log {log.path}')
    log.say(f'stop after the current run: touch {STOP_FILE}')
    try:
        for n, (task, harness) in enumerate(todo, 1):
            if os.path.exists(STOP_FILE):
                log.say(f'{STOP_FILE} found: stopping with {len(todo) - n + 1} left')
                return 0
            log.say(f'[{n}/{len(todo)}] {harness} {task}')
            code = run_one(task, harness, a.target, model, a.timeout_min, log)
            if code:
                log.say(f'run.sh failed (exit {code}): stopping the suite. Fix the cause above, then start it again.')
                return code
    except KeyboardInterrupt:
        log.say('interrupted: the unfinished run has no grade and runs again next time')
        return 130
    log.say('suite complete. Next: bench/review.py next, then bench/report.py')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
