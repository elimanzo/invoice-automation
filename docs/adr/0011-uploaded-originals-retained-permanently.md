# ADR-0011: Uploaded invoice originals are retained permanently

- **Status:** Accepted
- **Date:** 2026-08-23

## Context

Ticket 20 adds a clerk-facing upload endpoint so a document can start a run from the
dashboard, not just a server-side path. Once an upload exists as bytes in a request rather
than a file already sitting on disk, there's a real choice: keep what the clerk submitted, or
discard it once `load_document`/`run_invoice` has extracted from it.

Every other judgment call in this system is auditable by design — corrections record raw
value, stored value, and reason; flags and decisions carry reasoning; nothing is a silent
guess (see `CONTEXT.md`'s **Correction** entry). Discarding the one artifact a human actually
submitted would be the single silent thing in an otherwise fully-auditable pipeline: if
extraction got something wrong, there would be no way to go back and check it against what
was actually sent.

## Decision

Uploaded files are written to `<INVOICE_DATA_DIR>/uploads/` and kept indefinitely, the same
way path-based documents already sit wherever the clerk's filesystem put them. Storage path
and `document_name` are kept distinct: every upload *request* gets its own fresh UUID
directory (`<INVOICE_DATA_DIR>/uploads/<uuid>/`), and every file inside it keeps its original
filename untouched — so two different clerks uploading two different files both called
`invoice.pdf` can never collide (they land in different request directories), while
`document_name` — what the graph, ledger, and every view key on — stays the plain original
filename, unchanged from how a path-based run already names itself. This also means an
upload request's directory can be handed directly to the existing `run_batch` unmodified,
the same call `directory_path` already makes.

## Consequences

**Good.** An uploaded document is inspectable after the fact exactly like a path-based one
always was; no new blind spot in an otherwise fully-audited system. The UUID/document_name
split costs one path-join and a few lines, and means the UI never shows an ugly generated
name.

**Bad.** Storage grows without bound — there's no retention window or cleanup job. Acceptable
here: sample invoices are kilobytes, and this is a local/demo system, not a production
ingestion pipeline processing real volume. Revisit if this were ever deployed somewhere
uploads could be adversarially large or high-frequency (`ADR` covering upload size limits and
extension-only validation already bounds the worst case per file to 10 MB).
