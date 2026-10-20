# Migration inspection and recovery

Startup upgrades fresh databases and recognized revisions under the existing PostgreSQL
advisory lock. Unknown revisions, populated databases without a revision, inconsistent
ancestor/descendant revision records and missing required tables/columns stop startup.
The API remains unavailable. This replaces automatic adoption and automatic history replay
in both development and production. Existing reviewed merge points in the published history
remain supported; independent recorded parents can reach their recognized merge normally.

## Verified pre-upgrade recovery

`MIGRATION_BACKUP_MODE=required` is the default, including when `STARTUP_MODE` selects
development or testing defaults. Before changing a populated database, the runner requires
a recent recovery point issued for this deployment, source revision and **actual target
database Python sources**. This includes explicit history replay and repair at the current
head. A truly empty database needs no backup; a healthy unchanged database starts without
reading or renewing its recovery point. `--plan` remains read-only and writes no recovery
attempt report.

Use the [full deployment backup workflow](backups.md) on the Docker operator host. Keep
external writers, deployment automation and backup retention stopped throughout preparation
and upgrade. Its supported topology remains one app, one runtime and one database; scaled
or external writers require independently coordinated maintenance.

1. Pull/build the intended release without starting it. Keep the original app/runtime and
   PostgreSQL images available. Resolve the target application image to its immutable local
   ID with `docker image inspect --format '{{.Id}}' YOUR_TARGET_IMAGE`.
2. In the protected operator environment containing the existing Restic credentials, run:

   ```bash
   python3 tools/backup_deployment.py prepare-upgrade \
     --deployment-id main --project unnamed-tracking \
     --compose-file /srv/unnamed-tracking/example-docker-compose.yaml \
     --state-dir /var/lib/uta-backups \
     --upgrade-image sha256:REPLACE_WITH_COMPLETE_LOCAL_IMAGE_ID \
     --max-bytes 107374182400
   ```

   Use the exact currently deployed Compose files/project, before changing their image
   configuration. App/runtime writers stay **stopped on success and failure**. The command
   invalidates the previous approval after stopping writers; it captures PostgreSQL, files,
   configuration and encryption keys, uploads to Restic, restores and hashes the complete
   bundle, and actually SQL-restores the recovered dump in a new offline PostgreSQL container
   using the source database image. It checks source encoding/locales and restored revision
   records. The probe receives no live credentials, networks or volumes and is removed by
   its newly created container ID. The source PostgreSQL service remains running.
3. Require exit zero and `upgrade_recovery_verified: true`. Record `snapshot_id`, original
   image IDs and the target image ID. A one-shot container from that target image checks
   the existing key and live database/revisions, then atomically issues
   `APP_DATA_DIR/config/migration-recovery/point.json`. It inherits the original application
   UID and mounts, not Restic credentials or a Docker socket. Missing/conflicting keys fail
   before Settings can generate a replacement. UID or persistent-volume changes need an
   explicitly reviewed permission migration; this workflow never recursively changes ownership.
4. Keep writers stopped. Rehearse the intended release against a separate restored clone,
   using its own deployment-bound preparation where pending migrations are required.
   Update the real Compose application image to the exact verified target and start the
   designated migrator/app, then the other writers after migration succeeds. Before releasing
   traffic, verify ordinary-user records, file checksums, ownership and important workflows.
   Any change to the target database sources requires preparing a new point.

The default maximum capture age is two hours. Configure
`MIGRATION_BACKUP_MAX_AGE_SECONDS` on the deployment (60–604800 seconds) when a reviewed
maintenance window needs a different bound. Age uses the **capture**, not the end of
verification, against PostgreSQL's clock. Wrong source/target revisions, deployment key,
configured database or search path, modified code, tampering, stale evidence, unsafe file
permissions and verification failures halt before pending DDL. Password-only database
credential rotation does not invalidate database identity.

Recovery documents are owner-only files in a 0700 directory. HMAC evidence is a private
operator-to-runner contract, not an API or an assertion accepted from users/plugins.
Private unique `attempt-*.json` reports record waiting, inspected source/target revisions,
backup ID, timestamps, resulting revisions and failure class. Every attempt starts a new
report; a failed run cannot reuse a previous success. Driver SQL, passwords, keys and
validation input are excluded. Preserve/export these reports according to operator policy;
they are not automatically pruned. An unwritable/full report directory fails closed before
migration. A database outage records failure and cannot produce a success revision.

Both approval validation and migration remain under the session-level advisory lock. A
second starter waits, then rereads the database: after the first reaches head it performs
an unchanged startup without creating another backup. Lock contention is bounded at 60
seconds. PostgreSQL connection attempts have a five-second connect timeout. Operator
subprocesses use `--timeout`; the offline probe's readiness is bounded at 120 seconds and
cleanup at ten seconds. Staging requires twice the configured bundle budget, and the SQL
probe requires at least twice the source database size plus 128 MiB free. Use Restic retention
from the backup runbook, retaining the selected point throughout the upgrade/recovery window.

