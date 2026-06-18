"""Tests for contact name formatting."""

from src.contacts import (
    build_sync_failure_contact_context,
    build_sync_success_contact_context,
    format_contact_list,
    format_contact_name,
)
from src.validation import ValidationIssue


def test_format_contact_name():
    assert format_contact_name({"First Name": "John", "Last Name": "Smith"}) == "John Smith"
    assert format_contact_name({"First Name": "", "Last Name": ""}) == "Unknown"


def test_format_contact_list_truncates():
    rows = [
        {"First Name": f"Person{i}", "Last Name": "Test", "External ID": str(i)}
        for i in range(25)
    ]
    result = format_contact_list(rows, limit=20)
    assert "... and 5 more" in result
    assert result.startswith("Person0 Test")


def test_format_contact_list_includes_rejection_reason():
    rows = [{"First Name": "Connor", "Last Name": "McKinnis", "External ID": "99"}]
    issues = {"99": "invalid phone"}
    result = format_contact_list(rows, issues_by_external_id=issues)
    assert "Connor McKinnis (invalid phone)" in result


def test_build_sync_success_contact_context():
    valid = [{"First Name": "Jane", "Last Name": "Doe", "External ID": "1"}]
    rejected = [{"First Name": "Bad", "Last Name": "Row", "External ID": "2"}]
    issues = [ValidationIssue(external_id="2", reason="missing phone")]
    ctx = build_sync_success_contact_context(valid, rejected, issues)
    assert ctx["Succeeded contacts"] == "Jane Doe"
    assert "Bad Row (missing phone)" in ctx["Failed contacts"]


def test_build_sync_failure_contact_context():
    valid = [{"First Name": "John", "Last Name": "Smith", "External ID": "1"}]
    ctx = build_sync_failure_contact_context(valid, [], [])
    assert ctx["Attempted contacts"] == "John Smith"
    assert "Succeeded contacts" not in ctx
