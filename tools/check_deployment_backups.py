#!/usr/bin/env python3
"""Destroy and recover only generated disposable projects, using real APIs and SFTP."""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.cookiejar
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from uuid import uuid4

from deployment_backups import bundle
from deployment_backups.process import BackupError, Runner, private_directory
from deployment_backups.repository import Repository

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "Backup-acceptance-only-123!"
TOKEN = "backup-acceptance-only-runtime-token-20261020"
MAXIMUM = 256 * 1024**2


class Client:
    def __init__(self, origin):
        self.origin = origin
        self.cookie = ""
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def request(
        self, method, route, value=None, *, raw=None, content_type=None, status=200, binary=False
    ):
        data = raw if raw is not None else json.dumps(value).encode() if value is not None else None
        headers = {"Content-Type": content_type or "application/json"} if data is not None else {}
        request = urllib.request.Request(
            self.origin + route, data=data, headers=headers, method=method
        )
        if self.origin.startswith("docker://"):
            script = """
import base64, json, sys, urllib.error, urllib.request
value = json.loads(sys.argv[1])
headers = value['headers']
if value['cookie']: headers['Cookie'] = value['cookie']
data = base64.b64decode(value['data']) if value['data'] is not None else None
request = urllib.request.Request('http://127.0.0.1:8000' + value['route'], data=data, headers=headers, method=value['method'])
try:
    response = urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=10)
except urllib.error.HTTPError as error:
    response = error
with response:
    print(json.dumps({'status': response.status, 'data': base64.b64encode(response.read()).decode(), 'cookies': response.headers.get_all('Set-Cookie', [])}))
"""
            result = Runner(20).json(
                [
                    "docker",
                    "exec",
                    self.origin.removeprefix("docker://"),
                    "python",
                    "-c",
                    script,
                    json.dumps(
                        {
                            "method": method,
                            "route": route,
                            "headers": headers,
                            "cookie": self.cookie,
                            "data": base64.b64encode(data).decode() if data is not None else None,
                        }
                    ),
                ],
                "acceptance-private-api",
            )
            if result["status"] != status:
                raise BackupError(
                    f"acceptance-http-{method}-{route}-expected-{status}-got-{result['status']}"
                )
            if result["cookies"]:
                self.cookie = "; ".join(value.split(";", 1)[0] for value in result["cookies"])
            content = base64.b64decode(result["data"])
            return content if binary else json.loads(content) if content else None
        try:
            response = self.opener.open(request, timeout=20)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            if response.status != status:
                raise BackupError(
                    f"acceptance-http-{method}-{route.split('?')[0]}-expected-{status}-got-{response.status}"
                )
            content = response.read()
            return content if binary else json.loads(content) if content else None

    def login(self, username):
        self.request(
            "POST", "/api/auth/login", {"username_or_email": username, "password": PASSWORD}
        )
        return self


