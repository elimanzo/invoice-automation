"""The command line, exactly as the brief specifies it."""

from __future__ import annotations

from pathlib import Path

import pytest

from invoice_automation.cli import main


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the CLI off the developer's real catalogue, and off any real API key."""
    monkeypatch.setenv("INVOICE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("XAI_API_KEY", raising=False)


def test_invoice_path_prints_the_extracted_invoice(
    invoices_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main([f"--invoice_path={invoices_dir / 'invoice_1001.txt'}"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "INV-1001" in output
    assert "Widgets Inc." in output
    assert "WidgetA" in output
    assert "WidgetB" in output
    assert "5000.00" in output


def test_a_missing_document_is_an_error_not_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main([f"--invoice_path={tmp_path / 'nope.txt'}"])

    assert exit_code == 1
    assert "nope.txt" in capsys.readouterr().err


def test_an_unreadable_format_says_so_plainly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "invoice.docx"
    path.write_text("not an invoice", encoding="utf-8")

    exit_code = main([f"--invoice_path={path}"])

    assert exit_code == 1
    assert "unsupported format" in capsys.readouterr().err


def test_seed_command_reports_where_the_catalogue_lives(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["--seed-catalogue"])

    assert exit_code == 0
    assert "catalogue.db" in capsys.readouterr().out


def test_no_arguments_shows_help_rather_than_failing_obscurely(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main([])

    assert exit_code == 2
    assert "--invoice_path" in capsys.readouterr().out
