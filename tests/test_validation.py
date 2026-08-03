"""Tests for CSV validation."""

from src.validation import partition_rows, validate_opt_out_row, validate_row


def test_valid_row_passes():
    row = {
        "External ID": "100",
        "First Name": "Ada",
        "Last Name": "Lovelace",
        "Phone 1": "919-555-0100",
        "Email Address 1": "",
    }
    assert validate_row(row) == []


def test_missing_external_id_fails():
    row = {
        "External ID": "",
        "First Name": "Ada",
        "Last Name": "Lovelace",
        "Phone 1": "9195550100",
    }
    issues = validate_row(row)
    assert any("External ID" in issue for issue in issues)


def test_missing_contact_method_fails():
    row = {
        "External ID": "100",
        "First Name": "Ada",
        "Last Name": "Lovelace",
        "Phone 1": "",
        "Email Address 1": "",
    }
    issues = validate_row(row)
    assert any("Phone 1" in issue or "Email" in issue for issue in issues)


def test_partition_rows_splits_valid_and_invalid():
    rows = [
        {
            "External ID": "1",
            "First Name": "Good",
            "Last Name": "User",
            "Phone 1": "9195550100",
        },
        {
            "External ID": "",
            "First Name": "Bad",
            "Last Name": "User",
            "Phone 1": "9195550100",
        },
    ]
    valid, invalid = partition_rows(rows)
    assert len(valid) == 1
    assert len(invalid) == 1


def test_opt_out_requires_only_external_id():
    assert validate_opt_out_row({"External ID": "100"}) == []
    assert validate_opt_out_row({"External ID": ""}) == ["Missing External ID"]


def test_partition_rows_supports_opt_out_validator():
    rows = [
        {"External ID": "1"},
        {"External ID": ""},
    ]
    valid, invalid = partition_rows(rows, validator=validate_opt_out_row)
    assert len(valid) == 1
    assert len(invalid) == 1
