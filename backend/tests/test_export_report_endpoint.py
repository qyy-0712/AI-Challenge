from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import app.report_store as rs
from app.main import app


def test_export_review_report(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(rs, "_reports_dir", lambda: tmp_path)

    review_id = "abc123"
    p = tmp_path / f"review-20200101-000000-{review_id}.txt"
    p.write_text("# report\n", encoding="utf-8")

    client = TestClient(app)
    resp = client.get(f"/review/{review_id}/export")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers.get("content-type", "")
    assert resp.text.startswith("# report")


