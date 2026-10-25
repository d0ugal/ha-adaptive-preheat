"""Parsing of the JSON-lines history import format."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from rtm_core.history_import import HistoryImportError, parse_history, parse_line


def row(ts: str = "2026-01-05T07:00:00+00:00", **fields: object) -> str:
    return json.dumps({"ts": ts, "indoor_c": 18.5, "outdoor_c": 3.0, "effort": 0.5, **fields})


def test_parses_rows_with_optional_fields_and_offsets():
    parsed = parse_history(
        [
            row(),
            "",
            '{"ts": "2026-01-05T08:00:00Z", "indoor_c": null}',
            '{"ts": "2026-01-05T10:00:00+01:00", "effort": 1}',
        ]
    )
    first, second, third = parsed.samples
    assert (first.indoor_c, first.outdoor_c, first.effort) == (18.5, 3.0, 0.5)
    assert second.ts == datetime(2026, 1, 5, 8, tzinfo=UTC)
    assert (second.indoor_c, second.outdoor_c, second.effort) == (None, None, None)
    assert third.ts == datetime(2026, 1, 5, 9, tzinfo=UTC)
    assert third.effort == 1.0
    assert parsed.lines == 3
    assert not parsed.reordered
    assert parsed.duplicates == 0


def test_out_of_order_rows_are_sorted_and_duplicates_keep_the_last():
    parsed = parse_history(
        [
            row("2026-01-05T08:00:00+00:00", indoor_c=19.0),
            row("2026-01-05T07:00:00+00:00"),
            row("2026-01-05T08:00:00+00:00", indoor_c=20.0),
        ]
    )
    assert parsed.reordered
    assert parsed.duplicates == 1
    assert [s.indoor_c for s in parsed.samples] == [18.5, 20.0]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("{", "not valid JSON"),
        ("[1]", "not a JSON object"),
        ('{"indoor_c": 1}', "missing ts"),
        ('{"ts": 5}', "missing ts"),
        ('{"ts": "yesterday"}', "not ISO 8601"),
        ('{"ts": "2026-01-05T07:00:00"}', "no UTC offset"),
        (row(indoor_c="warm"), "indoor_c must be a number"),
        (row(indoor_c=True), "indoor_c must be a number"),
        (row(indoor_c=500), "outside"),
        (row(outdoor_c=float("nan")), "outside"),
        (row(effort=1.5), "effort 1.5 is outside"),
    ],
)
def test_bad_lines_are_rejected_with_a_reason(text: str, message: str):
    with pytest.raises(ValueError, match=message):
        parse_line(text)


def test_a_bad_file_lists_the_first_lines_and_imports_nothing():
    lines = [row()] + ["oops"] * 8
    with pytest.raises(HistoryImportError) as err:
        parse_history(lines)
    text = str(err.value)
    assert "line 2: not valid JSON" in text
    assert "line 6" in text
    assert "line 7" not in text
    assert "and 3 more" in text


def test_empty_file_and_row_cap(monkeypatch: pytest.MonkeyPatch):
    with pytest.raises(HistoryImportError, match="no rows"):
        parse_history(["", "  "])
    monkeypatch.setattr("rtm_core.history_import.MAX_ROWS", 2)
    with pytest.raises(HistoryImportError, match="split the file"):
        parse_history([row(), row(), row()])
