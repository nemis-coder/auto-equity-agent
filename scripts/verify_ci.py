"""Run offline checks against an already started, disposable Compose stack.

Intentionally refuses the normal demo project. Does not create/delete volumes,
read credentials, call a real model, or manage the user's running services.
"""

from __future__ import annotations

import json
import os
import re
import subprocess


def main() -> None:
    project = os.environ.get("COMPOSE_PROJECT_NAME", "")
    if not re.fullmatch(r"auto-equity-ci-[a-z0-9][a-z0-9-]*", project):
        raise SystemExit("Set COMPOSE_PROJECT_NAME=auto-equity-ci-<unique-run> first.")
    compose = ["docker", "compose", "-p", project]
    # Inspect privately; resolved configuration contains freshly generated credentials.
    config = json.loads(subprocess.check_output(  # noqa: S603 - fixed CLI, validated project
        [*compose, "config", "--format", "json"]
    ))
    services = config["services"]
    for service in ("api", "agent"):
        env = services[service]["environment"]
        if any(env.get(k) for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")):
            raise SystemExit("CI must not receive model credentials.")
        if str(env.get("LANGFUSE_ENABLED")).lower() != "false":
            raise SystemExit("CI requires disabled external telemetry.")
    if services["api"]["environment"].get("EXTRACTION_PROVIDER") != "fake":
        raise SystemExit("CI requires fake document extraction.")
    for service in ("api", "agent", "chat-ui"):
        if services[service].get("ports"):
            raise SystemExit("CI must not publish ports; load docker-compose.ci.yml.")

    def run(service: str, *command: str) -> None:
        subprocess.run(  # noqa: S603 - fixed commands below, no shell
            [*compose, "exec", "-T", service, *command], check=True
        )

    run("api", "ruff", "check", "app", "tests", "evals", "scripts", "conversation/server/graph.py")
    run("api", "python", "-m", "pytest", "tests", "-q")
    run("agent", "python", "-m", "unittest", "-q", "test_graph")
    run("chat-ui", "node", "--test", "src/components/thread/customer-view.test.mjs")
    run("api", "python", "scripts/demo.py", "--scenario", "all")
    subprocess.run(  # noqa: S603 - fixed CLI; revision is a single environment argument
        [*compose, "exec", "-T", "-e", f"EVAL_COMMIT={os.environ.get('EVAL_COMMIT', 'local')}",
         "api", "python", "-m", "scripts.run_evals", "deterministic", "--out", "/data/ci-eval"],
        check=True,
    )
    print("Offline checks passed. Real model quality and browser journeys are separate gates.")


if __name__ == "__main__":
    main()
