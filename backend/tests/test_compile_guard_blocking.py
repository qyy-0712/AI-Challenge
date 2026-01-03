from __future__ import annotations

import types

import pytest

import app.graph.graph as graph_mod
from app.config import Settings
from app.schemas import ReviewRequest


class _DummyLLM:
    def __init__(self, *args, **kwargs):
        # build_graph now creates two LLM instances:
        # - DeepSeek for compile_guard
        # - GLM for other stages (e.g. fix suggestions)
        self._model = (kwargs.get("model") or "").strip()

    def invoke(self, messages):
        if self._model == "deepseek-chat":
            # compile_guard JSON
            return types.SimpleNamespace(
                content=(
                    '{"compilable": false, "errors": '
                    '[{"file": "a.cpp", "line": 10, "type": "SyntaxError", "message": "缺少声明"}], '
                    '"fix_advice_cn": "- 在 a.cpp:10 补全声明或删除不完整代码。\\n- 示例：将 `int` 改为 `int x;`"}'
                )
            )
        # GLM path: compile fix suggestions (text)
        return types.SimpleNamespace(content="## 修复建议\n\n- 请删除不完整声明或补全变量名与分号。\n")


class _DummyGitHubClient:
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
async def test_compile_guard_blocks_and_generates_report(monkeypatch):
    monkeypatch.setattr(graph_mod, "ChatOpenAI", _DummyLLM)
    monkeypatch.setattr(graph_mod, "GitHubClient", _DummyGitHubClient)
    monkeypatch.setattr(graph_mod, "save_report_markdown", lambda md: {"id": "rid", "path": "x", "filename": "x.md"})

    req = ReviewRequest(repo_full_name="owner/repo", pr_number=1, requirements=None)
    res = await graph_mod.run_review(req, Settings(), token="t")
    assert res.review_id == "rid"
    assert "二、最终结论" in res.report_markdown
    assert "三、必须修复的问题清单" in res.report_markdown
    assert "a.cpp:10" in res.report_markdown
    assert "DIFF PATCH (fallback)" in res.report_markdown
    assert len(res.findings) == 1
    assert res.findings[0].category == "Compile/Parse"


