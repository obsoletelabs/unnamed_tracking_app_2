"""Exercise change selection against real Git histories and safe tool arguments."""

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "ci_checks", Path(__file__).parents[1] / "ci_checks.py"
)
ci_checks = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci_checks)


class ChangedChecksTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.git("init", "-q")
        self.git("config", "user.name", "CI test")
        self.git("config", "user.email", "ci-test@example.invalid")
        self.write("src/frontend/src/Untouched.vue", "<template>Untouched</template>\n")
        self.write("src/backend/src/untouched.py", "value = 1\n")
        self.base = self.commit()

    def git(self, *arguments):
        return (
            subprocess.run(["git", *arguments], cwd=self.root, check=True, stdout=subprocess.PIPE)
            .stdout.decode()
            .strip()
        )

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "test: create test history")
        return self.git("rev-parse", "HEAD")

    def draft_event(self, head):
        return {"pull_request": {"draft": True, "base": {"sha": self.base}, "head": {"sha": head}}}

    def test_branch_push_only_checks_changed_frontend_files(self):
        name = "src/frontend/src/Changed file.vue"
        self.write(name, "<template>Changed</template>\n")
        head = self.commit()
        changes = ci_checks.changed_paths(
            {"ref": "refs/heads/feat/test", "before": self.base, "after": head}, root=self.root
        )
        self.assertEqual(changes, [name])
        self.assertEqual(ci_checks.quality_paths(changes, "frontend-lint", root=self.root), [name])
        self.assertEqual(ci_checks.quality_paths(changes, "backend-pylint", root=self.root), [])
        self.assertFalse(ci_checks.affected(changes, ci_checks.BACKEND))

    def test_ready_pr_checks_every_file_after_any_update(self):
        self.write("README.md", "Documentation\n")
        head = self.commit()
        event = self.draft_event(head)
        event["pull_request"]["draft"] = False
        event["action"] = "synchronize"
        changes = ci_checks.changed_paths(event, root=self.root)
        self.assertIn("src/backend/src/untouched.py", changes)
        self.assertIn("src/frontend/src/Untouched.vue", changes)

    def test_main_push_checks_every_file(self):
        event = {"ref": "refs/heads/main", "before": self.base, "after": self.base}
        self.assertEqual(len(ci_checks.changed_paths(event, root=self.root)), 2)

    def test_pr_diff_includes_earlier_commits(self):
        self.write("src/frontend/src/First.ts", "export const first = 1;\n")
        self.commit()
        self.write("src/frontend/src/Second.ts", "export const second = 2;\n")
        head = self.commit()
        changes = ci_checks.changed_paths(self.draft_event(head), root=self.root)
        self.assertEqual(changes, ["src/frontend/src/First.ts", "src/frontend/src/Second.ts"])

    def test_deleted_and_renamed_paths_do_not_become_tool_arguments(self):
        self.git("mv", "src/frontend/src/Untouched.vue", "src/frontend/src/Renamed.vue")
        (self.root / "src/backend/src/untouched.py").unlink()
        head = self.commit()
        changes = ci_checks.changed_paths(self.draft_event(head), root=self.root)
        self.assertEqual(
            ci_checks.quality_paths(changes, "frontend-lint", root=self.root),
            ["src/frontend/src/Renamed.vue"],
        )
        self.assertEqual(ci_checks.quality_paths(changes, "backend-lint", root=self.root), [])
        self.assertTrue(ci_checks.affected(changes, ci_checks.BACKEND))

    def test_rule_changes_select_the_whole_affected_app(self):
        name = "src/frontend/eslint.config.js"
        self.write(name, "export default [];\n")
        self.commit()
        self.assertEqual(
            ci_checks.quality_paths([name], "frontend-lint", root=self.root),
            [name, "src/frontend/src/Untouched.vue"],
        )
        self.assertEqual(ci_checks.quality_paths([name], "backend-pylint", root=self.root), [])

    def test_missing_diff_baseline_fails_instead_of_silently_skipping(self):
        with self.assertRaises(subprocess.CalledProcessError):
            ci_checks.changed_paths(self.draft_event("not-a-git-ref"), root=self.root)

    def test_new_branch_without_baseline_checks_all_files(self):
        changes = ci_checks.changed_paths(
            {"before": "0" * 40, "after": self.base, "ref": "refs/heads/new"}, root=self.root
        )
        self.assertEqual(len(changes), 2)

    def test_tool_receives_safe_separate_arguments(self):
        name = "src/frontend/src/$(echo bad); --fix.vue"
        with (
            patch.object(ci_checks, "quality_paths", return_value=[name]),
            patch.object(ci_checks.subprocess, "run") as run,
        ):
            run.return_value.returncode = 0
            self.assertEqual(ci_checks.run_check("frontend-lint", [name]), 0)
        command = run.call_args.args[0]
        self.assertEqual(command[-2:], ["--", "src/$(echo bad); --fix.vue"])
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_backend_file_size_excludes_migrations_and_tests(self):
        names = [
            "src/backend/src/app.py",
            "src/backend/tests/test_app.py",
            "src/backend/src/database/migrations/versions/a.py",
        ]
        for name in names:
            self.write(name, "value = 1\n")
        self.assertEqual(ci_checks.quality_paths(names, "backend-size", root=self.root), [names[0]])

    def test_formatting_fix_filters_unsupported_binary_files(self):
        names = ["src/frontend/src/example.ts", "src/frontend/public/image.png"]
        for name in names:
            self.write(name, "test\n")
        self.assertEqual(
            ci_checks.quality_paths(names, "frontend-format-fix", root=self.root), [names[0]]
        )


if __name__ == "__main__":
    unittest.main()
