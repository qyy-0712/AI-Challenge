from __future__ import annotations

from app.config import Settings
from app.mcp.tools import MCPClient


def _types(defects: list[dict]) -> set[str]:
    return {d.get("type") for d in defects}


def test_dead_code_after_return_in_function() -> None:
    mcp = MCPClient(Settings())
    code = "\n".join(
        [
            "def f():",
            "    return 1",
            "    x = 2",
        ]
    )
    res = mcp.static_defect_scan([{"path": "a.py", "content": code, "patch": ""}])
    assert "DeadCode" in _types(res["defects"])


def test_dead_code_after_raise_in_function() -> None:
    mcp = MCPClient(Settings())
    code = "\n".join(
        [
            "def f():",
            "    raise ValueError('x')",
            "    print('unreachable')",
        ]
    )
    res = mcp.static_defect_scan([{"path": "a.py", "content": code, "patch": ""}])
    assert "DeadCode" in _types(res["defects"])


def test_divide_by_zero_literal_detected() -> None:
    mcp = MCPClient(Settings())
    code = "\n".join(
        [
            "def f():",
            "    return 1/0",
        ]
    )
    res = mcp.static_defect_scan([{"path": "a.py", "content": code, "patch": ""}])
    assert "DivideByZero" in _types(res["defects"])


def test_divide_by_zero_variable_not_reported() -> None:
    mcp = MCPClient(Settings())
    code = "\n".join(
        [
            "def f(x):",
            "    return 1/x",
        ]
    )
    res = mcp.static_defect_scan([{"path": "a.py", "content": code, "patch": ""}])
    assert "DivideByZero" not in _types(res["defects"])


def test_uninitialized_var_use_before_assign_detected() -> None:
    mcp = MCPClient(Settings())
    code = "\n".join(
        [
            "def f():",
            "    x = y + 1",
            "    y = 2",
            "    return x",
        ]
    )
    res = mcp.static_defect_scan([{"path": "a.py", "content": code, "patch": ""}])
    assert "UninitializedVar" in _types(res["defects"])


def test_uninitialized_var_not_reported_for_param() -> None:
    mcp = MCPClient(Settings())
    code = "\n".join(
        [
            "def f(y):",
            "    x = y + 1",
            "    return x",
        ]
    )
    res = mcp.static_defect_scan([{"path": "a.py", "content": code, "patch": ""}])
    assert "UninitializedVar" not in _types(res["defects"])


