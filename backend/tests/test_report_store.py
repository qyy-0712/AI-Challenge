from __future__ import annotations

from pathlib import Path

import app.report_store as rs


def test_save_and_find_report_markdown(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(rs, "_reports_dir", lambda: tmp_path)

    saved = rs.save_report_markdown("# hello\n")
    assert saved["id"]
    p = Path(saved["path"])
    assert p.exists()
    assert p.read_text(encoding="utf-8") == "# hello\n"

    found = rs.find_report_file(saved["id"])
    assert found is not None
    assert found.resolve() == p.resolve()


