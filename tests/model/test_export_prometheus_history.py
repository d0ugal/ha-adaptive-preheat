"""Pure parts of scripts/export_prometheus_history.py, plus one run against a local server."""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from rtm_core.history_import import parse_history

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "export_prometheus_history.py"
spec = importlib.util.spec_from_file_location("export_prometheus_history", SCRIPT)
assert spec is not None
assert spec.loader is not None
exporter = importlib.util.module_from_spec(spec)
sys.modules["export_prometheus_history"] = exporter
spec.loader.exec_module(exporter)

T0 = datetime(2026, 1, 5, tzinfo=UTC).timestamp()


def test_durations_and_times():
    assert exporter.parse_duration("5m") == 300
    assert exporter.parse_duration("1h30m") == 5400
    assert exporter.parse_duration("90") == 90
    assert exporter.parse_time("2026-01-05") == T0
    assert exporter.parse_time("2026-01-05T01:00:00+01:00") == T0
    assert exporter.parse_time("1767571200") == T0
    for bad in ("", "5x", "m5", "5m junk"):
        with pytest.raises(exporter.ExportError):
            exporter.parse_duration(bad)
    with pytest.raises(exporter.ExportError):
        exporter.parse_time("yesterday")


def test_chunks_cover_every_point_once_and_stay_under_the_limit():
    step = 300.0
    end = T0 + 40000 * step
    chunks = list(exporter.chunk_ranges(T0, end, step))
    assert len(chunks) == 5
    seen = 0
    previous_end = None
    for first, last in chunks:
        points = (last - first) / step + 1
        assert points <= exporter.MAX_POINTS
        if previous_end is not None:
            assert first == previous_end + step
        previous_end = last
        seen += points
    assert seen == 40001
    assert previous_end == end
    assert list(exporter.chunk_ranges(T0, T0, step)) == [(T0, T0)]
    with pytest.raises(exporter.ExportError):
        list(exporter.chunk_ranges(T0, T0 + 1, 0))
    with pytest.raises(exporter.ExportError):
        list(exporter.chunk_ranges(T0 + 1, T0, 60))


def matrix(values: list[list[object]], extra: int = 0) -> dict[str, object]:
    result = [{"metric": {"entity": f"e{i}"}, "values": values} for i in range(1 + extra)]
    return {"status": "success", "data": {"resultType": "matrix", "result": result}}


def test_parse_matrix():
    payload = matrix([[T0, "19.5"], [T0 + 300, "NaN"], [T0 + 600, "oops"], [T0 + 900, "20"]])
    assert exporter.parse_matrix(payload, "q") == {T0: 19.5, T0 + 900: 20.0}
    assert exporter.parse_matrix({"status": "success", "data": {"result": []}}, "q") == {}
    with pytest.raises(exporter.ExportError, match="2 series"):
        exporter.parse_matrix(matrix([[T0, "1"]], extra=1), "q")
    with pytest.raises(exporter.ExportError, match="bad query"):
        exporter.parse_matrix({"status": "error", "error": "bad query"}, "q")
    with pytest.raises(exporter.ExportError):
        exporter.parse_matrix("nonsense", "q")


def test_rows_are_on_the_grid_clamped_and_skip_empty_steps():
    series = {
        "indoor_c": {T0: 19.456, T0 + 600: 20.0},
        "effort": {T0: 1.4, T0 + 300: -0.2},
    }
    rows = list(exporter.build_rows(series, T0, T0 + 900, 300))
    assert [r["ts"] for r in rows] == [
        "2026-01-05T00:00:00+00:00",
        "2026-01-05T00:05:00+00:00",
        "2026-01-05T00:10:00+00:00",
    ]
    assert rows[0] == {"ts": rows[0]["ts"], "indoor_c": 19.46, "outdoor_c": None, "effort": 1.0}
    assert rows[1]["effort"] == 0.0
    assert rows[2]["indoor_c"] == 20.0


def test_headers():
    assert exporter.parse_header("Authorization: Bearer x") == ("Authorization", "Bearer x")
    with pytest.raises(exporter.ExportError):
        exporter.parse_header("nonsense")


class Handler(BaseHTTPRequestHandler):
    seen: list[dict[str, list[str]]] = []

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        form = parse_qs(body)
        Handler.seen.append(form)
        start, end, step = float(form["start"][0]), float(form["end"][0]), float(form["step"][0])
        if "fail" in form["query"][0]:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"status":"error"}')
            return
        values = []
        t = start
        while t <= end:
            values.append([t, "20.0" if "indoor" in form["query"][0] else "0.5"])
            t += step
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps(matrix(values)).encode())

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def server(socket_enabled: None):
    Handler.seen = []
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}/prometheus"
    httpd.shutdown()
    httpd.server_close()


def test_end_to_end_chunks_and_output_is_importable(server: str, tmp_path: Path):
    out = tmp_path / "out.jsonl"
    code = exporter.main(
        [
            server,
            "--start", "2026-01-05",
            "--end", "2026-02-15",
            "--step", "5m",
            "--indoor", "indoor_metric",
            "--effort", "effort_metric / 100",
            "--header", "X-Test: 1",
            "--output", str(out),
        ]
    )  # fmt: skip
    assert code == 0
    assert len(Handler.seen) == 2 * 2  # two chunks of 40 days (11520 points), two queries
    parsed = parse_history(out.read_text(encoding="utf-8").splitlines())
    assert len(parsed.samples) == 41 * 288 + 1
    assert parsed.samples[0].indoor_c == 20.0
    assert parsed.samples[0].effort == 0.5
    assert parsed.samples[0].outdoor_c is None


def test_main_reports_errors_without_a_traceback(
    server: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    base = ["--start", "2026-01-05", "--end", "2026-01-06", "--output", str(tmp_path / "o")]
    assert exporter.main([server, *base, "--indoor", "fail"]) == 1
    assert "HTTP 400" in capsys.readouterr().err
    assert exporter.main(["ftp://x", *base, "--indoor", "a"]) == 1
    assert exporter.main(["http://127.0.0.1:9", *base, "--indoor", "a"]) == 1
    assert "cannot query" in capsys.readouterr().err
