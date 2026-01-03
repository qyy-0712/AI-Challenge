from __future__ import annotations

import types

import pytest

import app.graph.graph as graph_mod
from app.config import Settings
from app.schemas import ReviewRequest


class _DummyLLMCompileBlock:
    """
    固定输出，保证报告可做快照比对。
    调用顺序：
    1) compile_guard JSON
    2) fix suggestions markdown
    """

    def __init__(self, *args, **kwargs):
        self._model = (kwargs.get("model") or "").strip()

    def invoke(self, messages):
        if self._model == "deepseek-chat":
            return types.SimpleNamespace(
                content=(
                    '{"compilable": false, "errors": ['
                    '{"file": "a.cpp", "line": 10, "type": "SyntaxError", "message": "int 后缺少声明"}'
                    '], "fix_advice_cn": "- 在 a.cpp:10 删除不完整声明，或补全变量名与分号。\\n- 错误: int\\n  正确: int x;"}'
                )
            )
        return types.SimpleNamespace(
            content=(
                "## 修复建议\n\n"
                "- 在 `a.cpp:10` 删除不完整声明，或补全变量名与分号。\n\n"
                "错误示例：\n"
                "```cpp\n"
                "int\n"
                "```\n\n"
                "正确示例：\n"
                "```cpp\n"
                "int x;\n"
                "```\n"
            )
        )


class _DummyGitHubClientCompileBlock:
    def __init__(self, token=None):
        pass

    async def fetch_diff(self, repo_full_name: str, pr_number: int) -> str:
        return "diff --git a/a.cpp b/a.cpp\n@@ -1 +1 @@\n+int \n"

    async def fetch_pr_files_meta(self, repo_full_name: str, pr_number: int):
        return [
            {
                "path": "a.cpp",
                "status": "modified",
                "patch": "@@ -1 +1 @@\n+int \n",
                "raw_url": "",
                "content": "",
            }
        ]

    async def fetch_greptile_reference_text(self, repo_full_name: str, pr_number: int) -> str:
        return ""


@pytest.mark.asyncio
async def test_report_markdown_compile_guard_snapshot(monkeypatch):
    monkeypatch.setattr(graph_mod, "ChatOpenAI", _DummyLLMCompileBlock)
    monkeypatch.setattr(graph_mod, "GitHubClient", _DummyGitHubClientCompileBlock)
    monkeypatch.setattr(graph_mod, "save_report_markdown", lambda md: {"id": "rid", "path": "x", "filename": "x.md"})

    req = ReviewRequest(repo_full_name="owner/repo", pr_number=1, requirements=None)
    res = await graph_mod.run_review(req, Settings(), token="t")

    assert res.review_id == "rid"
    md = res.report_markdown
    # 核心结构
    assert md.startswith("PR 审查报告")
    assert "二、最终结论" in md
    assert "三、必须修复的问题清单" in md
    assert "1. 语法错误" in md
    assert "位置: a.cpp:10" in md
    assert "相关代码片段" in md
    assert "DIFF PATCH (fallback)" in md
    assert "四、修复建议" in md
    # compile 阻断时不应包含其它分区
    assert "确定性静态缺陷（高优先级）" not in md
    assert "AI 推理风险（基于上下文推断）" not in md
    assert len(res.findings) == 1


