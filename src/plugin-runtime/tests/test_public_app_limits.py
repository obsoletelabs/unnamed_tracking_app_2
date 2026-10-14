"""Large library work uses a separate bound from ordinary plugin actions."""

import json
import sys

import pytest
from runtime import PluginSpec, PluginSupervisor, RuntimePolicyError


@pytest.fixture
def action(tmp_path, monkeypatch):
    monkeypatch.setenv("NONBUBBLE_ENV", "true")
    supervisor = PluginSupervisor(tmp_path / "workers", tmp_path / "storage")
    package = tmp_path / "package"
    package.mkdir()
    statement = """
import json, sys
request = json.loads(sys.stdin.readline())
print(json.dumps({'plugin_action_result': {'data': 'x' * request['size']}}), flush=True)
"""
    return supervisor, package, PluginSpec("bounded.public-app", (sys.executable, "-c", statement))


@pytest.mark.parametrize("public", [False, True])
def test_input_bound_is_specific_to_public_work(action, public):
    supervisor, package, spec = action
    payload = json.dumps({"size": 0, "library": "x" * (70 * 1024)}).encode()
    if public:
        assert json.loads(supervisor.execute(spec, package, payload, public_app_work=True)) == {
            "data": ""
        }
    else:
        with pytest.raises(RuntimePolicyError, match="payload exceeds 64 KiB"):
            supervisor.execute(spec, package, payload)
    with pytest.raises(RuntimePolicyError, match="work exceeds 4 MiB"):
        supervisor.execute(spec, package, b"x" * (4 * 1024 * 1024 + 1), public_app_work=True)


@pytest.mark.parametrize(
    "public,size,allowed",
    [
        (False, 70 * 1024, False),
        (True, 70 * 1024, True),
        (True, 4 * 1024 * 1024, False),
    ],
)
def test_result_bound_is_specific_to_public_work(action, public, size, allowed):
    supervisor, package, spec = action
    payload = json.dumps({"size": size}).encode()
    if allowed:
        assert (
            len(
                json.loads(supervisor.execute(spec, package, payload, public_app_work=public))[
                    "data"
                ]
            )
            == size
        )
    else:
        with pytest.raises(RuntimePolicyError, match="output exceeds"):
            supervisor.execute(spec, package, payload, public_app_work=public)
