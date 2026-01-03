from __future__ import annotations

from app.config import Settings
from app.mcp.tools import MCPClient


def test_security_signal_python_input_to_os_system() -> None:
    mcp = MCPClient(Settings())
    code = "\n".join(
        [
            "import os",
            "cmd = input('cmd: ')",
            "os.system(cmd)",
        ]
    )
    res = mcp.security_signal([{"path": "a.py", "content": code, "patch": ""}])
    assert any(s.get("sink") == "Command" and s.get("source") == "UserInput" for s in res["signals"])


def test_security_signal_python_execute_user_input_sql() -> None:
    mcp = MCPClient(Settings())
    code = "cursor.execute(user_input)\n"
    res = mcp.security_signal([{"path": "a.py", "content": code, "patch": ""}])
    assert any(s.get("sink") == "SQL" and s.get("source") == "UserInput" for s in res["signals"])


def test_security_signal_js_child_process_exec() -> None:
    mcp = MCPClient(Settings())
    code = "const { exec } = require('child_process'); exec(userInput);\n"
    res = mcp.security_signal([{"path": "a.js", "content": code, "patch": ""}])
    assert any(s.get("sink") == "Command" for s in res["signals"])


