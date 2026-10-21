"""Exercise actual dashboard queries against deterministic Prometheus samples.

Run with Python 3 and Docker, or pass --promtool /path/to/promtool.
No application, production requests, or additional Python dependencies are needed.
"""

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

EXAMPLE = Path(__file__).resolve().parent


def build_tests(dashboard: dict) -> dict:
    """Interpolate the shipped queries, retaining the same fixture labels as a real scrape."""
    panels = {panel["id"]: panel for panel in dashboard["panels"]}
    assert len(panels) == len(dashboard["panels"]), "Panel IDs must be unique"
    samples = []

    def series(name: str, labels: str, values: str) -> None:
        samples.append({"series": name + "{" + labels + "}", "values": values})

    app_a = 'job="unnamed-tracking",instance="app-a:9464"'
    app_b = 'job="unnamed-tracking",instance="app-b:9464"'
    other = 'job="another-service",instance="unrelated:9464"'
    for labels in [app_a, app_b]:
        series("up", labels, "1+0x10")
        series("uta_database_up", labels, "1+0x10")
    series("up", other, "0+0x10")
    series("uta_ready", app_a, "1+0x10")
    # app-b is scrapeable but has no readiness measurement: the aggregate is unknown.
    series("uta_plugin_runtime_up", app_a, "1+0x10")
    series("uta_plugin_runtime_up", app_b, "0+0x10")
    series(
        "uta_http_requests_total",
        app_a + ',route="/api/games",status_class="2xx"',
        "0+60x10",
    )
    series(
        "uta_http_requests_total",
        app_b + ',route="/api/games",status_class="2xx"',
        "0+90x10",
    )
    series(
        "uta_http_requests_total",
        other + ',route="/api/games",status_class="2xx"',
        "0+6000x10",
    )
    series(
        "uta_http_requests_total",
        app_a + ',route="/api/games",status_class="5xx"',
        "0+30x10",
    )
    series(
        "uta_http_requests_total",
        app_a + ',route="/api/auth/login",status_class="4xx"',
        "0+30x10",
    )
    series(
        "uta_http_rate_limited_responses_total",
        app_a + ',route="/api/auth/login"',
        "0+30x10",
    )
    for name, increment in [("cleanup", 30), ("backup", 60)]:
        series(
            "uta_job_runs_total",
            app_a + f',exported_job="{name}",outcome="success"',
            f"0+{increment}x10",
        )
    checks = []

    def check(panel_id: int, ref: str, expected: list, **overrides: str) -> None:
        target = next(
            target for target in panels[panel_id]["targets"] if target["refId"] == ref
        )
        variables = {
            "__rate_interval": "2m",
            "__range": "5m",
            "job": "unnamed-tracking",
            "instance": ".*",
            "route": ".*",
            **overrides,
        }
        expression = target["expr"]
        for key, value in variables.items():
            expression = expression.replace("$" + key, value)
        assert "$" not in expression, f"Unresolved dashboard variable: {expression}"
        checks.append(
            {
                "expr": expression,
                "eval_time": "5m",
                "exp_samples": [
                    {"labels": labels, "value": value} for labels, value in expected
                ],
            }
        )

    check(
        17, "A", [("{}", 1)]
    )  # An unrelated down service must not change app scrape health.
    check(
        18, "A", []
    )  # Missing readiness on one selected target must not produce green.
    check(18, "A", [("{}", 1)], instance="app-a:9464")
    check(19, "A", [("{}", 1)])
    check(20, "A", [("{}", 0)])
    check(20, "A", [("{}", 1)], instance="app-a:9464")
    check(
        1,
        "A",
        [
            ('{status_class="2xx"}', 5),
            ('{status_class="4xx"}', 1),
            ('{status_class="5xx"}', 1),
        ],
    )
    check(1, "A", [('{status_class="4xx"}', 1)], route="/api/auth/login")
    check(4, "A", [('{route="/api/games"}', 1)])
    check(4, "B", [('{route="/api/auth/login"}', 1)])
    check(
        12,
        "A",
        [
            ('{exported_job="backup",outcome="success"}', 600),
            ('{exported_job="cleanup",outcome="success"}', 300),
        ],
    )
    return {
        "rule_files": [],
        "tests": [
            {
                "name": "shipped dashboard queries",
                "interval": "30s",
                "input_series": samples,
                "promql_expr_test": checks,
            }
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--promtool", default=shutil.which("promtool"))
    args = parser.parse_args()
    dashboard = json.loads(
        (EXAMPLE / "grafana-dashboard.json").read_text(encoding="utf-8")
    )
    fixture = build_tests(dashboard)
    with tempfile.TemporaryDirectory(prefix="uta-dashboard-") as directory:
        path = Path(directory) / "dashboard.test.json"
        path.write_text(json.dumps(fixture), encoding="utf-8")
        Path(directory).chmod(0o755)
        path.chmod(0o644)
        if args.promtool:
            command = [args.promtool, "test", "rules", str(path)]
        else:
            command = [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{directory}:/work:ro",
                "-w",
                "/work",
                "--entrypoint",
                "promtool",
                "prom/prometheus:v2.55.1",
                "test",
                "rules",
                "dashboard.test.json",
            ]
        subprocess.run(command, check=True)
    print(
        f"Validated {len(fixture['tests'][0]['promql_expr_test'])} dashboard query cases"
    )


if __name__ == "__main__":
    main()
