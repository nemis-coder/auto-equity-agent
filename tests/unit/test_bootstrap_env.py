import stat

from scripts.bootstrap_env import PLACEHOLDER, bootstrap, render


def test_render_replaces_every_placeholder_with_distinct_secrets():
    out = render(f"A={PLACEHOLDER}\nB={PLACEHOLDER}\nC=fixed\n")
    values = dict(line.split("=", 1) for line in out.strip().splitlines())
    assert PLACEHOLDER not in out
    assert values["C"] == "fixed"
    assert values["A"] != values["B"]
    assert len(values["A"]) >= 32


def test_bootstrap_creates_private_file_and_never_overwrites(tmp_path):
    example = tmp_path / ".env.example"
    target = tmp_path / ".env"
    example.write_text(f"SECRET={PLACEHOLDER}\n")

    assert bootstrap(example, target) is True
    first = target.read_text()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600

    assert bootstrap(example, target) is False
    assert target.read_text() == first


def test_repository_env_example_defaults_to_evaluator_profile():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / ".env.example").read_text()
    assert "LANGFUSE_ENABLED=false" in text
    assert "CHAT_PROVIDER=" in text  # la conversación exige un proveedor real
    assert "EXTRACTION_PROVIDER=fake" in text
