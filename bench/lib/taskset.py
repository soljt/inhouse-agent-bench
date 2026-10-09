"""The task set a benchmark runs: where it lives, its taskset.toml, and each task's grade.py.

A task set is a folder with
  taskset.toml       image, workspace, deps folder, where the agent starts, reset settings (see below)
  tasks/<id>/        prompt.md, task.env (BASE_TAG=...), grade.py, and optionally hidden/, turns/, prepare.sh
  lib/               optional Python modules for its graders (on the path while grading and scoring)
plus whatever its hooks and image need. It is found through BENCH_TASKSET (environment, or the repo's
.env), and a run records which one it used in meta.txt.

taskset.toml keys:
  name, image (docker image), workspace (git repo with the base tags), deps (folder shared read-only),
  agent_workdir (relative to the workspace), [reset] keep (untracked paths git clean keeps) and
  after (script in the task set, run in the sandbox after the reset with WS and DEPS set).
BENCH_WORKSPACE and BENCH_DEPS override workspace and deps.

A task's grade.py has grade(g) for the checks. It may also define how its checks map to the rubric
(see rubric.py): behaviour(auto), correctness(auto), reuse(auto), procedure(auto, score, broken), plus
review_sections() and REGRADE_FROM_DIFF.

Usage: python3 bench/lib/taskset.py env    shell assignments for run.sh and load.sh
"""
import os
import shlex
import sys
import tomllib

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
        'deps': os.path.abspath(os.path.expanduser(os.environ.get('BENCH_DEPS') or c.get('deps', '~/llm-bench-deps'))),
        'agent_workdir': c.get('agent_workdir', '.'),
        'reset_keep': reset.get('keep', []),
        'reset_after': os.path.join(base, reset['after']) if reset.get('after') else None,
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


def shell_env(path=None):
    """Shell assignments for the bash scripts."""
    c = config(path)
    values = {'TASKSET': c['root'], 'TASKSET_NAME': c['name'], 'TASKSET_IMAGE': c['image'],
              'TASKSET_WORKSPACE': c['workspace'], 'TASKSET_DEPS': c['deps'], 'TASKSET_AGENT_WORKDIR': c['agent_workdir']}
    return '\n'.join(f'{k}={shlex.quote(v)}' for k, v in values.items())


if __name__ == '__main__':
    if sys.argv[1:] != ['env']:
        sys.exit(__doc__)
    print(shell_env())
