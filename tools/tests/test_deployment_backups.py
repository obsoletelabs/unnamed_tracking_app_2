"""Recovery behavior and failure boundaries; optional tests use a real encrypted Restic repo."""

import io
import json
import os
import re
import sys
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).parents[1]))

from backup_deployment import backup, check_paths, main  # noqa: E402
from deployment_backups import bundle  # noqa: E402
from deployment_backups.compose import Deployment, Instance, environment  # noqa: E402
from deployment_backups.process import BackupError, Runner, deployment_lock, private_directory  # noqa: E402
from deployment_backups.repository import COMPLETE, PENDING, Repository  # noqa: E402


def archive(path, entries=None):
    with tarfile.open(path, "w") as target:
        root = tarfile.TarInfo("data")
        root.type = tarfile.DIRTYPE
        root.mode = 0o700
        target.addfile(root)
        for name, content, kind in entries or [("data/key", b"private", tarfile.REGTYPE)]:
            item = tarfile.TarInfo(name)
            item.type = kind
            if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                item.linkname = content.decode()
                target.addfile(item)
            else:
                item.size = len(content)
                target.addfile(item, io.BytesIO(content))
    path.chmod(0o600)


def make_bundle(root):
    directory = private_directory(root / "bundle")
    (directory / "database.dump").write_bytes(b"PGDMPsynthetic-unit-dump")
    (directory / "compose-0000.yaml").write_text("services: {}\n")
    metadata = {"services": {role: {} for role in ("app", "runtime", "database")}, "mounts": []}
    for index, role in enumerate(("app", "runtime")):
        name = f"mount-{index:04}.tar"
        archive(directory / name)
        metadata["mounts"].append({"role": role, "archive": name, "destination": "/data"})
    (directory / "deployment.json").write_text(json.dumps(metadata))
    for path in directory.iterdir():
        path.chmod(0o600)
    bundle.seal(directory, "drill", uuid4().hex, 1024**2)
    return directory


class BackupBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        prior_umask = os.umask(0o077)
        os.umask(prior_umask)
        self.addCleanup(os.umask, prior_umask)

    def test_command_failure_does_not_disclose_output_or_arguments(self):
        with self.assertRaises(BackupError) as raised:
            Runner().run(
                [sys.executable, "-c", "import sys; print('SECRET'); sys.exit(3)"], "upload"
            )
        self.assertEqual(str(raised.exception), "upload (exit 3)")

    def test_timeout_and_size_limits(self):
        for code, timeout, limit, message in (
            ("import time; time.sleep(10)", 1, 100, "timeout"),
            ("print('x'*1000)", 10, 100, "size-limit"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(BackupError, message):
                Runner(timeout).run([sys.executable, "-c", code], "producer", max_bytes=limit)

    def test_file_producer_failure_leaves_no_false_success(self):
        target = self.root / "dump"
        with self.assertRaises(BackupError):
            Runner().run(
                [sys.executable, "-c", "print('partial'); exit(1)"], "dump", output_file=target
            )
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_private_state_refuses_existing_public_directory(self):
        path = self.root / "public"
        path.mkdir(mode=0o755)
        path.chmod(0o755)
        with self.assertRaises(BackupError):
            private_directory(path)
        self.assertEqual(path.stat().st_mode & 0o777, 0o755)

    def test_clean_environment_does_not_inherit_commands_proxies_or_app_secrets(self):
        inherited = {
            "RESTIC_PASSWORD_COMMAND": "must-not-run",
            "HTTP_PROXY": "http://proxy.example.test",
            "SECRET_KEY": "synthetic-key-must-not-reach-child",
        }
        with patch.dict(os.environ, inherited):
            for environment_values in (None, {"UTA_PROBE": "allowed"}):
                with self.subTest(environment=environment_values):
                    output = Runner().json(
                        [
                            sys.executable,
                            "-c",
                            "import json, os; print(json.dumps(dict(os.environ)))",
                        ],
                        "clean-environment",
                        environment=environment_values,
                        inherit_environment=False,
                    )
                    for name in inherited:
                        self.assertNotIn(name, output)
                    self.assertEqual(
                        output.get("UTA_PROBE"),
                        "allowed" if environment_values else None,
                    )

    def test_concurrent_operator_is_refused(self):
        with (
            deployment_lock(self.root, "drill"),
            self.assertRaisesRegex(BackupError, "another-backup"),
        ):
            with deployment_lock(self.root, "drill"):
                self.fail("Second backup acquired the lock")

    def test_bundle_roundtrip_and_file_corruption(self):
        directory = make_bundle(self.root)
        bundle.verify(directory, "drill", 1024**2)
        (directory / "database.dump").write_bytes(b"PGDMPchanged")
        with self.assertRaisesRegex(BackupError, "integrity"):
            bundle.verify(directory, "drill", 1024**2)

    def test_missing_component_wrong_deployment_and_unknown_version(self):
        directory = make_bundle(self.root)
        with self.assertRaisesRegex(BackupError, "deployment-mismatch"):
            bundle.verify(directory, "other", 1024**2)
        manifest = bundle.load_json(directory / "manifest.json")
        manifest["version"] = 999
        (directory / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(BackupError, "version"):
            bundle.verify(directory, "drill", 1024**2)
        (directory / "mount-0000.tar").unlink()
        with self.assertRaisesRegex(BackupError, "incomplete"):
            bundle.validate_contents(directory, 1024**2)

    def test_unsafe_archive_members_and_truncation(self):
        path = self.root / "mount.tar"
        for name, content, kind in (
            ("../escape", b"x", tarfile.REGTYPE),
            ("/data/absolute", b"x", tarfile.REGTYPE),
            ("data/device", b"", tarfile.CHRTYPE),
            ("data/link", b"../../etc/shadow", tarfile.SYMTYPE),
            ("data/link", b"/etc/shadow", tarfile.LNKTYPE),
        ):
            with self.subTest(name=name, kind=kind), self.assertRaises(BackupError):
                archive(path, [(name, content, kind)])
                bundle.inspect_archive(path, "data")
        archive(path)
        path.write_bytes(path.read_bytes()[:550])
        with self.assertRaises(BackupError):
            bundle.inspect_archive(path, "data")

    def test_archive_links_are_retained_without_following(self):
        path = self.root / "mount.tar"
        archive(
            path,
            [
                ("data/key", b"private", tarfile.REGTYPE),
                ("data/link", b"key", tarfile.SYMTYPE),
                ("data/hard", b"data/key", tarfile.LNKTYPE),
            ],
        )
        bundle.inspect_archive(path, "data")
        with tarfile.open(path) as source:
            self.assertTrue(source.getmember("data/link").issym())
            self.assertTrue(source.getmember("data/hard").islnk())

    def test_no_capture_of_backup_state_inside_data_mount(self):
        metadata = {"mounts": [{"type": "bind", "source": str(self.root)}]}
        with self.assertRaisesRegex(BackupError, "inside-application-mount"):
            check_paths(SimpleNamespace(files=[]), metadata, [self.root / "credentials"])

    def test_capture_failure_resumes_writers_and_never_uploads(self):
        deployment = Mock()
        deployment.preflight.return_value = {
            "database": {"database_bytes": 1},
            "mounts": [],
            "writers": {"app": True, "plugin-runtime": False},
        }
        deployment.files = []
        deployment.capture.side_effect = BackupError("dump-failed")
        repository = Mock()
        options = SimpleNamespace(state_dir=self.root, max_bytes=1024**2, deployment_id="drill")
        report = {"run_id": uuid4().hex, "backup_complete": False}
        with (
            patch.dict(os.environ, {"RESTIC_PASSWORD_FILE": str(self.root / "key")}),
            self.assertRaisesRegex(BackupError, "dump-failed"),
        ):
            backup(deployment, repository, self.root, options, report)
        deployment.resume_writers.assert_called_once()
        repository.store.assert_not_called()
        self.assertTrue(report["writers_resumed"])
        self.assertFalse(report["backup_complete"])
        maintenance = bundle.load_json(self.root / "drill-maintenance.json")
        self.assertEqual(maintenance["previous_writers"], {"app": True, "plugin-runtime": False})
        self.assertEqual(maintenance["phase"], "resumed")

    def test_stop_failure_still_attempts_to_resume(self):
        deployment = Mock()
        deployment.preflight.return_value = {"database": {"database_bytes": 1}, "mounts": []}
        deployment.files = []
        deployment.stop_writers.side_effect = BackupError("partial-stop")
        options = SimpleNamespace(state_dir=self.root, max_bytes=1024**2, deployment_id="drill")
        with (
            patch.dict(os.environ, {"RESTIC_PASSWORD_FILE": str(self.root / "key")}),
            self.assertRaises(BackupError),
        ):
            backup(deployment, Mock(), self.root, options, {})
        deployment.resume_writers.assert_called_once()

    def test_partial_restic_exit_never_promotes(self):
        runner = Mock()
        runner.run.side_effect = BackupError("upload-pending-snapshot", 3)
        repository = Repository(runner, "drill")
        with self.assertRaises(BackupError):
            repository.store(self.root / "bundle", "run", self.root / "restore", 1024**2)
        runner.run.assert_called_once()
        self.assertEqual(runner.run.call_args.args[1], "upload-pending-snapshot")

    def test_failed_writer_restart_is_not_reported_as_success_even_if_snapshot_is_safe(self):
        deployment = Mock()
        deployment.preflight.return_value = {"database": {"database_bytes": 1}, "mounts": []}
        deployment.files = []

        def capture(directory, _metadata, _maximum):
            make_bundle(directory.parent)
            (directory / "manifest.json").unlink()

        deployment.capture.side_effect = capture
        deployment.resume_writers.side_effect = BackupError("unhealthy-restart")
        repository = Mock()
        repository.store.return_value = "a" * 64
        options = SimpleNamespace(state_dir=self.root, max_bytes=1024**2, deployment_id="drill")
        report = {"run_id": uuid4().hex, "backup_complete": False}
        with (
            patch.dict(os.environ, {"RESTIC_PASSWORD_FILE": str(self.root / "key")}),
            self.assertRaisesRegex(BackupError, "unhealthy-restart"),
        ):
            backup(deployment, repository, self.root, options, report)
        self.assertTrue(report["backup_complete"])
        self.assertFalse(report["writers_resumed"])
        self.assertEqual(report["resume_error"], "unhealthy-restart")

    def test_application_cannot_receive_repository_credentials(self):
        with self.assertRaisesRegex(BackupError, "credentials"):
            environment({"Config": {"Env": ["RESTIC_PASSWORD=do-not-log-this"]}})

    def test_failed_cli_run_preserves_last_success_and_other_commands_have_separate_status(self):
        arguments = ["backup", "--deployment-id", "drill", "--state-dir", str(self.root)]
        with (
            patch("backup_deployment.signal.signal"),
            patch("backup_deployment.execute") as execute,
            redirect_stdout(io.StringIO()),
        ):
            execute.side_effect = lambda _options, report: report.update(
                {"backup_complete": True, "writers_resumed": True}
            )
            self.assertEqual(main(arguments), 0)
            previous = bundle.load_json(self.root / "drill-backup.json")
            execute.side_effect = BackupError("upload-failed", 3)
            self.assertEqual(main(arguments), 1)
            current = bundle.load_json(self.root / "drill-backup.json")
            self.assertEqual(current["last_success_at"], previous["last_success_at"])
            self.assertFalse(current["backup_complete"])
            self.assertEqual(current["status"], "failed")
            execute.side_effect = None
            self.assertEqual(main(["plan", *arguments[1:]]), 0)
            self.assertEqual(bundle.load_json(self.root / "drill-backup.json"), current)

    def test_restore_refuses_existing_target_and_unsafe_snapshot_path(self):
        repository = Repository(Mock(), "drill")
        for path, target in (("/old/bundle", self.root), ("/../bundle", self.root / "new")):
            with self.assertRaises(BackupError):
                repository.restore({"id": "a" * 64, "paths": [path]}, target, 1024**2)
        repository.runner.run.assert_not_called()


class ComposeIdentityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        compose = self.root / "compose.yaml"
        compose.write_text("services: {}\n")
        self.runner = Mock()
        self.runner.timeout = 60
        self.deployment = Deployment(self.runner, "drill", [compose])
        for role, service, env, mounts in (
            (
                "app",
                "app",
                [
                    "POSTGRES_USER=backup",
                    "POSTGRES_PASSWORD=private-test-only",
                    "POSTGRES_DB=backup",
                    "POSTGRES_HOST=db",
                ],
                [{"Type": "volume", "Destination": "/data", "Source": "/volumes/app", "RW": True}],
            ),
            (
                "runtime",
                "plugin-runtime",
                [],
                [
                    {
                        "Type": "volume",
                        "Destination": "/var/lib/unnamed-tracking/plugins",
                        "Source": "/volumes/plugins",
                        "RW": True,
                    }
                ],
            ),
            (
                "database",
                "db",
                [
                    "POSTGRES_USER=backup",
                    "POSTGRES_PASSWORD=private-test-only",
                    "POSTGRES_DB=backup",
                    "PGDATA=/var/lib/postgresql/18/docker",
                ],
                [
                    {
                        "Type": "volume",
                        "Destination": "/var/lib/postgresql",
                        "Source": "/volumes/live-db",
                        "RW": True,
                    }
                ],
            ),
        ):
            info = {
                "Config": {"Env": env},
                "Mounts": mounts,
                "State": {"Running": True},
                "NetworkSettings": {
                    "Networks": {
                        "drill_default": {
                            "NetworkID": "network",
                            "Aliases": [service],
                            "IPAddress": "172.20.0.2",
                        }
                    }
                },
            }
            self.deployment.instances[role] = Instance(role, service, role, info)

    def test_live_pgdata_is_excluded_and_plugin_mount_is_retained(self):
        mounts = self.deployment.mounts()
        self.assertEqual({item["role"] for item in mounts}, {"app", "runtime"})
        self.assertEqual({item["source"] for item in mounts}, {"/volumes/app", "/volumes/plugins"})

    def test_shared_live_database_mount_is_refused(self):
        self.deployment.instances["app"].info["Mounts"][0]["Source"] = "/volumes/live-db"
        with self.assertRaisesRegex(BackupError, "live-database-volume"):
            self.deployment.mounts()

    def test_nested_live_database_mount_is_refused(self):
        self.deployment.instances["app"].info["Mounts"][0]["Source"] = "/volumes/live-db/18"
        with self.assertRaisesRegex(BackupError, "live-database-volume"):
            self.deployment.mounts()

    def test_external_or_mismatched_database_is_refused(self):
        self.deployment._validate_database()
        for value in (
            "POSTGRES_HOST=external-db",
            "POSTGRES_PASSWORD=another-secret",
            "POSTGRES_DB=other-db",
        ):
            original = self.deployment.instances["app"].info["Config"]["Env"]
            name = value.split("=", 1)[0]
            self.deployment.instances["app"].info["Config"]["Env"] = [
                item for item in original if not item.startswith(name + "=")
            ] + [value]
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(BackupError, "configuration-mismatch"),
            ):
                self.deployment._validate_database()
            self.deployment.instances["app"].info["Config"]["Env"] = original

    def test_stopped_runtime_is_not_started(self):
        self.deployment.instances["runtime"].info["State"]["Running"] = False
        self.deployment.inspect = Mock(return_value=self.deployment.instances["app"])
        self.deployment.resume_writers()
        self.runner.run.assert_called_once()
        self.assertEqual(self.runner.run.call_args.args[0][-2:], ["start", "app"])

    def test_writers_must_really_be_stopped(self):
        self.deployment.inspect = Mock(return_value=self.deployment.instances["app"])
        with self.assertRaisesRegex(BackupError, "did-not-stop"):
            self.deployment.stop_writers()

    def test_missing_data_mount_is_refused_before_maintenance(self):
        instances = self.deployment.instances.copy()
        instances["app"].info["Mounts"] = []
        self.deployment.inspect = Mock(side_effect=instances.__getitem__)
        self.deployment.sql = Mock(side_effect=[{"database_bytes": 1}, "f"])
        with self.assertRaisesRegex(BackupError, "persistent-roots-must-be-mounted"):
            self.deployment.preflight()
        self.runner.run.assert_not_called()


@unittest.skipUnless(
    os.environ.get("RUN_RESTIC_TESTS") == "1", "Set RUN_RESTIC_TESTS=1 with Restic on PATH"
)
class RealRepositoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        password = self.root / "password"
        password.write_text("Synthetic-backup-test-only\n")
        password.chmod(0o600)
        self.environment = patch.dict(
            os.environ,
            {
                **{
                    key: value for key, value in os.environ.items() if not key.startswith("RESTIC_")
                },
                "RESTIC_REPOSITORY": str(self.root / "repository"),
                "RESTIC_PASSWORD_FILE": str(password),
            },
            clear=True,
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.runner = Runner(120)
        self.runner.run(["restic", "init"], "test-init")
        self.repository = Repository(self.runner, "drill")
        self.repository.preflight()

    def test_real_encrypted_restore_retention_and_pending_isolation(self):
        directory = make_bundle(self.root)
        identifiers = []
        for index in range(3):
            identifiers.append(
                self.repository.store(
                    directory, uuid4().hex, self.root / f"verify-{index}", 1024**2
                )
            )
        self.runner.run(
            [
                "restic",
                "backup",
                "--host",
                "drill",
                "--tag",
                self.repository.tag + "," + PENDING,
                "--",
                directory.name,
            ],
            "test-pending",
            cwd=self.root,
        )
        self.runner.run(
            [
                "restic",
                "backup",
                "--host",
                "other",
                "--tag",
                "uta-deployment:other," + COMPLETE,
                "--",
                directory.name,
            ],
            "test-other",
            cwd=self.root,
        )
        self.repository.retain(last=1, daily=0, weekly=0, monthly=0, dry_run=True)
        self.assertEqual(len(self.repository.snapshots()), 3)
        self.repository.retain(last=1, daily=0, weekly=0, monthly=0, dry_run=False)
        self.assertEqual(len(self.repository.snapshots()), 1)
        self.assertEqual(len(self.repository.snapshots(PENDING)), 1)
        all_snapshots = self.runner.json(["restic", "snapshots", "--json"], "test-list")
        self.assertEqual(len([item for item in all_snapshots if item["hostname"] == "other"]), 1)
        snapshot = self.repository.select("latest")
        restored = self.repository.restore(snapshot, self.root / "final-restore", 1024**2)
        self.assertEqual(
            (restored / "database.dump").read_bytes(), (directory / "database.dump").read_bytes()
        )
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{64}", item) for item in identifiers))

    def test_corrupt_bundle_never_receives_complete_tag(self):
        directory = make_bundle(self.root)
        (directory / "database.dump").write_bytes(b"PGDMPcorrupt")
        with self.assertRaisesRegex(BackupError, "integrity"):
            self.repository.store(directory, uuid4().hex, self.root / "bad-restore", 1024**2)
        self.assertEqual(self.repository.snapshots(), [])
        self.assertEqual(len(self.repository.snapshots(PENDING)), 1)

    def test_wrong_password_fails_without_disclosing_secret(self):
        password = self.root / "wrong-password"
        password.write_text("Secret-that-must-not-appear\n")
        password.chmod(0o600)
        with (
            patch.dict(os.environ, {"RESTIC_PASSWORD_FILE": str(password)}),
            self.assertRaises(BackupError) as raised,
        ):
            self.repository.preflight()
        self.assertNotIn("Secret-that", str(raised.exception))


class DatabaseRestoreTests(unittest.TestCase):
    def runner(self, *, fail=None, space=1024**4, revisions=None):
        runner = Mock(timeout=15)

        def run(_arguments, step, **_options):
            if step == fail:
                raise BackupError(step)
            return {
                "create-database-restore-probe": b"a" * 64,
                "database-restore-space": f"Filesystem 1024-blocks Used Available Capacity Mounted\nprobe 100 0 {space // 1024} 0% /var/lib/postgresql\n".encode(),
                "inspect-restored-history": b"t\n",
                "inspect-database-restore-process": b"postgres\n",
            }.get(step, b"")

        runner.run.side_effect = run
        runner.json.return_value = ["source"] if revisions is None else revisions
        return runner

    def restore(self, runner):
        from deployment_backups.database import verify_database_restore

        verify_database_restore(
            runner,
            Path("backup.dump"),
            {
                "user": "owner",
                "database_bytes": 100,
                "revisions": ["source"],
                "encoding": "UTF8",
                "lc_collate": "C",
                "lc_ctype": "C",
            },
            "sha256:" + "b" * 64,
        )

    def test_real_restore_command_is_offline_and_cleans_only_created_id(self):
        runner = self.runner()
        self.restore(runner)
        calls = runner.run.call_args_list
        create = calls[0].args[0]
        self.assertEqual(create[:4], ["docker", "create", "--network", "none"])
        self.assertNotIn("--volumes-from", create)
        self.assertNotIn("POSTGRES_PASSWORD", " ".join(create))
        restore = next(call for call in calls if call.args[1] == "restore-database-archive")
        self.assertIn("--single-transaction", restore.args[0])
        self.assertEqual(restore.kwargs["input_file"], Path("backup.dump"))
        self.assertEqual(calls[-1].args[0], ["docker", "rm", "--force", "--volumes", "a" * 64])
        self.assertEqual(calls[-1].kwargs["timeout"], 10)

    def test_create_failure_never_removes_any_container(self):
        runner = self.runner(fail="create-database-restore-probe")
        with self.assertRaises(BackupError):
            self.restore(runner)
        self.assertEqual(runner.run.call_count, 1)

    def test_restore_failure_or_disk_full_never_looks_verified(self):
        for runner in (
            self.runner(fail="restore-database-archive"),
            self.runner(space=0),
            self.runner(revisions=["wrong"]),
        ):
            with self.subTest(runner=runner), self.assertRaises(BackupError):
                self.restore(runner)
            self.assertEqual(runner.run.call_args.args[1], "remove-database-restore-probe")

    def test_bad_probe_id_is_never_used_for_cleanup(self):
        runner = self.runner()
        runner.run.side_effect = None
        runner.run.return_value = b"live-app-name"
        with self.assertRaisesRegex(BackupError, "invalid-database-restore-probe-id"):
            self.restore(runner)
        self.assertEqual(runner.run.call_count, 1)

    def test_environment_override_and_short_deadline(self):
        result = Runner(10).run(
            [sys.executable, "-c", "import os; print(os.environ['UTA_PROBE'])"],
            "environment",
            environment={"UTA_PROBE": "value"},
            timeout=1,
        )
        self.assertEqual(result.strip(), b"value")
        with self.assertRaisesRegex(BackupError, "timeout"):
            Runner(10).run(
                [sys.executable, "-c", "import time; time.sleep(2)"], "short", timeout=0.1
            )


class UpgradePreparationTests(unittest.TestCase):
    def test_failed_prepare_keeps_writers_stopped_and_never_issues_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for failure in ("stop", "capture", "repository", "restore", None):
                deployment = Mock(files=[])
                deployment.preflight.return_value = {
                    "database": {"database_bytes": 1},
                    "mounts": [],
                    "writers": {"app": True},
                }

                def capture(directory, _metadata, _maximum, failure=failure):
                    if failure == "capture":
                        raise BackupError("dump-failed")
                    make_bundle(directory.parent)
                    (directory / "manifest.json").unlink()

                deployment.capture.side_effect = capture
                if failure == "stop":
                    deployment.stop_writers.side_effect = BackupError("stop-failed")
                repository = Mock()
                repository.store.return_value = "a" * 64
                if failure == "repository":
                    repository.store.side_effect = BackupError("upload-failed")
                preparation = Mock(image="sha256:" + "b" * 64)
                if failure == "restore":
                    preparation.verify_and_issue.side_effect = BackupError("restore-failed")
                options = SimpleNamespace(
                    state_dir=root,
                    max_bytes=1024**2,
                    deployment_id="drill",
                    command="prepare-upgrade",
                    upgrade_image=preparation.image,
                )
                report = {"run_id": uuid4().hex, "backup_complete": False}
                with (
                    patch("backup_deployment.UpgradePreparation", return_value=preparation),
                    patch.dict(os.environ, {"RESTIC_PASSWORD_FILE": str(root / "key")}),
                ):
                    if failure:
                        with self.assertRaises(BackupError):
                            backup(deployment, repository, root, options, report)
                    else:
                        backup(deployment, repository, root, options, report)
                deployment.resume_writers.assert_not_called()
                if failure == "stop":
                    preparation.invoke.assert_not_called()
                else:
                    preparation.invoke.assert_called_once_with()
                if failure in ("stop", "capture", "repository"):
                    preparation.verify_and_issue.assert_not_called()
                self.assertEqual(report.get("upgrade_recovery_verified", False), failure is None)
                self.assertFalse(report["writers_resumed"])
                self.assertEqual(
                    bundle.load_json(root / "drill-maintenance.json")["phase"],
                    "ready-for-upgrade"
                    if failure is None
                    else (
                        "stop-failed-check-writers" if failure == "stop" else "stopped-for-upgrade"
                    ),
                )

    def test_captured_keys_match_persistent_volume_and_environment(self):
        import hashlib

        from deployment_backups.upgrade import captured_key_digest

        key = "XJVqc8r7Uj801VzGUYhlQV8A2tZTBepjOYEiP2pTzFk="
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive(
                directory / "data.tar", [("data/config/fernet.key", key.encode(), tarfile.REGTYPE)]
            )
            metadata = {
                "services": {"app": {"environment": {}}},
                "mounts": [{"role": "app", "destination": "/data", "archive": "data.tar"}],
            }
            self.assertEqual(
                captured_key_digest(directory, metadata), hashlib.sha256(key.encode()).hexdigest()
            )
            metadata["services"]["app"]["environment"]["SECRET_KEY"] = (
                "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
            )
            with self.assertRaisesRegex(BackupError, "conflicting"):
                captured_key_digest(directory, metadata)
            archive(
                directory / "data.tar", [("data/config/fernet.key", b"damaged", tarfile.REGTYPE)]
            )
            metadata["services"]["app"]["environment"] = {}
            with self.assertRaisesRegex(BackupError, "missing"):
                captured_key_digest(directory, metadata)

    def test_issuer_never_receives_repository_credentials_or_argv_secrets(self):
        from deployment_backups.upgrade import UpgradePreparation

        deployment = Mock()
        network = {"default": {"NetworkID": "c" * 64}}
        app = Instance(
            "app",
            "app",
            "a" * 64,
            {
                "Config": {
                    "Env": [
                        "POSTGRES_PASSWORD=private-test",
                        "AWS_SECRET_ACCESS_KEY=repository-only",
                    ]
                },
                "NetworkSettings": {"Networks": network},
            },
        )
        database = Instance("database", "db", "d" * 64, {"NetworkSettings": {"Networks": network}})
        deployment.instances = {"app": app, "database": database}
        deployment.runner.json.return_value = [{"Id": "sha256:" + "b" * 64}]
        deployment.runner.run.return_value = b"e" * 64
        preparation = UpgradePreparation(deployment, "sha256:" + "b" * 64)
        preparation.invoke()
        create = deployment.runner.run.call_args_list[0]
        self.assertNotIn("private-test", " ".join(create.args[0]))
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", create.kwargs["environment"])
        self.assertEqual(create.kwargs["environment"]["POSTGRES_PASSWORD"], "private-test")
        self.assertNotIn("--volumes", deployment.runner.run.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
