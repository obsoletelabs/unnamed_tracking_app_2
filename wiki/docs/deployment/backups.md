# Full deployment backups

`tools/backup_deployment.py` is a Linux/WSL **operator CLI**, run on the Docker host with Python 3.12+, Docker Compose v2, and Restic 0.16.4+. The web application receives no Docker socket or backup-repository credentials. Existing per-user JSON snapshots and Settings import/export remain separate.

## Recovery boundary

The supported topology is one explicit Compose project with exactly one application, one plugin runtime and one PostgreSQL service, as in `example-docker-compose.yaml`. Default service names are `app`, `plugin-runtime`, and `db`; the CLI has service-name overrides. Use the same absolute Compose files, in the same order, that created the containers. Scaled writers, external databases, multiple app deployments sharing one database, and storage changed outside these services require an independently coordinated workflow.

The operator must suspend any external writers, deployment automation, plugin installers and other maintenance tools for the capture. The CLI refuses existing additional database clients after stopping the app/runtime; this check does not prevent an uncoordinated external writer connecting later. Run one backup scheduler per deployment and do not deploy concurrently. The local lock prevents overlapping invocations on that operator host, not distributed maintenance.

Each bundle contains:

| Component | Capture |
| --- | --- |
| Application database | PostgreSQL custom-format logical dump, using the database container's native `pg_dump`, with owners/ACLs excluded |
| Application persistent files | `/data`, including accounts' uploads, notes, saves, stored configuration and persistent Fernet key copies |
| Plugin state | All runtime bind/named mounts, including `/var/lib/unnamed-tracking/plugins`, with numeric ownership and permissions |
| Additional mounted configuration | App/runtime mounts and database configuration mounts; the live PostgreSQL data mount is excluded |
| Deployment context | Original supplied Compose files, resolved container environment, image IDs/tags, mount map, PostgreSQL version, database name/user and Alembic revision(s) |
| Integrity | Versioned manifest with exact file set, byte lengths and SHA-256 digests |

All bind/named mounts of these services are captured, except PostgreSQL's live data directory. Other services (reverse proxies, identity providers, DNS, registries and external object stores), PostgreSQL cluster roles/tablespaces/server-wide configuration, and container writable layers are outside the bundle. Store application state on declared persistent mounts. Keep matching application/runtime images available by immutable digest or an independently protected image archive; a recorded image ID cannot download a deleted image. Restore to the **same PostgreSQL major version** first; perform a separately tested PostgreSQL upgrade afterward.

The CLI verifies that the application's component-form database configuration (preferred when complete), or its legacy `DATABASE_URL`, points to this database service on a shared network with matching database/user/password. Configuration supplied only in a container-local `.env` must be made explicit in the Compose environment before this workflow can verify it.

## Encryption and protected state

Restic encrypts the repository. Supported locations are an absolute local path, SFTP, HTTPS REST, or an explicitly HTTPS S3 endpoint. Plain HTTP is refused. SFTP uses the operator's SSH configuration: pin the host key and use a restricted dedicated account with key authentication. HTTPS verifies certificates using Restic's normal CA configuration; this wrapper provides no insecure-TLS option.

Create new private operator directories (0700), outside all application mounts, on a disk with sufficient free space. Run as their owner. Existing public directories are refused rather than silently changing their permissions. Use an encrypted host disk for plaintext staging; files are 0600 and temporary directories 0700, but deletion is not secure erasure on SSDs or copy-on-write storage.

For example, in a protected operator shell:

```bash
umask 077
mkdir -m 0700 /var/lib/uta-backups /etc/unnamed-tracking-backup
openssl rand -base64 48 > /etc/unnamed-tracking-backup/restic-password
export RESTIC_PASSWORD_FILE=/etc/unnamed-tracking-backup/restic-password
export RESTIC_REPOSITORY=sftp:backup-host:/srv/restic/unnamed-tracking
restic init
```

