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


class PluginIntegrationScopeTests(unittest.TestCase):
    def scope(self, files, event_name="pull_request", event=None):
        return ci_gate.plugin_integration_scope(event_name, event or pull_request(), files)

    def test_unrelated_pull_request_skips_everything(self):
        scope = self.scope(["src/frontend/src/views/Home.vue", "README.md", "src/backend/src/x.py"])
        self.assertEqual(scope, {"run": False, "heavy": False})

    def test_plugin_runtime_and_api_changes_run_the_heavy_acceptance(self):
        for path in (
            "src/plugin-runtime/runtime.py",
            "src/backend/src/plugin_api/base_contracts.py",
            "src/backend/src/api/routes/plugin_manager/lifecycle.py",
            "src/frontend/src/components/plugins/PluginCard.vue",
            "src/frontend/src/components/MediaWatchLinks.vue",
            "src/frontend/src/services/mediaWatchLinks.ts",
            ".github/workflows/plugin-integration.yml",
        ):
            with self.subTest(path):
                self.assertEqual(self.scope([path]), {"run": True, "heavy": True})

    def test_frontend_unit_tests_do_not_trigger_the_browser_acceptance(self):
        self.assertEqual(
            self.scope(["src/frontend/src/tests/mediaWatchLinks.test.ts"]),
            {"run": False, "heavy": False},
        )

    def test_schemas_and_plugin_docs_run_only_the_contract_comparison(self):
        for path in (
            "wiki/docs/assets/plugin-metadata/metadata-registration-v1.schema.json",
            "wiki/docs/development/plugin-capabilities.md",
            "tools/export_metadata_contract.py",
        ):
            with self.subTest(path):
                self.assertEqual(self.scope([path]), {"run": True, "heavy": False})

    def test_main_tags_manual_runs_and_unknown_changes_run_everything(self):
        full = {"run": True, "heavy": True}
        self.assertEqual(self.scope(["README.md"], "push", push("refs/heads/main")), full)
        self.assertEqual(self.scope(["README.md"], "push", push("refs/tags/v0.0.5")), full)
        self.assertEqual(self.scope(["README.md"], "workflow_dispatch", {}), full)
        self.assertEqual(self.scope(None), full)
        self.assertEqual(self.scope([]), full)


class PluginIntegrationMainTests(unittest.TestCase):
    def test_draft_pull_request_and_covered_push_do_nothing(self):
        original = ci_gate.open_pull_request
        ci_gate.open_pull_request = lambda *_: True
        try:
            draft = ci_gate.plugin_integration_main("pull_request", "o/r", pull_request(True))
            covered = ci_gate.plugin_integration_main("push", "o/r", push("refs/heads/fix/plugin-x"))
        finally:
            ci_gate.open_pull_request = original
        self.assertEqual((draft["run"], draft["heavy"]), ("false", "false"))
        self.assertEqual((covered["run"], covered["heavy"]), ("false", "false"))


if __name__ == "__main__":
    unittest.main()
