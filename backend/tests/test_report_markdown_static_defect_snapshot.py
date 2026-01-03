from __future__ import annotations

import types

import pytest

import app.graph.graph as graph_mod
from app.config import Settings
from app.schemas import ReviewRequest


class _DummyLLMPassCompileNoAI:
    """
    固定输出：
    1) compile_guard -> compilable true
    2) ai_review -> 空数组
    """

    def __init__(self, *args, **kwargs):
        self._model = (kwargs.get("model") or "").strip()

    def invoke(self, messages):
        if self._model == "deepseek-chat":
            # compile_guard -> compilable true
            return types.SimpleNamespace(content='{"compilable": true, "errors": []}')
        # GLM -> ai_review -> empty array
        return types.SimpleNamespace(content="[]")


class _DummyGitHubClientStaticLoop:
    def __init__(self, token=None):
        pass

    async def fetch_diff(self, repo_full_name: str, pr_number: int) -> str:
        return "diff --git a/a.cpp b/a.cpp\n@@ -1,1 +1,6 @@\n+while(true){\n+  int x;\n+  x++;\n+}\n"

    async def fetch_pr_files_meta(self, repo_full_name: str, pr_number: int):
        return [
            {
                "path": "a.cpp",
                "status": "modified",
                "patch": "@@ -1,1 +1,6 @@\n+while(true){\n+  int x;\n+  x++;\n+}\n",
                "raw_url": "",
                "content": "",
            }
        ]

    async def fetch_greptile_reference_text(self, repo_full_name: str, pr_number: int) -> str:
        return ""


@pytest.mark.asyncio
async def test_report_markdown_static_defect_includes_patch_snippet(monkeypatch):
    monkeypatch.setattr(graph_mod, "ChatOpenAI", _DummyLLMPassCompileNoAI)
    monkeypatch.setattr(graph_mod, "GitHubClient", _DummyGitHubClientStaticLoop)
    monkeypatch.setattr(graph_mod, "save_report_markdown", lambda md: {"id": "rid", "path": "x", "filename": "x.md"})

    req = ReviewRequest(repo_full_name="owner/repo", pr_number=2, requirements=None)
    res = await graph_mod.run_review(req, Settings(), token="t")

    md = res.report_markdown
    assert "确定性静态缺陷（高优先级）" in md
    assert "InfiniteLoop" in md
    assert "DIFF PATCH (fallback)" in md
    assert "a.cpp" in md
    assert len(res.findings) >= 1