Do not initialize over an existing repository. Test credentials with `restic snapshots`. Keep the Restic password and backend/SSH credentials in a **separate protected recovery store**, available if this host is lost. Never put them in app/runtime environment variables or beneath application data mounts. The CLI requires an owner-only password file and never prints child output, environment values or private configuration in error messages. Use a dedicated operator environment without `RESTIC_PASSWORD_COMMAND` or `RESTIC_REPOSITORY_FILE` overrides.

**The encrypted bundle contains deployment secrets**, including resolved environment and app encryption keys. Restrict repository/password access accordingly; do not attach recovered bundles or `deployment.json` to support tickets. Retain separately the Restic password, backend credentials, SSH host keys, registry access, any unmounted operator configuration and recovery instructions. An app encryption key alone cannot decrypt the Restic repository; a new app encryption key cannot decrypt old application secrets.

## Plan, capture and verify

From the complete source checkout:

```bash
python3 tools/backup_deployment.py plan \
  --deployment-id main --project unnamed-tracking \
  --compose-file /srv/unnamed-tracking/example-docker-compose.yaml

python3 tools/backup_deployment.py backup \
  --deployment-id main --project unnamed-tracking \
  --compose-file /srv/unnamed-tracking/example-docker-compose.yaml \
  --state-dir /var/lib/uta-backups --max-bytes 107374182400
```

Choose a stable `--deployment-id`, unique to this deployment within the repository, and retain it across replacement hosts. `plan` reads repository/container/database metadata and reports counts; it does not stop services or create a snapshot. `backup` checks credentials, database identity, persistent roots and staging space before stopping writers. A default maximum of 100 GiB requires at least 200 GiB free staging space for capture plus verified restoration. Set an appropriate smaller/larger limit for your deployment. `--staging-dir` selects a separate private staging disk. No staging/repository/password file may lie inside a captured bind mount.

The maintenance window stops app and runtime, checks that they stopped, dumps PostgreSQL and copies persistent mounts. PostgreSQL remains running; its live volume is never copied. App/runtime are restarted in a `finally` block, including when stopping or capture fails. Only services running before backup are restarted. Configured container health checks must become healthy. Hashing, upload and verification then run with the writers resumed.

The source is bounded by `--max-bytes`; each subprocess has `--timeout` (default 1,800 seconds), and Restic transfers default to `--rate-kib 10240`. Each mount archive is limited to 100,000 entries. Unsafe/truncated archives, special-device entries and links escaping a mount are refused. Archives are inspected without extracting files on the host. Very large deployments should use dedicated filesystem quotas and an appropriately sized maintenance window; these limits do not provide distributed coordination or a storage snapshot.

Uploads initially carry `uta-pending-v1`. A complete capture requires successful `pg_dump` and `pg_restore --list`, bundle validation, Restic exit status zero, repository integrity check, and **Restic restoration to another private directory with content verification plus manifest validation**. Only then is the snapshot promoted to `uta-complete-v1`. Partial-read exit status 3, failed producers or interrupted uploads never qualify. The final snapshot ID is re-read because changing Restic tags changes the ID. Pending snapshots remain available for operator diagnosis and are excluded from restores/retention by default.

