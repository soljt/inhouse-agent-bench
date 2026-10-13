"""The task set a benchmark runs: where it lives, its taskset.toml, and each task's grade.py.

A task set is a folder with
  taskset.toml       image, workspace, deps folder, where the agent starts, reset settings (see below)
  tasks/<id>/        prompt.md, task.env (BASE_TAG=...), grade.py, and optionally hidden/, turns/, prepare.sh
  lib/               optional Python modules for its graders (on the path while grading and scoring); the one
                     named by module may define Grader (a checks.Grader subclass), RULE_BREAKS and BUILD_KEYS
plus whatever its hooks and image need. It is found through BENCH_TASKSET (environment, or the repo's
.env), and a run records which one it used in meta.txt.

taskset.toml keys:
  name, image (docker image), workspace (git repo with the base tags), deps (folder shared read-only, optional),
  agent_workdir (relative to the workspace), module (a module in lib/, optional), [reset] keep (untracked
  paths git clean keeps) and after (script in the task set, run in the sandbox after the reset with WS and
  DEPS set), [agent] run_dirs (container path = folder in the run folder, mounted into the agent container,
  e.g. where a task deploys to) and create (more folders to create in the run folder).
BENCH_WORKSPACE and BENCH_DEPS override workspace and deps.

A task's grade.py has grade(g) for the checks. It may also define how its checks map to the rubric
(see rubric.py): behaviour(auto), correctness(auto), reuse(auto), procedure(auto, score, broken), plus
review_sections(), REGRADE_FROM_DIFF, REGRADE_KEEP (keys a regrade takes over from the previous auto.json),
GRADER_FILES (files besides hidden/ the review page shows), TEST_LOGS
(key -> test log file) and BEHAVIOUR_TEST (the behaviour gate's test key), AUTO_KEYS (what its auto.json keys mean) and RUBRIC_NOTES
(task specific grading notes per dimension).

Usage: python3 bench/lib/taskset.py env    shell assignments for run.sh and load.sh
"""
import importlib
import os
import shlex
import sys
import tomllib

import checks
from checks import import_file

BENCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(BENCH)
_modules = {}


def _from_dotenv(key):
    """A value from the repo's .env, for tools started without it (review.py, report.py)."""
    path = os.path.join(REPO, '.env')
    if not os.path.exists(path):
        return None
    for line in open(path):
        k, sep, v = line.strip().partition('=')
        if sep and k.strip() == key:
            return v.strip().strip('"\'')
    return None


def root(path=None):
    """The task set folder: the given path, BENCH_TASKSET, or BENCH_TASKSET in .env."""
    path = path or os.environ.get('BENCH_TASKSET') or _from_dotenv('BENCH_TASKSET')
    if not path:
        sys.exit('No task set: set BENCH_TASKSET (in the environment or .env) to a task set folder.')
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(os.path.join(path, 'taskset.toml')):
        sys.exit(f'{path} is not a task set (no taskset.toml)')
    return path


def _optional_path(p):
    return os.path.abspath(os.path.expanduser(p)) if p else ''


def config(path=None):
    """taskset.toml with defaults filled in and paths expanded."""
    base = root(path)
    c = tomllib.load(open(os.path.join(base, 'taskset.toml'), 'rb'))
    reset = c.get('reset', {})
    return {
        'root': base,
        'name': c.get('name', os.path.basename(base)),
        'image': c['image'],
        'workspace': os.path.abspath(os.path.expanduser(os.environ.get('BENCH_WORKSPACE') or c['workspace'])),
        'deps': _optional_path(os.environ.get('BENCH_DEPS') or c.get('deps')),
        'agent_workdir': c.get('agent_workdir', '.'),
        'module': c.get('module'),
        'reset_keep': reset.get('keep', []),
        'reset_after': os.path.join(base, reset['after']) if reset.get('after') else None,
        'run_dirs': c.get('agent', {}).get('run_dirs', {}),
        'run_create': c.get('agent', {}).get('create', []),
    }


def tasks_dir(path=None):
    return os.path.join(root(path), 'tasks')


def task_dir(task, path=None):
    return os.path.join(tasks_dir(path), task)


def lib_dir(path=None):
    return os.path.join(root(path), 'lib')


def task_module(task, path=None):
    """The task's grade.py, imported once. The task set's lib/ goes on the path first."""
    key = (root(path), task)
    if key not in _modules:
        if lib_dir(path) not in sys.path:
            sys.path.insert(0, lib_dir(path))
        _modules[key] = import_file(os.path.join(task_dir(task, path), 'grade.py'))
    return _modules[key]


def module(path=None):
    """The task set's own module (taskset.toml: module), or None."""
    name = config(path)['module']
    if not name:
        return None
    if lib_dir(path) not in sys.path:
        sys.path.insert(0, lib_dir(path))
    return importlib.import_module(name)  # the same module the task's grade.py files import


def grader_class(path=None):
    """The task set's Grader, or the generic one."""
    return getattr(module(path), 'Grader', checks.Grader)


def rule_breaks(path=None):
    return getattr(module(path), 'RULE_BREAKS', None)


def build_keys(path=None):
    return getattr(module(path), 'BUILD_KEYS', None)


def auto_keys(task, path=None):
    """What the task's auto.json keys mean: rubric.AUTO_KEYS, then the task set module's AUTO_KEYS, then the
    task's (later ones win). key -> (good value, description)."""
    import rubric
    return {**rubric.AUTO_KEYS, **getattr(module(path), 'AUTO_KEYS', {}),
            **getattr(task_module(task, path), 'AUTO_KEYS', {})}


def rubric_notes(task, path=None):
    """Task specific grading notes per dimension from the task's RUBRIC_NOTES (dimension -> text), shown with
    the rubric on the review page."""
    return getattr(task_module(task, path), 'RUBRIC_NOTES', {})


def shell_env(path=None):
    """Shell assignments for the bash scripts."""
    c = config(path)
    values = {'TASKSET': c['root'], 'TASKSET_NAME': c['name'], 'TASKSET_IMAGE': c['image'],
              'TASKSET_WORKSPACE': c['workspace'], 'TASKSET_DEPS': c['deps'], 'TASKSET_AGENT_WORKDIR': c['agent_workdir']}
    lines = [f'{k}={shlex.quote(v)}' for k, v in values.items()]
    # bash arrays: container path=run folder, and folders to create
    lines.append('TASKSET_RUN_DIRS=(' + ' '.join(shlex.quote(f'{k}={v}') for k, v in c['run_dirs'].items()) + ')')
    lines.append('TASKSET_RUN_CREATE=(' + ' '.join(shlex.quote(d) for d in c['run_create']) + ')')
    return '\n'.join(lines)


if __name__ == '__main__':
    if sys.argv[1:] != ['env']:
        sys.exit(__doc__)
    print(shell_env())
