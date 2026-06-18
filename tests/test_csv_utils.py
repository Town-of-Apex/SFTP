"""Tests for CSV normalization."""

from src.csv_utils import normalize_csv_fieldnames, normalize_csv_row


def test_normalize_csv_fieldnames_drops_blank_and_none():
    assert normalize_csv_fieldnames(["External ID", None, "", "  ", "Phone 1"]) == [
        "External ID",
        "Phone 1",
    ]


def test_normalize_csv_row_coerces_none_and_drops_orphan_keys():
    row = {
        "External ID": "1",
        "First Name": None,
        "Phone 1": "9195550100",
        None: "junk",
        "Extra": "ignored",
    }
    headers = ["External ID", "First Name", "Phone 1"]
    assert normalize_csv_row(row, headers) == {
        "External ID": "1",
        "First Name": "",
        "Phone 1": "9195550100",
    }
