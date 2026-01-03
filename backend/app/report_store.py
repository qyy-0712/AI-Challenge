from __future__ import annotations

import os
import time
import uuid
from pathlib import Path


def _reports_dir() -> Path:
    # Keep reports under backend/reports for easy access
    base = Path(__file__).resolve().parents[2]  # backend/
    d = base / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_report_markdown(markdown: str) -> dict:
    """
    Persist report as UTF-8 text file.
    Returns: {id, path, filename}
    """
    report_id = uuid.uuid4().hex
    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    filename = f"review-{ts}-{report_id}.txt"
    path = _reports_dir() / filename
    path.write_text(markdown or "", encoding="utf-8")
    return {"id": report_id, "path": str(path), "filename": filename}


def find_report_file(report_id: str) -> Path | None:
    d = _reports_dir()
    for p in d.glob(f"*{report_id}*.txt"):
        return p
    return None


