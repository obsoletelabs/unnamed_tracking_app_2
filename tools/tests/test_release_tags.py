"""Exercise which image tags each version tag publishes."""

import importlib.util
import sys
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "release_tags", Path(__file__).parents[1] / "release_tags.py"
)
release_tags = importlib.util.module_from_spec(SPEC)
sys.modules["release_tags"] = release_tags
SPEC.loader.exec_module(release_tags)

tags_for = release_tags.tags_for


class ReleaseTagTests(unittest.TestCase):
    def test_stable_release_moves_latest_unless_a_higher_one_exists(self):
        self.assertEqual(
            tags_for("v0.0.4", ["v0.0.3"]), ["v0.0.4", "0.0.4", "0.0", "latest"]
        )
        self.assertNotIn("latest", tags_for("v0.0.3", ["v0.0.4"]))

    def test_beta_gets_its_own_line_tag_and_the_next_line_gets_latest_beta(self):
        self.assertEqual(
            tags_for("v0.0.4-beta.2", ["v0.0.3", "v0.0.4-beta.1"]),
            ["v0.0.4-beta.2", "0.0.4-beta.2", "0.0.4-beta", "latest-beta"],
        )

    def test_two_lines_can_have_betas_but_only_the_next_line_owns_latest_beta(self):
        existing = ["v0.0.3", "v0.0.4-beta.1"]
        later = tags_for("v0.0.5-beta.1", existing)
        self.assertIn("0.0.5-beta", later)
        self.assertNotIn("latest-beta", later)
        self.assertIn("latest-beta", tags_for("v0.0.4-beta.2", existing + ["v0.0.5-beta.1"]))

    def test_latest_beta_passes_on_once_the_line_ships(self):
        existing = ["v0.0.4", "v0.0.4-beta.3", "v0.0.5-beta.1"]
        self.assertIn("latest-beta", tags_for("v0.0.5-beta.2", existing))

    def test_rebuilding_an_older_beta_moves_no_shared_tag(self):
        tags = tags_for("v0.0.4-beta.1", ["v0.0.4-beta.2"])
        self.assertEqual(tags, ["v0.0.4-beta.1", "0.0.4-beta.1"])

    def test_branch_previews_never_move_a_shared_tag(self):
        self.assertEqual(
            tags_for("v0.0.5-remote-launch.1", []),
            ["v0.0.5-remote-launch.1", "0.0.5-remote-launch.1"],
        )

    def test_non_version_tags_publish_nothing_special(self):
        self.assertEqual(tags_for("nightly", []), [])


if __name__ == "__main__":
    unittest.main()
