"""Tests for delete staging CSV generation."""

import csv
from pathlib import Path

from tests.test_pipeline import make_config
from src.delta import write_delete_staging_csv


def test_write_delete_staging_csv_populates_external_id_only(tmp_path):
    config = make_config(tmp_path)
    headers = ["First Name", "Last Name", "External ID", "END"]
    write_delete_staging_csv(config, headers, "1234")

    with Path(config.delete_staging_csv).open(encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)

    assert reader.fieldnames == headers
    assert len(rows) == 1
    assert rows[0]["External ID"] == "1234"
    assert rows[0]["First Name"] == ""
    assert rows[0]["Last Name"] == ""
