"""Citation verifier: a quote is displayed only if it is verbatim in the immutable snapshot."""

from __future__ import annotations

from uuid import UUID

from contracts.models import SourceSpan
from app.reasoning.context import EvidenceSource


def verify_span(repo: EvidenceSource, dataset_version_id: UUID, span: SourceSpan) -> tuple[bool, str]:
    """True when span.exact_text sits at span.char_start..char_end of the cited document, whose hash matches."""
    try:
        whole = repo.read_lines(dataset_version_id, span.document_name, span.line_start, span.line_end)
    except (LookupError, ValueError) as e:
        return False, f"source lines unavailable: {e}"
    except Exception as e:  # noqa: BLE001 - e.g. NoResultFound from the database
        return False, f"source lines unavailable: {type(e).__name__}"
    if whole.document_hash != span.document_hash:
        return False, "document hash differs from the cited snapshot"
    rel = span.char_start - whole.char_start
    if rel < 0 or span.char_end > whole.char_end:
        return False, "offsets fall outside the cited lines"
    if whole.exact_text[rel: rel + len(span.exact_text)] != span.exact_text:
        return False, "quote is not verbatim at the cited offsets"
    return True, "ok"
