#!/usr/bin/env python3
"""Prove the production gate with SFTP Restic, offline SQL restoration and ordinary APIs."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from check_deployment_backups import Drill
from deployment_backups.compose import Deployment, environment
from deployment_backups.process import BackupError


class UpgradeDrill(Drill):
    def __init__(self, directory, options):
        super().__init__(directory, options)
        self.test_image = "uta-migration-proof-" + uuid4().hex
        self.test_image_created = False

    def utility(self, deployment, image, module):
        """Only target the generated deployment, with exact child cleanup on failure."""
        app = deployment.instances["app"]
        env = environment(app.info)
        env["ENV_FILE"] = "/dev/null"
        # This harness proves the retained strict mode; the application default is automatic.
        env["MIGRATION_BACKUP_MODE"] = "required"
        network = next(iter(app.info["NetworkSettings"]["Networks"].values()))["NetworkID"]
        arguments = [
            "docker",
            "create",
            "--network",
            network,
            "--volumes-from",
            app.identifier,
            "--entrypoint",
            "python",
            "--workdir",
            "/app",
        ]
        for key in env:
            arguments.extend(["--env", key])
        arguments.extend([image, "-m", module])
        identity = (
            self.runner.run(arguments, "create-owned-upgrade-probe", environment=env)
            .decode()
            .strip()
        )

        if not re.fullmatch(r"[0-9a-f]{64}", identity):
            raise BackupError("invalid-owned-upgrade-probe")
        try:
            return self.runner.run(["docker", "start", "--attach", identity], "owned-upgrade-probe")
        finally:
            self.runner.run(
                ["docker", "rm", "--force", identity], "remove-owned-upgrade-probe", timeout=10
            )

    def target(self):
        head = (
            self.runner.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--entrypoint",
                    "python",
                    self.options.app_image,
                    "-c",
                    "from alembic.config import Config; from alembic.script import ScriptDirectory; print(ScriptDirectory.from_config(Config('alembic.ini')).get_current_head())",
                ],
                "inspect-test-image-history",
            )
            .decode()
            .strip()
        )
        revision = "probe_" + uuid4().hex[:12]
        # A disposable image-only successor exercises a genuinely pending migration
        # without changing the production migration graph or falsifying source history.
        (self.directory / "revision.py").write_text(
            "from alembic import op\nimport sqlalchemy as sa\n"
            + f"revision = {revision!r}\ndown_revision = {head!r}\nbranch_labels = None\ndepends_on = None\n"
            + "def upgrade():\n    op.create_table('migration_gate_acceptance', sa.Column('value', sa.Text(), nullable=False))\n    op.execute(\"INSERT INTO migration_gate_acceptance VALUES ('migrated-once')\")\n"
            + "def downgrade():\n    raise RuntimeError('No automatic downgrade in this acceptance fixture')\n"
        )
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9./:@_-]*", self.options.app_image):
            raise BackupError("invalid-acceptance-image-reference")
        base = self.runner.json(
            ["docker", "image", "inspect", self.options.app_image], "inspect-source-image"
        )[0]["Id"]
        (self.directory / "Dockerfile").write_text(
            f"FROM {self.options.app_image}\nCOPY revision.py /app/src/database/migrations/versions/upgrade_gate_probe.py\n"
        )
        self.runner.run(
            [
                "docker",
                "build",
                "--network",
                "none",
                "-t",
                self.test_image,
                "-f",
                str(self.directory / "Dockerfile"),
                str(self.directory),
            ],
            "build-owned-upgrade-image",
        )
        self.test_image_created = True
        if (
            self.runner.json(
                ["docker", "image", "inspect", self.options.app_image], "recheck-source-image"
            )[0]["Id"]
            != base
        ):
            raise BackupError("acceptance-source-image-changed")
        image = self.runner.json(
            ["docker", "image", "inspect", self.test_image], "inspect-target-image"
        )[0]["Id"]
        return head, revision, image

    def run(self):
        head, target, image = self.target()
        self.transport()
        project = self.configuration("upgrade-source")
        self.compose(project, "up", "-d")
        samples = self.seed(self.origin(project), project)
        self.verify_api(self.origin(project), samples)
        deployment = Deployment(self.runner, project, [self.directory / (project + ".json")])
        deployment.preflight()
        deployment.stop_writers()
        print("Two ordinary accounts and persistent files seeded; writers stopped", flush=True)
        try:
            self.utility(deployment, image, "src.database.migrate")
            raise AssertionError("Pending migration accepted without recovery")
        except BackupError as error:
            if error.step != "owned-upgrade-probe":
                raise
        if (
            deployment.sql(
                "SELECT to_regclass('migration_gate_acceptance') IS NULL", as_json=False
            ).strip()
            != "t"
        ):
            raise BackupError("unapproved-upgrade-changed-schema")
        if deployment.sql("SELECT json_agg(version_num) FROM alembic_version") != [head]:
            raise BackupError("unapproved-upgrade-changed-history")
        print("Missing recovery point halted the real target image before DDL", flush=True)
        report = self.cli(
            "prepare-upgrade",
            "--upgrade-image",
            image,
            "--project",
            project,
            "--compose-file",
            str(self.directory / (project + ".json")),
        )
        if (
            not report.get("upgrade_recovery_verified")
            or not report["backup_complete"]
            or report["writers_resumed"]
        ):
            raise BackupError("invalid-prepare-upgrade-result")
        if deployment.inspect("app").running or deployment.inspect("runtime").running:
            raise BackupError("prepare-upgrade-resumed-writers")
        print(
            "Full SFTP recovery point restored and SQL-restored; target-bound approval issued",
            flush=True,
        )
        self.utility(deployment, image, "src.database.migrate")
        if deployment.sql("SELECT json_agg(version_num) FROM alembic_version") != [target]:
            raise BackupError("approved-upgrade-did-not-reach-target")
        if (
            deployment.sql("SELECT count(*) FROM migration_gate_acceptance", as_json=False).strip()
            != "1"
        ):
            raise BackupError("pending-migration-did-not-run-exactly-once")
        # Remove the approval: an unchanged restart must not request another backup.
        self.runner.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--volumes-from",
                deployment.instances["app"].identifier,
                "--entrypoint",
                "python",
                image,
                "-m",
                "src.database.prepare_upgrade",
                "--invalidate",
            ],
            "invalidate-test-approval",
        )
        self.utility(deployment, image, "src.database.migrate")
        configuration = self.directory / (project + ".json")
        data = json.loads(configuration.read_text())
        data["services"]["app"]["image"] = image
        configuration.write_text(json.dumps(data))
        self.compose(project, "up", "-d", "app", "plugin-runtime")
        self.verify_api(self.origin(project), samples)
        print(
            json.dumps(
                {
                    "status": "success",
                    "transport": "sftp",
                    "ordinary_accounts": 2,
                    "missing_backup_halted_before_ddl": True,
                    "offline_postgresql_restore": True,
                    "writers_held_stopped": True,
                    "pending_migration_ran_once": True,
                    "unchanged_restart_without_backup": True,
                    "ordinary_api_and_file_checks": True,
                    "source_revision": head,
                    "target_revision": target,
                    "snapshot_id": report["snapshot_id"],
                    "drill_seconds": round(time.monotonic() - self.started_at, 2),
                }
            ),
            flush=True,
        )

    def cleanup(self):
        super().cleanup()
        if self.test_image_created:
            self.runner.run(["docker", "image", "rm", self.test_image], "remove-owned-test-image")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("app", "runtime", "sftp"):
        parser.add_argument("--" + name + "-image", required=True)
    options = parser.parse_args()
    os.umask(0o077)
    for key in list(os.environ):
        if key.startswith("RESTIC_"):
            del os.environ[key]
    with tempfile.TemporaryDirectory(prefix="uta-migration-drill-") as temporary:
        drill = UpgradeDrill(Path(temporary), options)
        try:
            drill.run()
        finally:
            drill.cleanup()


if __name__ == "__main__":
    main()
