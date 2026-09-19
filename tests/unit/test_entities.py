from app.ingest.canonical import canonicalize_text
from app.ingest.entities import build_catalog, heading_subjects
from app.ingest.parse import parse_markdown_tables
from app.markdown import Outline

DOC = """# Report
## 3. Snapshot
| Ticker | Company | Revenue |
|---|---|---|
| ACME | Acme Holdings Inc. | $5M |
| All 6 | Sector | $9M |
## 5. Deep Dives
### ACME — Acme Holdings Inc.
### BNS — Bank of Nova Scotia (Scotiabank)
## 6. Performance
### ACME — Acme Holdings Inc. — Quarterly Performance
| Quarter | Revenue |
|---|---|
| Q2 2026 (Jun 30) | $5M |
### BNS — Bank of Nova Scotia — Quarterly Performance
| Field | Value |
|---|---|
| Market Cap | $3B |
"""


def _catalog():
    c = canonicalize_text(DOC.encode())
    return build_catalog(Outline.parse(c.text), parse_markdown_tables(c))


def test_entities_come_from_heading_subjects_and_identifier_columns_only():
    cat = _catalog()
    assert cat.labels() == {"ACME", "BNS"}           # no 'Q2 2026 (Jun 30)', 'Market Cap', 'All 6'
    assert {"acme holdings inc.", "acme holdings"} <= cat.aliases["ACME"]
    assert {"bank of nova scotia", "scotiabank"} <= cat.aliases["BNS"]


def test_cells_resolve_to_their_entity():
    cat = _catalog()
    assert cat.owner_of("ACME ($5M)") == "ACME"
    assert cat.owner_of("**Scotiabank**") == "BNS"
    assert cat.owner_of("Q2 2026 (Jun 30)") is None


def test_heading_subject_needs_two_sections():
    o = Outline.parse("# T\n## A\n### ONE — Only once\n## B\n### TWO — Twice\n### TWO — Twice again\n")
    assert set(heading_subjects(o)) == {"TWO"}


def test_short_uppercase_labels_survive_the_group_filter():
    from app.ingest.entities import EntityCatalog
    cat = EntityCatalog()
    cat.add("NA", "National Bank of Canada")                 # a heading subject named NA
    cat.add("N/A", from_table=True)
    cat.add("All 6", from_table=True)
    assert cat.labels() == {"NA"}
