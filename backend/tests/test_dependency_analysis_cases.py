from __future__ import annotations

from app.config import Settings
from app.mcp.tools import MCPClient


def test_dependency_analysis_layer_violation_api_imports_db() -> None:
    mcp = MCPClient(Settings())
    files = [
        {
            "path": "backend/api/user.py",
            "content": "import app.db\n\ndef f():\n    return 1\n",
            "patch": "",
        }
    ]
    res = mcp.dependency_analysis(files)
    assert res["violations"]
    assert any(v.get("type") == "LayerViolation" for v in res["violations"])


def test_dependency_analysis_layer_violation_api_imports_dao() -> None:
    mcp = MCPClient(Settings())
    files = [
        {
            "path": "service/api/handler.py",
            "content": "from foo.dao import UserDao\n",
            "patch": "",
        }
    ]
    res = mcp.dependency_analysis(files)
    assert any(v.get("type") == "LayerViolation" for v in res["violations"])


def test_dependency_analysis_no_violation_non_api_path() -> None:
    mcp = MCPClient(Settings())
    files = [
        {
            "path": "service/core/user.py",
            "content": "import app.db\n",
            "patch": "",
        }
    ]
    res = mcp.dependency_analysis(files)
    assert res["violations"] == []