A verified bundle is not proof that a particular application upgrade will succeed. Rehearse restoration and upgrades in a separate isolated deployment. Use [`prepare-upgrade`](migration-recovery.md#verified-pre-upgrade-recovery) for the required pre-migration gate: unlike ordinary `backup`, it holds writers stopped through verification, SQL-restores the recovered dump and issues evidence for the exact target image.

## Scheduling, retention and alerts

Example systemd units live in `tools/deployment_backups/systemd/`. Copy and edit the source path, Compose path/project, capacity and `EnvironmentFile` before installing. The environment file is owner-only and contains `RESTIC_REPOSITORY`, `RESTIC_PASSWORD_FILE`, plus any backend credentials; it must never be mounted in the app. The example assumes a protected checkout at `/opt/unnamed-tracking`, production files at `/srv/unnamed-tracking`, and `/etc/unnamed-tracking/backup.env`. Keep those secrets outside the source checkout.

```bash
systemctl daemon-reload
systemctl start uta-backup.service
systemctl status uta-backup.service
systemctl enable --now uta-backup.timer
systemctl list-timers uta-backup.timer
```

The example runs nightly at 03:00 local time with up to ten minutes of jitter, catches missed runs, and runs retention only after successful backup. It keeps at least the last three completed snapshots, seven daily, four weekly and twelve monthly snapshots. Preview or apply manually:

```bash
python3 tools/backup_deployment.py retention --deployment-id main
python3 tools/backup_deployment.py retention --deployment-id main --apply
```

Retention matches **both** this deployment's tag and the complete tag, filters the exact host identifier, and groups by host rather than changing staging paths/run tags. It never treats partial snapshots as completed backups. Pruning is followed by a repository check. Keep at least one completed snapshot; zero is refused. Other deployments' snapshots remain outside the selection. Repository-wide prune/check can contend with other operators; use a dedicated repository or schedule operations together. A local repository alone does not survive loss of its host/disk: select an off-host backend and rehearse recovery from that backend.

Every operation prints one safe JSON result and exits nonzero on failure. Private status files are separate per command, for example `/var/lib/uta-backups/main-backup.json`, and retain the last successful backup timestamp after failure. Monitor **both failed service runs and the age of `last_success_at`**; a timer never running cannot raise its own failure. Connect systemd `OnFailure=` to your existing alerting service and configure an independent stale-backup alert. No email/webhook destination is guessed or contacted by this tool. Inspect `journalctl -u uta-backup.service` for phase/exit codes; secret-bearing child diagnostics are deliberately suppressed.

Nightly successful backups imply an RPO of approximately 24 hours plus scheduling/capture delay; a failed run extends that interval. RTO depends on database/files size, network throughput, available images and operational practice. Measure it during a drill rather than assuming a fixed recovery duration.

For ordinary `backup`, SIGINT/SIGTERM attempt writer restart. `prepare-upgrade` deliberately leaves writers stopped, including after interruption; inspect its separate status and maintenance records before continuing. SIGKILL, power loss, Docker failure or a second interruption can prevent cleanup. Each run first persists a `running` status; before stopping writers it also saves their previous running states in `main-maintenance.json` (using your deployment ID). After an interrupted maintenance window, inspect these private records, check app/runtime state and explicitly start the services that were previously running. Preserve failed artifacts for diagnosis only in protected storage. After resolving a stale Restic lock, follow Restic's documented unlock procedure; the CLI never silently removes repository locks.

## Restore into a disposable deployment

Never restore directly over production. First recover the protected Restic password/backend credentials and matching images on a separate operator host. Select a new, nonexistent output directory under a protected parent:

```bash
python3 tools/backup_deployment.py restore --deployment-id main \
  --snapshot latest --target /srv/recovery/main-20261020
```

This downloads a qualified completed snapshot and verifies its bundle; it does **not** connect to a database, change a container, or overwrite a live volume. Restic restores the original absolute staging tree beneath the new target. Locate the one `bundle/manifest.json` beneath that target, then inspect its private `deployment.json` and mount map. Use the snapshot ID in the JSON result to pin subsequent work. An existing target is refused; an incomplete restore remains for diagnosis and must not be reused as a verified bundle.

Recheck a downloaded bundle offline with `python3 tools/backup_deployment.py verify --deployment-id main --target /path/to/bundle`. This validates the exact file set, sizes, hashes and archive structure without repository credentials.

1. Create a new Compose file/project and new, empty persistent volumes. Use saved app/runtime images and PostgreSQL's recorded major version. Recreate the application database role and database from the private metadata/environment. Preserve the original app encryption keys. Do not blindly run the archived Compose file: it may point to production binds, ports, external services or volumes.
2. Keep the restored app/runtime **stopped**. Put the clone on an internal Docker network with no outgoing access or public ingress. Restore data before app startup so it cannot generate replacement keys, run migrations or emit notifications against external accounts. Use a separate ingress/probe container on that private network if UI inspection is needed.
3. Start only the new database and wait for readiness. Create the stopped app/runtime containers so their new mounts exist. Confirm the target database is empty and every destination mount belongs to the disposable project.
4. Restore the logical dump with matching PostgreSQL tooling. Substitute the disposable database container ID, database and role (the example contains no password on the command line):

   ```bash
   docker exec -i DISPOSABLE_DB pg_restore --username=APP_ROLE --dbname=APP_DATABASE \
     --no-owner --no-acl --exit-on-error < BUNDLE/database.dump
   ```

5. For **each** mount listed in `deployment.json`, restore its TAR into the **parent** of its recorded container destination. TAR roots retain their original basename. For example `/data` uses `/`, and `/var/lib/unnamed-tracking/plugins` uses `/var/lib/unnamed-tracking`. Use the stopped disposable container ID and preserve numeric ownership with `-a`:

   ```bash
   docker cp -a - DISPOSABLE_APP:/ < BUNDLE/mount-0000.tar
   ```

   Map by `role` and `destination`, never assume `mount-0000.tar` is `/data`. Additional config/file mounts must also be recreated and restored; writable backing copies may be needed before recreating a read-only secret mount. Restore no raw PostgreSQL data archive: none exists in this format.
6. Start the disposable app/runtime, wait for readiness, then sign in as ordinary users. Verify private library ownership, notes, media, uploaded files, save downloads/checksums, preferences, plugin state and decryption of pre-existing encrypted credentials. Confirm outsiders cannot read another user's records. Compare IDs/counts and file hashes against the pre-backup inventory.
7. Record snapshot ID, source image IDs, PostgreSQL version, recovery duration, verification results and any exceptions. Plan production recovery separately after the drill passes. Keep the clone isolated until external integrations have been explicitly reviewed.

`docker compose down -v` **destroys named volumes**. It is appropriate only when deliberately removing the disposable drill, or when recovering from an independently tested off-host backup. Do not use it as a normal update step.

## Reproducible acceptance

The [deployment backup recovery workflow](https://github.com/obsoletelabs/unnamed_tracking_app_2/blob/main/.github/workflows/deployment-backups.yml) runs real Restic tests and builds isolated backend/runtime/SFTP images. Run the same checks locally:

```bash
RUN_RESTIC_TESTS=1 python3 -m unittest discover -s tools/tests -p test_deployment_backups.py -v
docker build -t backup-drill-app -f src/backend/dockerfile src/backend
docker build -t backup-drill-runtime -f src/plugin-runtime/Dockerfile .
docker build -t backup-drill-sftp -f tools/backup_acceptance/Dockerfile.sftp tools/backup_acceptance
python3 tools/check_deployment_backups.py --app-image backup-drill-app \
  --runtime-image backup-drill-runtime --sftp-image backup-drill-sftp
```

The acceptance script generates its own project names, volumes, keys and repository. It seeds two ordinary users through real endpoints, backs up over SFTP with a pinned host key, removes the original application/database/plugin volumes, and restores to a fresh internal-network deployment. It exercises private-user endpoints again, checks save/upload bytes, preserves plugin file ownership/mode, and decrypts a synthetic stored credential using the restored persistent key. It contains no plugin implementation. SFTP is a separate container/volume test endpoint; actual host/disk/availability-zone resilience still requires an operator drill against your off-host storage. S3/HTTPS REST support follows Restic's backend contract; the automated transport drill covers SFTP, not live cloud-provider credentials.

Operational references: [PostgreSQL logical dumps](https://www.postgresql.org/docs/18/app-pgdump.html), [Docker archive copying](https://docs.docker.com/reference/cli/docker/container/cp/), [Restic repository backends](https://restic.readthedocs.io/en/stable/030_preparing_a_new_repo.html) and [Restic retention filters](https://restic.readthedocs.io/en/stable/060_forget.html).
