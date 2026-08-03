"""Tests for analytics metadata helpers."""

from src.metadata import everbridge_headers, parse_opted_in


def test_parse_opted_in_legacy_without_column():
    assert parse_opted_in({}, column_present=False) is True


def test_parse_opted_in_true_false():
    assert parse_opted_in({"Opted In": "TRUE"}, column_present=True) is True
    assert parse_opted_in({"Opted In": "false"}, column_present=True) is False
    assert parse_opted_in({"Opted In": "maybe"}, column_present=True) is None


def test_everbridge_headers_stops_at_end_and_drops_metadata():
    headers = [
        "First Name",
        "External ID",
        "END",
        "Opted In",
        "Submitter Email",
        "Submitter Department",
        "Submission Datetime",
    ]
    assert everbridge_headers(headers) == ["First Name", "External ID", "END"]