`MIGRATION_BACKUP_MODE=development` is the explicit opt-out for disposable development
data. It is reported in diagnostics and does not enable automatic adoption/repair. Do not
use it to bypass production preparation. The runner never automatically restores, downgrades
or deletes existing tables after failure. If migration fails, keep writers stopped, preserve
the failure report and inspect a separate restored deployment. Recover the recorded pre-upgrade
snapshot **with its original app/runtime and PostgreSQL image versions and encryption keys**
using the backup runbook. Do not start an older image against a partially migrated live database.

Always prepare a full deployment point for a release that changes persistent file formats,
even when its Alembic head is unchanged. The SQL gate cannot infer file-format changes or
prove application-level upgrade semantics from a successful SQL restore. A local Restic
repository on the same physical disk is not host-loss protection; use the documented off-host
backend and separately protected recovery credentials.
## Inspect before changing anything

Keep application writers stopped. Retain the current application image/version and record
the reported revisions and missing schema names. A second starting instance waits up to
60 seconds for the same database advisory lock, then stops safely if the first is still
working. After acquiring it, every instance reads the revision again.

From the backend working directory with the intended deployment configuration:

```bash
python -m src.database.migrate --plan
```

This reads schema and revision metadata without upgrading, stamping or replaying. An unsafe
history exits nonzero with recovery guidance. A recognized older revision is reported as
an upgrade candidate; inspection does not simulate pending SQL or prove the upgrade safe.
The runner requires one root and one final head. Never edit an old revision or stamp a
database merely to silence the error.

## Operator-approved repair

1. Make a supported PostgreSQL backup and preserve the matching persistent files, plugin
   state, configuration and encryption keys in protected storage. A per-user JSON export is
   insufficient. Retain an off-host recovery copy and a compatible application image.
2. Restore into a separate database and empty file volumes; keep the clone private and
   disable outbound jobs/integrations. Verify ownership, identifiers, meaningful row values,
   file checksums and decryption continuity before attempting a repair.
3. Review the recorded history against the image's migration files. A newer database may
   need its newer image, not replay. Compare the clone's missing schema and expected changes;
   review destructive SQL and data transformations explicitly. If the path is unsupported,
   keep the deployment stopped and obtain a reviewed migration instead.
4. Only when baseline replay is appropriate, rehearse this **on the restored clone**:

   ```bash
   python -m src.database.migrate --repair-after-verified-backup
   ```

5. Verify domain-level records, ownership, relationships and file checksums after repair,
   then start the compatible image privately and exercise ordinary user endpoints. Only
   repeat the reviewed procedure on the stopped deployment after that rehearsal succeeds.

The repair flag is a one-command operator acknowledgement that the history review and
clone rehearsal have happened. It does not itself create a backup; required mode also
enforces the verified pre-upgrade recovery point described above before any replay.
There is no persistent environment switch or request/API parameter enabling automatic
repair. Scheduled restore drills remain separate operational work.

Explicit replay puts Alembic stamping, replay and the final schema-presence check on the
same PostgreSQL connection and transaction. Migration exceptions or failed final validation
roll back that transaction, including the old revision record. No implicit restore or
downgrade runs. Current migrations do not use autocommit blocks; future nontransactional SQL,
external file changes and provider calls cannot be rolled back by this mechanism and need
separate recovery/review. A successful table/column check does not prove preservation of
values, types, constraints, files or application semantics.

An older recognized revision can legitimately lack pending columns, so its ordinary upgrade
runs before the final check. If that check fails, startup stays unavailable and the runner
does not replay earlier migrations. At the current head, missing schema is refused before
calling Alembic. Historical compatibility coverage is being expanded separately in #298.

## Verification

`tests/test_migration_safety.py` uses random isolated PostgreSQL schemas. It covers orphaned
and missing revisions without mutation, inconsistent revisions, known head drift, recognized
upgrades and merge parents, empty setup, explicit replay, rollback of deliberately deleted
records/new DDL, read-only inspection, lock contention/retry and two real startup processes.
`tests/test_migrations.py` retains the existing replay and data-preservation coverage. The
history-focused safety suite explicitly selects development mode; required-mode integration
coverage lives in `tests/test_migration_backup_runtime.py`, with isolated PostgreSQL databases,
invalid/missing/stale evidence, explicit repair, unchanged starts, rollback and concurrent
processes. `tests/test_migration_backup_gate.py` covers the private document contract.

`tools/check_migration_backups.py --app-image APP --runtime-image RUNTIME --sftp-image SFTP`
reuses the disposable deployment recovery harness. It builds a private image-only successor
revision, seeds two ordinary accounts and files, proves missing evidence blocks DDL, runs
real SFTP Restic preparation and offline SQL restoration, applies the successor exactly once,
starts without evidence at the unchanged head, and rechecks ordinary APIs, ownership, uploads,
notes, saves and preferences. It leaves the production migration graph unchanged. Historical
release upgrade compatibility is broader than this controlled successor test and remains
separate coverage.

Run against a dedicated development database:

```bash
python -m pytest tests/test_migration_backup_gate.py tests/test_migration_backup_runtime.py tests/test_migration_safety.py tests/test_migrations.py -q
```

Never run schema-mutating tests or repair rehearsals against the only copy of a deployment.
