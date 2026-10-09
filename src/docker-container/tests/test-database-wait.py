"""Exercise the entrypoint's actual wait with a simulated clock and failing probes."""

import os
import subprocess
import tempfile
import time
from pathlib import Path


def main():
    entrypoint = Path("/app/entrypoint.sh").read_text(encoding="utf-8")
    wait = entrypoint.split('log "Waiting for PostgreSQL"', 1)[1].split(
        'log "PostgreSQL is ready"', 1
    )[0]
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        clock = root / "clock"
        clock.write_text("0", encoding="utf-8")
        for name, script in {
            "date": '#!/bin/sh\ncat "$TEST_CLOCK"\n',
            "sleep": '#!/bin/sh\nn=$(cat "$TEST_CLOCK"); echo $((n + $1)) > "$TEST_CLOCK"\n',
            "pg_isready": '#!/bin/sh\nn=$(cat "$TEST_CLOCK"); echo $((n + 5)) > "$TEST_CLOCK"; exit 1\n',
        }.items():
            command = root / name
            command.write_text(script, encoding="utf-8")
            command.chmod(0o755)
        harness = root / "wait.sh"
        harness.write_text(
            '#!/bin/sh\nset -eu\nlog() { :; }\nwrite_status() { :; }\n'
            'fail_startup() { printf "%s\\n" "$1"; exit 1; }\n'
            'DB_HEALTH_MODE="$TEST_MODE"; DB_HEALTH_HOST=absent; DB_HEALTH_PORT=5432\n'
            'DB_HEALTH_USER=test; DB_HEALTH_DB=test; DB_HEALTH_URL=postgresql://absent/test\n'
            + wait,
            encoding="utf-8",
        )
        environment = {**os.environ, "PATH": f"{root}:{os.environ['PATH']}", "TEST_CLOCK": str(clock)}
        for mode in ("components", "url"):
            clock.write_text("0", encoding="utf-8")
            result = subprocess.run(
                ["sh", str(harness)], env={**environment, "TEST_MODE": mode},
                capture_output=True, text=True, timeout=10,
            )
            assert result.returncode == 1, result.stderr
            assert result.stdout.strip() == "DATABASE_FAILED", result.stdout
            assert 120 <= int(clock.read_text()) < 130, "Slow probes must count toward the 120s deadline"

        # A stuck DNS/provider probe must be terminated independently of pg_isready's own timeout.
        (root / "pg_isready").write_text(
            '#!/bin/sh\necho 120 > "$TEST_CLOCK"\nexec /bin/sleep 30\n', encoding="utf-8"
        )
        clock.write_text("0", encoding="utf-8")
        started = time.monotonic()
        result = subprocess.run(
            ["sh", str(harness)], env={**environment, "TEST_MODE": "components"},
            capture_output=True, text=True, timeout=6,
        )
        assert result.returncode == 1, result.stderr
        assert result.stdout.strip() == "DATABASE_FAILED", result.stdout
        assert time.monotonic() - started < 5, "The stuck probe must not hold startup indefinitely"
        (root / "pg_isready").write_text('#!/bin/sh\nexit 0\n', encoding="utf-8")
        clock.write_text("0", encoding="utf-8")
        result = subprocess.run(
            ["sh", str(harness)], env={**environment, "TEST_MODE": "components"},
            capture_output=True, text=True, timeout=6,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""
    print("Database wait: elapsed deadline, stuck probes and immediate readiness passed")


if __name__ == "__main__":
    main()