def multipart(fields, filename, content):
    boundary = "uta-backup-" + uuid4().hex
    result = bytearray()
    for name, value in fields.items():
        result.extend(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    result.extend(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
    )
    result.extend(content)
    result.extend(f"\r\n--{boundary}--\r\n".encode())
    return bytes(result), "multipart/form-data; boundary=" + boundary


class Drill:
    def __init__(self, directory, options):
        self.directory = directory
        self.options = options
        self.runner = Runner(300)
        self.projects = []
        self.started_at = time.monotonic()
        self.identifier = "uta-backup-drill-" + uuid4().hex[:12]

    def compose(self, project, *arguments, **kwargs):
        return self.runner.run(
            [
                "docker",
                "compose",
                "-p",
                project,
                "-f",
                str(self.directory / (project + ".json")),
                *arguments,
            ],
            "acceptance-compose",
            **kwargs,
        )

    def container(self, project, service):
        return self.compose(project, "ps", "--all", "--quiet", service).decode().strip()

    def configuration(self, suffix, environment=None):
        project = self.identifier + "-" + suffix
        app_environment = {
            "POSTGRES_USER": "backup",
            "POSTGRES_PASSWORD": PASSWORD,
            "POSTGRES_DB": "backup",
            "POSTGRES_HOST": "db",
            "ENV_FILE": "/dev/null",
            "STARTUP_MODE": "testing",
            "PRIMARY_USER_USERNAME": "backupadmin",
            "PRIMARY_USER_EMAIL": "admin@example.invalid",
            "PRIMARY_USER_PASSWORD": PASSWORD,
            "GAME_IDENTITY_BACKFILL": "off",
            "DEBUG": "false",
            "PLUGIN_RUNTIME_TOKEN": TOKEN,
            "PLUGIN_RUNTIME_URL": "http://plugin-runtime:8000",
        }
        if environment is not None:
            app_environment = environment
        data = {
            "services": {
                "app": {
                    "image": self.options.app_image,
                    "environment": app_environment,
                    "volumes": ["appdata:/data"],
                    "healthcheck": {
                        "test": [
                            "CMD",
                            "python",
                            "-c",
                            "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)",
                        ],
                        "interval": "2s",
                        "timeout": "3s",
                        "retries": 60,
                    },
                },
                "plugin-runtime": {
                    "image": self.options.runtime_image,
                    "environment": {
                        "PLUGIN_RUNTIME_TOKEN": TOKEN,
                        "PLUGIN_GATEWAY_URL": "http://app:8000",
                    },
                    "volumes": ["plugindata:/var/lib/unnamed-tracking/plugins"],
                },
                "db": {
                    "image": "postgres:18",
                    "environment": {
                        "POSTGRES_USER": "backup",
                        "POSTGRES_PASSWORD": PASSWORD,
                        "POSTGRES_DB": "backup",
                    },
                    "volumes": ["pgdata:/var/lib/postgresql"],
                    "healthcheck": {
                        "test": ["CMD", "pg_isready", "-U", "backup", "-d", "backup"],
                        "interval": "1s",
                        "retries": 60,
                    },
                },
            },
            "volumes": {"appdata": {}, "plugindata": {}, "pgdata": {}},
            "networks": {"default": {"internal": True}},
        }
        (self.directory / (project + ".json")).write_text(json.dumps(data))
        self.projects.append(project)
        return project

    def origin(self, project):
        origin = "docker://" + self.container(project, "app")
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            try:
                Client(origin).login("backupadmin")
                return origin
            except (BackupError, OSError):
                time.sleep(1)
        raise BackupError("acceptance-app-did-not-start")

    def seed(self, origin, project):
        admin = Client(origin).login("backupadmin")
        samples = []
        for index in range(2):
            username = "reader" + str(index)
            account = admin.request(
                "POST",
                "/api/auth/users",
                {
                    "username": username,
                    "email": username + "@example.invalid",
                    "password": PASSWORD,
                },
                status=201,
            )
            client = Client(origin).login(username)
            game = client.request(
                "POST",
                "/api/game/create",
                {
                    "title": "Private game " + str(index),
                    "folder_location": "SameFolder",
                    "playtime_seconds": 1234 + index,
                },
                status=201,
            )
            movie = client.request(
                "POST",
                "/api/movie/create",
                {"title": "Private movie " + str(index), "note": "Private media note"},
                status=201,
            )
            client.request(
                "POST",
                f"/api/game/{game['id']}/notes/Recovery",
                {"content": "private markdown " + str(index)},
                status=201,
            )
            client.request("PATCH", "/api/preferences", {"calendar_week_start": index})
            save = io.BytesIO()
            with zipfile.ZipFile(save, "w") as archive:
                archive.writestr("progress.txt", "private save " + str(index))
            content, kind = multipart({"name": "Recovery slot"}, "save.zip", save.getvalue())
            archive = client.request(
                "POST", f"/api/game/{game['id']}/archives/save", raw=content, content_type=kind
            )
            content, kind = multipart({}, "manual.txt", b"private uploaded manual")
            client.request(
                "POST", f"/api/game/{game['id']}/files/doc", raw=content, content_type=kind
            )
            samples.append(
                {
                    "username": username,
                    "account": account,
                    "game": game,
                    "movie": movie,
                    "archive": archive,
                    "save": save.getvalue(),
                }
            )
        script = """
import asyncio
from sqlalchemy import select
from src.database.session import SessionLocal
from src.database.models.user import User
from src.core.crypto import encrypt_secret
async def seed():
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.username == 'reader0'))
        user.steam_api_key = encrypt_secret('synthetic-encryption-continuity')
        await session.commit()
asyncio.run(seed())
"""
        self.runner.run(
            ["docker", "exec", self.container(project, "app"), "python", "-c", script],
            "seed-encrypted-fixture",
        )
        script = "from pathlib import Path; p=Path('/var/lib/unnamed-tracking/plugins/backup-state.json'); p.write_text('{\"count\":42}'); p.chmod(0o600)"
        self.runner.run(
            ["docker", "exec", self.container(project, "plugin-runtime"), "python", "-c", script],
            "seed-runtime-state",
        )
        return samples

    def verify_api(self, origin, samples):
        for index, sample in enumerate(samples):
            client = Client(origin).login(sample["username"])
            current = client.request("GET", "/api/auth/me")
            if current["is_admin"]:
                raise BackupError("acceptance-ordinary-user-became-admin")
            game = client.request("GET", "/api/game/get/" + sample["game"]["id"])
            if game["title"] != sample["game"]["title"] or game["playtime_seconds"] != 1234 + index:
                raise BackupError("acceptance-game-data-differs")
            foreign = samples[1 - index]["game"]["id"]
            client.request("GET", "/api/game/get/" + foreign, status=404)
            note = client.request("GET", f"/api/game/{game['id']}/notes/Recovery", binary=True)
            if note != ("private markdown " + str(index)).encode():
                raise BackupError("acceptance-note-differs")
            movies = client.request("GET", "/api/movie/list")
            if [movie["id"] for movie in movies["items"]] != [sample["movie"]["id"]]:
                raise BackupError("acceptance-media-ownership-differs")
            if client.request("GET", "/api/preferences")["calendar_week_start"] != index:
                raise BackupError("acceptance-preferences-differ")
            archive = client.request("GET", f"/api/game/{game['id']}/archives/save")[0]
            restored = client.request("GET", archive["versions"][0]["url"], binary=True)
            if hashlib.sha256(restored).digest() != hashlib.sha256(sample["save"]).digest():
                raise BackupError("acceptance-save-checksum-differs")
            documents = client.request("GET", f"/api/game/{game['id']}/files/doc")
            if not documents["files"]:
                raise BackupError("acceptance-upload-missing")
            uploaded = client.request(
                "GET",
                f"/api/game/{game['id']}/files/doc/" + documents["files"][0]["filename"],
                binary=True,
            )
            if uploaded != b"private uploaded manual":
                raise BackupError("acceptance-upload-checksum-differs")

    def transport(self):
        project = self.identifier + "-transport"
        self.projects.append(project)
        config = {
            "services": {
                "sftp": {
                    "image": self.options.sftp_image,
                    "ports": ["127.0.0.1::22"],
                    "volumes": ["repository:/repository"],
                }
            },
            "volumes": {"repository": {}},
        }
        (self.directory / (project + ".json")).write_text(json.dumps(config))
        self.compose(project, "up", "-d")
        identifier = self.container(project, "sftp")
        key = self.directory / "ssh-key"
        self.runner.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], "create-test-ssh-key"
        )
        self.runner.run(
            ["docker", "cp", str(key) + ".pub", identifier + ":/root/.ssh/authorized_keys"],
            "install-test-ssh-key",
        )
        self.runner.run(
            [
                "docker",
                "exec",
                identifier,
                "sh",
                "-c",
                "chown 0:0 /root/.ssh/authorized_keys; chmod 0600 /root/.ssh/authorized_keys; ssh-keygen -A",
            ],
            "prepare-test-sshd",
        )
        public = (
            self.runner.run(
                ["docker", "exec", identifier, "cat", "/etc/ssh/ssh_host_ed25519_key.pub"],
                "test-ssh-host-key",
            )
            .decode()
            .split()
        )
        port = self.compose(project, "port", "sftp", "22").decode().strip().rsplit(":", 1)[1]
        known = self.directory / "known-hosts"
        known.write_text(f"[127.0.0.1]:{port} {public[0]} {public[1]}\n")
        ssh_config = self.directory / "ssh-config"
        ssh_config.write_text(
            f"Host backup-drill\n HostName 127.0.0.1\n Port {port}\n User root\n IdentityFile {key}\n UserKnownHostsFile {known}\n StrictHostKeyChecking yes\n BatchMode yes\n IdentitiesOnly yes\n"
        )
        self.runner.run(
            [
                "docker",
                "exec",
                "-d",
                identifier,
                "/usr/sbin/sshd",
                "-D",
                "-E",
                "/tmp/sshd.log",
                "-o",
                "PasswordAuthentication=no",
                "-o",
                "PermitRootLogin=prohibit-password",
            ],
            "start-test-sftp",
        )
        native = shutil.which("restic")
        binary_dir = private_directory(self.directory / "bin")
        wrapper = binary_dir / "restic"
        wrapper.write_text(
            "#!/bin/sh\nexec "
            + shlex.quote(native)
            + " -o "
            + shlex.quote("sftp.args=-F " + str(ssh_config))
            + ' "$@"\n'
        )
        wrapper.chmod(0o700)
        os.environ["PATH"] = str(binary_dir) + os.pathsep + os.environ["PATH"]
        os.environ["RESTIC_REPOSITORY"] = "sftp:backup-drill:/repository"
        password = self.directory / "repository-password"
        password.write_text("synthetic-restic-acceptance-password\n")
        password.chmod(0o600)
        os.environ["RESTIC_PASSWORD_FILE"] = str(password)
        self.runner.run(["restic", "init"], "initialize-test-sftp-repository")

    def cli(self, command, *arguments, status=0):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools/backup_deployment.py"),
                command,
                "--deployment-id",
                self.identifier,
                "--state-dir",
                str(self.directory / "state"),
                "--max-bytes",
                str(MAXIMUM),
                *arguments,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=900,
            check=False,
        )
        report = json.loads(result.stdout)
        if result.returncode != status or (report["status"] == "success") != (status == 0):
            raise BackupError("acceptance-cli-failure:" + report.get("error", "unknown"))
        return report

    def run(self):
        self.transport()
        print("SFTP repository initialized with pinned host key", flush=True)
        source = self.configuration("source")
        self.compose(source, "up", "-d")
        samples = self.seed(self.origin(source), source)
        self.verify_api(self.origin(source), samples)
        print(
            "Populated two ordinary accounts, games, movies, notes, uploads, saves, settings and encrypted fixture",
            flush=True,
        )
        self.cli(
            "plan", "--project", source, "--compose-file", str(self.directory / (source + ".json"))
        )
        report = self.cli(
            "backup",
            "--project",
            source,
            "--compose-file",
            str(self.directory / (source + ".json")),
        )
        if not report["backup_complete"] or not report["writers_resumed"]:
            raise BackupError("acceptance-backup-not-complete-or-writers-stopped")
        self.verify_api(self.origin(source), samples)
        self.compose(source, "down", "--volumes")
        remaining = self.runner.run(
            [
                "docker",
                "volume",
                "ls",
                "--filter",
                "label=com.docker.compose.project=" + source,
                "--format",
                "{{.Name}}",
            ],
            "verify-original-volumes-removed",
        )
        if remaining.strip():
            raise BackupError("acceptance-original-volumes-still-exist")
        print(
            "Original app, database and plugin volumes destroyed; encrypted SFTP backup retained",
            flush=True,
        )
        recovery_started = time.monotonic()
        self.cli(
            "restore",
            "--snapshot",
            report["snapshot_id"],
            "--target",
            str(self.directory / "restored"),
        )
        repository = Repository(self.runner, self.identifier)
        snapshot = repository.select(report["snapshot_id"])
        restored = self.directory / "restored" / snapshot["paths"][0].lstrip("/")
        metadata = bundle.verify(restored, self.identifier, MAXIMUM)
        if any(path.stat().st_mode & 0o077 for path in restored.iterdir()):
            raise BackupError("acceptance-restored-bundle-permissions")
        target = self.configuration("restored", metadata["services"]["app"]["environment"])
        self.compose(target, "up", "-d", "--wait", "db")
        self.compose(target, "create", "app", "plugin-runtime")
        for mount in metadata["mounts"]:
            service = metadata["services"][mount["role"]]["service"]
            self.runner.run(
                [
                    "docker",
                    "cp",
                    "-a",
                    "-",
                    self.container(target, service)
                    + ":"
                    + str(PurePosixPath(mount["destination"]).parent),
                ],
                "restore-test-persistent-mount",
                input_file=restored / mount["archive"],
            )
        self.runner.run(
            [
                "docker",
                "exec",
                "-i",
                self.container(target, "db"),
                "pg_restore",
                "--username=backup",
                "--dbname=backup",
                "--no-owner",
                "--no-acl",
                "--exit-on-error",
            ],
            "restore-test-database",
            input_file=restored / "database.dump",
        )
        self.compose(target, "start", "app", "plugin-runtime")
        self.verify_api(self.origin(target), samples)
        script = """
import asyncio
from sqlalchemy import select
from src.database.session import SessionLocal
from src.database.models.user import User
from src.core.crypto import decrypt_secret
async def verify():
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.username == 'reader0'))
        assert decrypt_secret(user.steam_api_key) == 'synthetic-encryption-continuity'
asyncio.run(verify())
"""
        self.runner.run(
            ["docker", "exec", self.container(target, "app"), "python", "-c", script],
            "verify-restored-encryption",
        )
        script = "from pathlib import Path; import os; p=Path('/var/lib/unnamed-tracking/plugins/backup-state.json'); assert p.read_text() == '{\"count\":42}'; assert p.stat().st_uid == 10001; assert p.stat().st_mode & 0o777 == 0o600"
        self.runner.run(
            ["docker", "exec", self.container(target, "plugin-runtime"), "python", "-c", script],
            "verify-restored-runtime-state",
        )
        print(
            json.dumps(
                {
                    "status": "success",
                    "transport": "sftp",
                    "ordinary_accounts": 2,
                    "source_volumes_destroyed": True,
                    "encryption_continuity": True,
                    "file_ownership_preserved": True,
                    "recovery_seconds": round(time.monotonic() - recovery_started, 2),
                    "drill_seconds": round(time.monotonic() - self.started_at, 2),
                    "snapshot_id": report["snapshot_id"],
                }
            ),
            flush=True,
        )

    def cleanup(self):
        for project in reversed(self.projects):
            if not project.startswith(self.identifier + "-"):
                raise BackupError("refuse-cleanup-of-unowned-project")
            self.compose(project, "down", "--volumes")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-image", required=True)
    parser.add_argument("--runtime-image", required=True)
    parser.add_argument("--sftp-image", required=True)
    options = parser.parse_args()
    os.umask(0o077)
    for key in list(os.environ):
        if key.startswith("RESTIC_"):
            del os.environ[key]
    with tempfile.TemporaryDirectory(prefix="uta-backup-drill-") as temporary:
        drill = Drill(Path(temporary), options)
        try:
            drill.run()
        finally:
            drill.cleanup()


if __name__ == "__main__":
    main()
