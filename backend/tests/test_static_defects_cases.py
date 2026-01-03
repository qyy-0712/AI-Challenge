from __future__ import annotations

from app.config import Settings
from app.mcp.tools import MCPClient


def _types(defects: list[dict]) -> set[str]:
    return {d.get("type") for d in defects}


def test_static_defect_python_infinite_loop_while_true_no_break() -> None:
    mcp = MCPClient(Settings())
    code = "\n".join(
        [
            "def f():",
            "    while True:",
            "        x = 1",
            "        x += 1",
        ]
    )
    res = mcp.static_defect_scan([{"path": "a.py", "content": code, "patch": ""}])
    assert "defects" in res
    assert "InfiniteLoop" in _types(res["defects"])


def test_static_defect_python_resource_leak_open_without_with_or_close() -> None:
    mcp = MCPClient(Settings())
    code = "\n".join(
        [
            "def read():",
            "    f = open('a.txt', 'r')",
            "    data = f.read()",
            "    return data",
        ]
    )
    res = mcp.static_defect_scan([{"path": "a.py", "content": code, "patch": ""}])
    assert "ResourceLeak" in _types(res["defects"])


def test_static_defect_python_resource_leak_not_reported_for_with_open() -> None:
    mcp = MCPClient(Settings())
    code = "\n".join(
        [
            "def read():",
            "    with open('a.txt', 'r') as f:",
            "        return f.read()",
        ]
    )
    res = mcp.static_defect_scan([{"path": "a.py", "content": code, "patch": ""}])
    assert "ResourceLeak" not in _types(res["defects"])


def test_static_defect_python_always_true_condition_if_true() -> None:
    mcp = MCPClient(Settings())
    code = "\n".join(
        [
            "def f(x):",
            "    if True:",
            "        return 1",
            "    return 0",
        ]
    )
    res = mcp.static_defect_scan([{"path": "a.py", "content": code, "patch": ""}])
    assert "AlwaysTrueCondition" in _types(res["defects"])


def test_static_defect_js_always_true_condition_if_true() -> None:
    mcp = MCPClient(Settings())
    code = "\n".join(
        [
            "function f(){",
            "  if (true) {",
            "    return 1;",
            "  }",
            "  return 0;",
            "}",
        ]
    )
    res = mcp.static_defect_scan([{"path": "a.js", "content": code, "patch": ""}])
    assert "AlwaysTrueCondition" in _types(res["defects"])


