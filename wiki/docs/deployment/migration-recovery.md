# Migration inspection and recovery

Startup upgrades fresh databases and recognized revisions under the existing PostgreSQL
advisory lock. Unknown revisions, populated databases without a revision, inconsistent
ancestor/descendant revision records and missing required tables/columns stop startup.
The API remains unavailable. This replaces automatic adoption and automatic history replay
in both development and production. Existing reviewed merge points in the published history
remain supported; independent recorded parents can reach their recognized merge normally.

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

The repair flag is a one-command operator acknowledgement that backup verification and
the rehearsal have already happened. **It does not create, inspect or certify a backup.**
There is no persistent environment switch or request/API parameter enabling automatic
repair. Automated pre-upgrade backup verification belongs to #297; full deployment backups
and scheduled restore drills are separate #296/#300 work.

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
`tests/test_migrations.py` retains the existing replay and data-preservation coverage.

Run against a dedicated development database:

```bash
python -m pytest tests/test_migration_safety.py tests/test_migrations.py -q
```

Never run schema-mutating tests or repair rehearsals against the only copy of a deployment.
