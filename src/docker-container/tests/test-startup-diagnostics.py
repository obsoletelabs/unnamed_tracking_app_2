"""#263: the entrypoint's failure messages name the component and a safe recovery, and never break the status JSON."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

ENTRYPOINT = Path(os.environ.get("ENTRYPOINT_PATH", "/app/entrypoint.sh"))


def blocks():
    source = ENTRYPOINT.read_text(encoding="utf-8")
    safe_text = "safe_text() {" + source.split("safe_text() {", 1)[1].split("\n}\n", 1)[0] + "\n}\n"
    storage = source.split('log "Checking persistent storage"', 1)[1].split('log "Waiting for PostgreSQL"', 1)[0]
    migration = source.split('if [ "$MIGRATION_RESULT" -ne 0 ]; then', 1)[1].split("\nfi\n", 1)[0]
    database = source.split("database_deadline=", 1)[1].split('log "PostgreSQL is ready"', 1)[0]
    return safe_text, storage, migration, database


PRELUDE = (
    "#!/bin/sh\nset -eu\nlog() { :; }\n"
    'fail_startup() { printf "%s|%s\\n" "$1" "$2"; exit 1; }\n'
)


def run(script: str, env=None):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "t.sh"
        path.write_text(script, encoding="utf-8")
        return subprocess.run(
            ["sh", str(path)], capture_output=True, text=True, timeout=10, env={**os.environ, **(env or {})}
        )


def status_json(message: str) -> dict:
    """The exact way write_status embeds a message."""
    return json.loads('{"message":"%s"}' % message)


def main():
    safe_text, storage, migration, database = blocks()

    # --- unwritable persistent storage is named before anything else fails -------------------------------------
    with tempfile.TemporaryDirectory() as directory:
        good = run(PRELUDE + safe_text + storage, {"DATA_DIR": directory})
        assert good.returncode == 0 and good.stdout == "", good
        assert not list(Path(directory).glob(".write-check.*")), "the probe file must be removed"
        blocker = Path(directory) / "a-file"
        blocker.write_text("not a directory", encoding="utf-8")
        bad = run(PRELUDE + safe_text + storage, {"DATA_DIR": str(blocker / "data")})
        assert bad.returncode == 1, bad
        phase, message = bad.stdout.strip().split("|", 1)
        assert phase == "CONFIGURATION_FAILED"
        assert "is not writable" in message and "restart" in message and "untouched" in message
        status_json(message)  # must stay valid JSON

    # --- the database wait names the host and says it is usually temporary ----------------------------------
    stubs = (
        'date() { echo 1000; }\nsleep() { :; }\ntimeout() { shift; "$@"; }\npg_isready() { return 1; }\n'
        "attempt=1\n"
    )
    for mode, expect in (("components", "db.internal:5432"), ("url", "DATABASE_URL")):
        script = (
            PRELUDE + safe_text + stubs + f'DB_HEALTH_MODE={mode}\nDB_HEALTH_HOST="db.internal"\n'
            'DB_HEALTH_PORT=5432\nDB_HEALTH_USER=u\nDB_HEALTH_DB=d\nDB_HEALTH_URL=postgresql://u:SECRET@h/d\n'
            "database_deadline=0\n" + 'while :; do\n  if [ "$(date +%s)" -ge "$database_deadline" ]; then\n'
            + database.split('while :; do\n  if [ "$(date +%s)" -ge "$database_deadline" ]; then\n', 1)[1].split(
                '  fi\n  if [ "${DB_HEALTH_MODE', 1
            )[0]
            + "  fi\ndone\n"
        )
        result = run(script)
        assert result.returncode == 1, result
        phase, message = result.stdout.strip().split("|", 1)
        assert phase == "DATABASE_FAILED" and expect in message and "temporary" in message
        assert "SECRET" not in message and "postgresql://" not in message  # never echoes the connection string
        status_json(message)

    # --- a failed migration quotes its own reason, safely -----------------------------------------------------
    with tempfile.TemporaryDirectory() as directory:
        log = Path(directory) / "migration.log"
        log.write_text(
            'Waiting for the database\nsqlalchemy.exc.ProgrammingError: relation "games" has a \\ weird "value"\n\n',
            encoding="utf-8",
        )
        result = run(
            PRELUDE + safe_text + f'MIGRATION_LOG="{log}"\nDETAILS_FILE=/dev/null\n' + migration,
        )
        assert result.returncode == 1, result
        phase, message = result.stdout.strip().split("|", 1)
        assert phase == "MIGRATION_FAILED"
        assert "ProgrammingError" in message and "relation games" in message
        assert "restore your last backup" in message and "was not deleted" in message
        status_json(message)  # quotes and backslashes in the reason cannot break the status file
        log.write_text("", encoding="utf-8")
        empty = run(PRELUDE + safe_text + f'MIGRATION_LOG="{log}"\nDETAILS_FILE=/dev/null\n' + migration)
        assert "Database migrations failed." in empty.stdout and "failed:" not in empty.stdout
    print("Startup diagnostics: storage, database wait and migration messages passed")


if __name__ == "__main__":
    main()
