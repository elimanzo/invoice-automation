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


def test_invoice_path_runs_the_pipeline_end_to_end(
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
    assert "Decision:  APPROVED" in output
    assert "Payment:   success" in output


def test_a_stock_aggregation_violation_is_reported_as_rejected_with_no_payment(
    invoices_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """INV-1013's WidgetA/WidgetB/GadgetX each exceed stock only once aggregated
    across their multiple lines — see test_graph.py for the full breakdown."""
    exit_code = main([f"--invoice_path={invoices_dir / 'invoice_1013.json'}"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Decision:  REJECTED" in output
    assert "Payment:   not made" in output


def test_a_run_leaves_a_checkpoint_trace_a_second_run_can_extend(
    invoices_dir: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two separate CLI invocations must not clash over the same checkpoint database."""
    path = invoices_dir / "invoice_1001.txt"

    first = main([f"--invoice_path={path}"])
    capsys.readouterr()
    second = main([f"--invoice_path={path}"])

    assert first == 0
    assert second == 0
    assert "APPROVED" in capsys.readouterr().out


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


def test_invoice_path_and_invoice_dir_together_is_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        [f"--invoice_path={tmp_path / 'x.txt'}", f"--invoice_dir={tmp_path}"]
    )

    assert exit_code == 2
    assert "mutually exclusive" in capsys.readouterr().err


def test_invoice_dir_processes_the_directory_and_reports_a_summary(
    invoices_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main([f"--invoice_dir={invoices_dir}"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "invoice_1001.txt" in output
    assert "Processed: " in output
    assert "approved:" in output


def test_a_nonexistent_invoice_dir_is_a_clean_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main([f"--invoice_dir={tmp_path / 'does-not-exist'}"])

    assert exit_code == 1
    assert "no such directory" in capsys.readouterr().err


def test_an_unconfigured_currency_is_a_clean_error_not_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Found by code-review on PR #3: UnknownCurrency propagated past every except
    clause in the single-invoice path and crashed the CLI with a raw traceback."""
    import invoice_automation.providers as providers_module

    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    document_path = tmp_path / "invoice_foreign.txt"
    document_path.write_text("irrelevant to the fake", encoding="utf-8")
    (responses_dir / "invoice_foreign.json").write_text(
        '{"vendor": {"name": "X"}, "currency": "XYZ", "total": "100.00", "line_items": []}',
        encoding="utf-8",
    )
    monkeypatch.setattr(providers_module, "SAMPLE_RESPONSES_DIR", responses_dir)

    exit_code = main([f"--invoice_path={document_path}"])

    assert exit_code == 1
    assert "XYZ" in capsys.readouterr().err
