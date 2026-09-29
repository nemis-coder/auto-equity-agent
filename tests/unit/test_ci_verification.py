"""Safety checks for the offline verification entrypoint; no Docker is started."""

import json

import pytest

from scripts import verify_ci


@pytest.fixture
def ci(monkeypatch):
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "auto-equity-ci-test")
    config = {"services": {
        "api": {"environment": {"LANGFUSE_ENABLED": "false", "EXTRACTION_PROVIDER": "fake"}},
        "agent": {"environment": {"LANGFUSE_ENABLED": "false"}},
        "chat-ui": {},
    }}
    commands = []
    monkeypatch.setattr(verify_ci.subprocess, "check_output", lambda _: json.dumps(config))
    monkeypatch.setattr(verify_ci.subprocess, "run", lambda args, **_: commands.append(args))
    return config, commands


@pytest.mark.parametrize("project", [
    "", "auto-equity", "auto-equity-ci-", "auto-equity-ci-../demo",
])
def test_refuses_normal_or_invalid_project(ci, monkeypatch, project):
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", project)
    with pytest.raises(SystemExit, match="unique-run"):
        verify_ci.main()
    assert not ci[1]


@pytest.mark.parametrize("service,key,value", [
    ("api", "OPENAI_API_KEY", "not-a-real-key"),
    ("agent", "ANTHROPIC_API_KEY", "not-a-real-key"),
    ("api", "LANGFUSE_ENABLED", "true"),
    ("agent", "LANGFUSE_ENABLED", "true"),
    ("api", "EXTRACTION_PROVIDER", "openai"),
])
def test_refuses_external_services(ci, service, key, value):
    ci[0]["services"][service]["environment"][key] = value
    with pytest.raises(SystemExit):
        verify_ci.main()
    assert not ci[1]


def test_refuses_published_ports(ci):
    ci[0]["services"]["chat-ui"]["ports"] = [{"published": "3000", "target": 3000}]
    with pytest.raises(SystemExit, match="publish ports"):
        verify_ci.main()
    assert not ci[1]


def test_runs_all_gates_without_paid_evaluation_or_volume_deletion(ci):
    verify_ci.main()
    commands = ci[1]
    assert len(commands) == 6
    assert all(command[:4] == ["docker", "compose", "-p", "auto-equity-ci-test"]
               for command in commands)
    assert all("real_ai" not in command and "down" not in command for command in commands)
    assert any("deterministic" in command for command in commands)
    assert any("scripts/demo.py" in command and "all" in command for command in commands)


def test_stops_at_first_failed_gate(ci, monkeypatch):
    def fail(args, **_):
        ci[1].append(args)
        raise verify_ci.subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(verify_ci.subprocess, "run", fail)
    with pytest.raises(verify_ci.subprocess.CalledProcessError):
        verify_ci.main()
    assert len(ci[1]) == 1
