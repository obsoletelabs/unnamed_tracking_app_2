"""Exercise the CI gate's run/skip decision and area selection."""

import importlib.util
import sys
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("ci_gate", Path(__file__).parents[1] / "ci_gate.py")
ci_gate = importlib.util.module_from_spec(SPEC)
sys.modules["ci_gate"] = ci_gate
SPEC.loader.exec_module(ci_gate)


def push(ref):
    return {"ref": ref}


def pull_request(draft=False):
    return {"pull_request": {"draft": draft, "number": 7}}


class DecisionTests(unittest.TestCase):
    def test_ready_pull_request_runs_and_draft_does_not(self):
        never = lambda _: self.fail("a pull request must not look up pull requests")  # noqa: E731
        self.assertTrue(ci_gate.decide("pull_request", pull_request(), has_open_pull_request=never).run)
        self.assertFalse(
            ci_gate.decide("pull_request", pull_request(True), has_open_pull_request=never).run
        )

    def test_main_and_tags_always_run(self):
        never = lambda _: self.fail("main and tags need no lookup")  # noqa: E731
        for ref in ("refs/heads/main", "refs/tags/v0.0.4"):
            self.assertTrue(ci_gate.decide("push", push(ref), has_open_pull_request=never).run)

    def test_branch_with_open_pull_request_is_left_to_that_run(self):
        decision = ci_gate.decide(
            "push", push("refs/heads/feat/x"), has_open_pull_request=lambda _: True
        )
        self.assertFalse(decision.run)

    def test_branch_without_pull_request_still_gets_ci(self):
        decision = ci_gate.decide(
            "push", push("refs/heads/feat/x"), has_open_pull_request=lambda _: False
        )
        self.assertTrue(decision.run)

    def test_unknown_pull_request_state_runs_ci(self):
        decision = ci_gate.decide(
            "push", push("refs/heads/feat/x"), has_open_pull_request=lambda _: None
        )
        self.assertTrue(decision.run)


class AreaTests(unittest.TestCase):
    def select(self, files, event="pull_request"):
        return ci_gate.areas(files, event_name=event, branch_push=event != "pull_request")

    def test_unknown_changes_run_everything(self):
        self.assertTrue(all(self.select(None).values()))
        self.assertTrue(all(self.select([]).values()))

    def test_frontend_only_change_skips_backend(self):
        selected = self.select(["src/frontend/src/App.vue"])
        self.assertFalse(selected["backend"])
        self.assertTrue(selected["frontend"])

    def test_backend_only_change_skips_frontend(self):
        selected = self.select(["src/backend/src/main.py"])
        self.assertTrue(selected["backend"])
        self.assertFalse(selected["frontend"])

    def test_development_docs_still_run_backend_tests(self):
        # Backend tests read wiki/docs/development/api-access-matrix.md.
        selected = self.select(["wiki/docs/development/api-access-matrix.md"])
        self.assertTrue(selected["backend"])
        self.assertFalse(selected["frontend"])
        self.assertTrue(selected["docs"])

    def test_workflow_change_runs_gated_pipelines(self):
        selected = self.select([".github/workflows/ci.yml"])
        self.assertTrue(selected["smoke"] and selected["recovery"] and selected["docs"])

    def test_branch_pushes_skip_pull_request_only_areas(self):
        selected = self.select(["src/backend/src/main.py"], event="push")
        self.assertFalse(selected["smoke"] or selected["recovery"] or selected["restore_drill"])


if __name__ == "__main__":
    unittest.main()
