"""Where the tasks live, and each task's grade.py as a module.

A task's grade.py has grade(g) for the checks. It may also define how its checks map to the rubric
(see rubric.py): behaviour(auto), correctness(auto), reuse(auto), procedure(auto, score, broken).
"""
import os

from checks import import_file

BENCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_modules = {}


def tasks_dir():
    return os.path.join(BENCH, 'tasks')


def task_dir(task):
    return os.path.join(tasks_dir(), task)


def task_module(task):
    """The task's grade.py, imported once."""
    if task not in _modules:
        _modules[task] = import_file(os.path.join(task_dir(task), 'grade.py'))
    return _modules[task]
