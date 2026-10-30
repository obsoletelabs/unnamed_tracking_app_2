# Disaster recovery runbook and restore drill

A backup is only reliable once a restore has been exercised. This page has two parts: the
automated drill that proves it on every week of synthetic data, and the manual runbook an
operator follows after losing a deployment. Backups themselves are described in
[Full deployment backups](backups.md); upgrade gating in [Migration recovery](migration-recovery.md).

## The automated restore drill

`tests/test_restore_drill.py` and the scheduled **Restore drill** workflow
(`.github/workflows/restore-drill.yml`, Tuesdays 04:41 UTC, on demand and on pull requests that
touch the drill) run the whole path with synthetic data only:

1. Build a populated database at an older released revision (the same synthetic rows as the
   [historical upgrade matrix](migration-recovery.md#verification)), app files, an encrypted
   canary value and an encryption key.
2. Back it up with the application's own startup backup (Restic repository, `pg_dump`).
3. Check the repository (`restic check --read-data`), restore it into an empty directory and
   validate the bundle (manifest, recorded database checksum, key file).
4. Restore the dump into a **new, empty database**. In CI this is a second PostgreSQL server;
   locally set `RESTORE_DRILL_TARGET_URL` to use one, otherwise a new database on the test server
   is created and dropped. The configured database is refused as a target, and so is any
   database that already has tables.
5. Check the restored copy equals the backed-up state (rows, owners, values, foreign keys),
   that every media row still has its file, and that the restored key decrypts the canary.
6. Start the real migration command, end its database session while a table lock holds it
   partway through, then retry. The retried upgrade must reach head and keep every row, owner,
   value, foreign key and file checksum the upgrade matrix checks.

Damaged backups are also exercised: truncated and bit-flipped packs, a deleted pack, a missing
snapshot index, a missing key file and a wrong repository password must all be refused before
anything is restored, and an incomplete or altered bundle is reported with a short reason.

The drill never reads production data or secrets and never connects to a deployment. Its report
(`restore-drill.md` and `restore-drill.json`, from `RESTORE_DRILL_REPORT_DIR`) lists each step,
its duration and a failure reason that names tables, steps and counts only; the configured key
is removed from the report and masked in the CI log. The workflow publishes the report in the
run summary and as an artifact, and a failed **scheduled** run opens (or comments on) an issue
titled "Scheduled restore drill is failing". The time of the last good drill is the newest
successful run of the workflow. The report records the time spent restoring and upgrading as a
recovery-time measurement and the age of the backup when the drill began as a recovery-point
measurement.

What it does not cover: the full container topology. That is
`tools/check_deployment_backups.py` (also now scheduled weekly in the *Deployment backup
recovery* workflow), which restores a populated deployment from an SFTP Restic repository into
fresh volumes and checks the running application.

Run it yourself against a disposable PostgreSQL (needs `restic` and a PostgreSQL client at least
as new as the server):

```bash
cd src/backend
TEST_DATABASE_URL=postgresql+psycopg://test:test@localhost:5432/unnamed_tracking_test \
  python -m pytest tests/test_restore_drill.py -q
```

## Choosing matching versions

* **Application image**: the image that wrote the backup, or the oldest one still able to read
  its revision. The `manifest.json` in the bundle records the source revision; the database
  `alembic_version` table holds it as well. Restore first with that image, then upgrade.
* **PostgreSQL**: the same major version as the source server (18 for current releases). A
  restore target must not be older; `pg_restore` must be at least as new as the dump's server.
* **Encryption key**: the `SECRET_KEY` in `recovery-keys.json`. Starting with another key makes
  stored credentials unreadable and the app refuses to generate a replacement over them.

## Manual runbook after losing a deployment

Work on a separate host or in a private network, and never restore over a running deployment.

1. **Stop and preserve.** Keep the damaged deployment stopped. Do not delete its volumes. Keep
   the failing drill report or startup `attempt-*.json` report.
2. **Gather credentials.** Retrieve the Restic repository location and password from separate
   protected storage (an app-local repository's password is *not* inside it) and the matching
   images.
3. **Check the repository.** `restic check --read-data` must pass. A truncated, corrupt or
   incomplete repository, or a wrong password, stops here; try the next older snapshot or the
   off-host copy rather than forcing it.
4. **Restore into an empty directory** and read `manifest.json`: confirm source revision,
   `database_sha256` (compare with `sha256sum database.dump`) and that `recovery-keys.json` holds
   the key.
5. **Create a new PostgreSQL instance** at the right version and an empty database, then
   `pg_restore --no-owner --exit-on-error --dbname NEW_DB database.dump`.
6. **Restore the files** into new, empty volumes (the restored `APP_DATA_DIR`; for full
   deployments see [Restore into a disposable deployment](backups.md#restore-into-a-disposable-deployment))
   and set `SECRET_KEY` from the bundle.
7. **Verify before starting.** Row counts and owners per table, no orphaned foreign keys, and
   every media file present at `<user>/games/<folder>/<screenshots|clips|soundtrack>/<file>`.
   `python -m src.database.migrate --plan` must report a recognised revision.
8. **Start the matching image privately** with outbound jobs and integrations disabled, check
   the health check, sign in with a test account, and open a game with media.
9. **Upgrade only on the restored copy first.** If the upgrade is interrupted, repeat the
   command; it resumes from a consistent revision.
10. **Switch traffic** only after the checks pass, and record when it happened and how long
    the recovery took.

If a drill or restore fails, the report's *Detail* column names the first failing step. Common
causes are a wrong repository password, a key that does not match the data, a client older than
the server, and a restore target that is not empty.
