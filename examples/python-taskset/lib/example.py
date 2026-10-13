"""Grading for the example task set: a Python package tested with pytest.

taskset.toml names this module, so the framework grades with this Grader and scores procedure and the build
gate with RULE_BREAKS and BUILD_KEYS (see bench/lib/rubric.py).
"""
from checks import Grader as BaseGrader

# Procedure: auto.json keys whose value means one of the repository's rules (AGENTS.md) was broken
RULE_BREAKS = {
    'diff_check_clean': lambda v: v is False,
    'agent_wrote_tests': lambda v: v is False,
}

# The build gate: the existing tests still pass
BUILD_KEYS = ['tests_ok']

# What this task set's checks put in auto.json, for the review page: key -> (good value, what it measures).
# See AUTO_KEYS in bench/lib/rubric.py for the generic keys and the good values.
AUTO_KEYS = {
    'tests_ok': (True, "The package's tests, the agent's own included, pass: the build gate"),
    'agent_wrote_tests': (True, 'The agent added or changed tests (AGENTS.md asks for a test with every change)'),
}


class Grader(BaseGrader):
    # Python comments, for count_added
    COMMENT_LINE = r'^\+\s*#'

    def pytest(self, key, tests):
        """Runs pytest from the workspace (so the package imports) on test files or folders. No cache folder:
        hidden tests live in the task set, which the sandbox mounts read-only."""
        self.command(key, ['python3', '-m', 'pytest', '-q', '-p', 'no:cacheprovider', *tests],
                     summary=r'^\d+ (?:passed|failed|errors?).*')
