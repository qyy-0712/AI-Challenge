from __future__ import annotations

from app.config import Settings
from app.mcp.tools import MCPClient


def test_static_defect_detects_infinite_loop_from_patch_cpp() -> None:
    mcp = MCPClient(Settings())
    patch = "\n".join(
        [
            "@@ -1,1 +1,6 @@",
            "+int main(){",
            "+  while(true){",
            "+    int x;",
            "+    x++;",
            "+  }",
            "+}",
        ]
    )
    res = mcp.static_defect_scan(
        [
            {
                "path": "a.cpp",
                "content": "",
                "patch": patch,
            }
        ]
    )
    assert "defects" in res
    assert any(d.get("type") == "InfiniteLoop" and d.get("file") == "a.cpp" for d in res["defects"])


