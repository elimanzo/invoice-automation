"""Which provider build_deps and the CLI choose, and how that fails cleanly."""

from __future__ import annotations

from pathlib import Path

import pytest

from invoice_automation.config import MissingApiKey, Settings
from invoice_automation.deps import build_deps
from invoice_automation.providers import FakeProvider, GrokProvider


def test_no_key_and_no_override_uses_the_fake_provider(tmp_path: Path) -> None:
    settings = Settings(api_key=None, data_dir=str(tmp_path))

    deps = build_deps(settings)

    assert isinstance(deps.provider, FakeProvider)


def test_a_key_present_and_no_override_uses_grok(tmp_path: Path) -> None:
    settings = Settings(api_key="sk-test", data_dir=str(tmp_path))

    deps = build_deps(settings)

    assert isinstance(deps.provider, GrokProvider)


def test_explicit_fake_override_wins_even_with_a_key(tmp_path: Path) -> None:
    settings = Settings(api_key="sk-test", data_dir=str(tmp_path))

    deps = build_deps(settings, provider="fake")

    assert isinstance(deps.provider, FakeProvider)


def test_explicit_grok_with_no_key_fails_naming_the_env_var(tmp_path: Path) -> None:
    settings = Settings(api_key=None, data_dir=str(tmp_path))

    with pytest.raises(MissingApiKey) as excinfo:
        build_deps(settings, provider="grok")

    assert "XAI_API_KEY" in str(excinfo.value)


def test_cli_reports_the_missing_key_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from invoice_automation.cli import main

    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setenv("INVOICE_DATA_DIR", str(tmp_path / "data"))

    exit_code = main(["--provider=grok", "--invoice_path=data/invoices/invoice_1001.txt"])

    assert exit_code == 1
    assert "XAI_API_KEY" in capsys.readouterr().err
