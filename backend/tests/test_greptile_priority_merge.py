from __future__ import annotations

import types

import pytest

import app.graph.graph as graph_mod
from app.config import Settings
from app.schemas import ReviewRequest


class _DummyLLM:
    def __init__(self, *args, **kwargs):
        self._model = (kwargs.get("model") or "").strip()

    def invoke(self, messages):
        text = ""
        if isinstance(messages, list) and messages:
            # messages like [("user", prompt)]
            text = str(messages[0][1])

        # DeepSeek compile_guard: pass compilable so we reach synthesis/merge
        if self._model == "deepseek-chat":
            return types.SimpleNamespace(content='{"compilable": true, "errors": [], "fix_advice_cn": ""}')

        # GLM greptile_parse: detect GREPTILE_TEXT prompt
        if "GREPTILE_TEXT:" in text:
            return types.SimpleNamespace(
                content=(
                    '[{"file":"a.cpp","line":1,"level":"high","category":"Bug","title":"InfiniteLoop","detail":"GT says loop never terminates","suggestion":"Add break condition"},'
                    '{"file":"b.cpp","line":2,"level":"medium","category":"Style","title":"Naming","detail":"GT naming","suggestion":"Rename var"}]'
                )
            )

        # GLM ai_review: return one finding overlapping with greptile (same title/file/line)
        return types.SimpleNamespace(
            content='[{"file":"a.cpp","line":1,"level":"medium","category":"AI Review","title":"InfiniteLoop","detail":"Ours loop risk","suggestion":"Fix loop"}]'
        )


class _DummyGitHubClient:
    def __init__(self, token=None):
        pass

    async def fetch_diff(self, repo_full_name: str, pr_number: int) -> str:
        return "diff --git a/a.cpp b/a.cpp\n@@ -1 +1 @@\n+while(true){}\n"

    async def fetch_pr_files_meta(self, repo_full_name: str, pr_number: int):
        return [
            {"path": "a.cpp", "status": "modified", "patch": "@@ -1 +1 @@\n+while(true){}\n", "raw_url": "", "content": ""},
        ]

    async def fetch_raw_text(self, raw_url: str) -> str:
        return ""

    async def fetch_greptile_reference_text(self, repo_full_name: str, pr_number: int) -> str:
        return "Greptile Review: InfiniteLoop at a.cpp:1 and Naming at b.cpp:2"


@pytest.mark.asyncio
async def test_greptile_priority_merge_orders_both_then_greptile_then_ours(monkeypatch):
    monkeypatch.setattr(graph_mod, "ChatOpenAI", _DummyLLM)
    monkeypatch.setattr(graph_mod, "GitHubClient", _DummyGitHubClient)
    monkeypatch.setattr(graph_mod, "save_report_markdown", lambda md: {"id": "rid", "path": "x", "filename": "x.txt"})

    req = ReviewRequest(repo_full_name="owner/repo", pr_number=1, requirements=None)
    res = await graph_mod.run_review(req, Settings(), token="t")
    report = res.report_markdown

    assert "关键问题清单（按优先级排序）" in report
    # Overlap finding should be present and marked as high-confidence
    assert "来源: 高置信（多来源一致）" in report
    # Greptile-only should also appear (external reference)
    assert "来源: 中置信（外部参考）" in report


