"""Regressions found reviewing tickets 03-05.

Each is a way the system could fail loudly in the wrong way (a raw traceback) or
misclassify content that merely resembles a format without being one.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest
from openai import APIConnectionError

from invoice_automation.config import Settings
from invoice_automation.documents import UnreadableDocument, load_document
from invoice_automation.models import DocumentFormat
from invoice_automation.providers import GrokProvider, ProviderUnavailable, StructuredCall


class _FailingClient:
    """Raises whatever the test wants from `.chat.completions.create(...)`."""

    def __init__(self, exc: Exception | None = None, response: Any = None) -> None:
        self._exc = exc
        self._response = response

    class _Chat:
        def __init__(self, outer: "_FailingClient") -> None:
            self._outer = outer

        class _Completions:
            def __init__(self, outer: "_FailingClient") -> None:
                self._outer = outer

            def create(self, **kwargs: Any) -> Any:
                if self._outer._exc is not None:
                    raise self._outer._exc
                return self._outer._response

        @property
        def completions(self) -> "_FailingClient._Chat._Completions":
            return _FailingClient._Chat._Completions(self._outer)

    @property
    def chat(self) -> "_FailingClient._Chat":
        return _FailingClient._Chat(self)


def _call() -> StructuredCall:
    return StructuredCall(system="s", user="u", schema={"type": "object"}, document_id="x.txt")


class TestProviderTransportFailures:
    def test_an_openai_sdk_error_becomes_provider_unavailable(self) -> None:
        underlying = APIConnectionError(
            request=httpx2.Request("POST", "https://api.x.ai/v1/chat/completions")
        )
        client = _FailingClient(exc=underlying)
        provider = GrokProvider(api_key="k", base_url="https://api.x.ai/v1", model="grok-4", client=client)  # type: ignore[arg-type]

        with pytest.raises(ProviderUnavailable):
            provider.structured(_call())

    def test_an_empty_choices_list_becomes_provider_unavailable(self) -> None:
        client = _FailingClient(response=SimpleNamespace(choices=[]))
        provider = GrokProvider(api_key="k", base_url="https://api.x.ai/v1", model="grok-4", client=client)  # type: ignore[arg-type]

        with pytest.raises(ProviderUnavailable):
            provider.structured(_call())

    def test_the_cli_reports_a_transport_failure_cleanly(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The exact failure mode hit live this session: a real key with no credits."""
        import invoice_automation.deps as deps_module
        from invoice_automation.cli import main

        monkeypatch.setenv("XAI_API_KEY", "sk-real-looking-but-unauthorized")
        monkeypatch.setenv("INVOICE_DATA_DIR", str(tmp_path / "data"))

        def _raising_grok(*args: Any, **kwargs: Any) -> Any:
            class _Raises:
                def structured(self, call: StructuredCall) -> dict[str, Any]:
                    raise ProviderUnavailable(f"Grok request failed for {call.document_id!r}: 403")

            return _Raises()

        monkeypatch.setattr(deps_module, "GrokProvider", _raising_grok)

        exit_code = main(["--invoice_path=data/invoices/invoice_1001.txt"])

        assert exit_code == 1
        assert "403" in capsys.readouterr().err


class TestCorruptPdf:
    def test_a_corrupt_pdf_is_refused_not_a_traceback(self, tmp_path: Path) -> None:
        path = tmp_path / "invoice_corrupt.pdf"
        # Valid PDF magic bytes, garbage after it: pdfminer will fail deep inside,
        # not with a clean "no text" result.
        path.write_bytes(b"%PDF-1.4\nthis is not a real pdf structure at all\n%%EOF")

        with pytest.raises(UnreadableDocument):
            load_document(path)


class TestXmlSniffingIsNotJustALeadingBracket:
    def test_prose_starting_with_a_bracketed_token_is_not_misdetected_as_xml(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "invoice_email.txt"
        path.write_text(
            "<CONFIDENTIAL> Please process the attached invoice at your convenience.",
            encoding="utf-8",
        )

        document = load_document(path)

        assert document.format is DocumentFormat.TEXT

    def test_real_xml_is_still_detected(self, tmp_path: Path) -> None:
        path = tmp_path / "invoice.dat"
        path.write_text('<?xml version="1.0"?><invoice></invoice>', encoding="utf-8")

        document = load_document(path)

        assert document.format is DocumentFormat.XML

    def test_prose_starting_with_a_json_like_brace_is_not_misdetected_as_json(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "invoice_email.txt"
        path.write_text("{almost a JSON object but not quite}", encoding="utf-8")

        document = load_document(path)

        assert document.format is DocumentFormat.TEXT


def test_settings_no_longer_carries_a_dead_extraction_attempts_field() -> None:
    assert not hasattr(Settings(), "extraction_max_attempts")
