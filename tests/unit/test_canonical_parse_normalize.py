from app.ingest.canonical import canonicalize_text
from app.ingest.normalize import normalize_number
from app.ingest.parse import parse_markdown_tables
from contracts.models import Unit


def test_canonical_spans_preserve_lines_and_normalize_crlf():
    document = canonicalize_text(b"first\r\nsecond\rthird")
    start, end, exact = document.span_for_lines(2, 3)
    assert document.text == "first\nsecond\nthird"
    assert (start, end, exact) == (6, 18, "second\nthird")


def test_markdown_table_and_value_normalizer_are_conservative():
    document = canonicalize_text(b"| Name | Revenue (USD) | Margin |\n| --- | --- | --- |\n| Acme | US$5.2M | 58% |\n")
    table = parse_markdown_tables(document)[0]
    assert table.headers == ("Name", "Revenue (USD)", "Margin")
    assert table.rows == (("Acme", "US$5.2M", "58%"),)
    money = normalize_number("US$5.2M", "Revenue (USD)")
    assert money and (money.value, money.currency, money.unit, money.scale) == (5_200_000, "USD", Unit.money, 1e6)
    assert normalize_number("approximately five") is None
