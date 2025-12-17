from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, TypedDict

from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

from ..config import Settings
from ..github_client import GitHubClient
from ..mcp.tools import MCPClient
from ..report_store import save_report_markdown
from ..schemas import Finding, ReviewRequest, ReviewResponse


class ReviewState(TypedDict, total=False):
    repo_full_name: str
    pr_number: int
    requirements: Optional[str]
    diff: str
    changed_files: List[str]
    file_blobs: List[Dict[str, str]]
    related_files: List[str]
    language: str
    deterministic: Dict[str, Any]
    ai_findings: List[Dict[str, Any]]
    report_markdown: str
    llm_compile_result: Dict[str, Any]
    llm_compile_block: bool
    llm_compile_parse_error: Optional[str]


def detect_language(changed_files: List[str]) -> str:
    for path in changed_files:
        if path.endswith(".py"):
            return "python"
        if path.endswith(".js") or path.endswith(".jsx") or path.endswith(".ts") or path.endswith(".tsx"):
            return "javascript"
        if path.endswith(".java"):
            return "java"
        if path.endswith(".cpp") or path.endswith(".cc") or path.endswith(".cxx"):
            return "cpp"
    return "mixed"


def build_graph(settings: Settings, token: Optional[str] = None):
    github_client = GitHubClient(token=token)
    mcp_client = MCPClient(settings)
    llm = ChatOpenAI(
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        temperature=0.2,
    )

    async def pr_context_builder(state: ReviewState) -> ReviewState:
        repo = state["repo_full_name"]
        pr_number = state["pr_number"]
        diff = await github_client.fetch_diff(repo, pr_number)
        files = await github_client.fetch_pr_files_with_content(repo, pr_number)
        changed_files = [f["path"] for f in files]
        lang = detect_language(changed_files)
        return {
            **state,
            "diff": diff,
            "changed_files": changed_files,
            "file_blobs": files,
            "related_files": [],  # TODO: expand with dependency-aware fetch if needed
            "language": lang,
        }

    def _try_parse_json_object(text: str) -> tuple[Optional[dict], Optional[str]]:
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data, None
            return None, "LLM response is not a JSON object"
        except Exception:
            # fallback: extract first {...} block
            m = re.search(r"\{[\s\S]*\}", text)
            if not m:
                return None, "Failed to find JSON object in LLM response"
            try:
                data = json.loads(m.group(0))
                if isinstance(data, dict):
                    return data, None
                return None, "Extracted JSON is not an object"
            except Exception as exc:  # noqa: BLE001
                return None, f"JSON parse error: {type(exc).__name__}"

    def compile_guard_node(state: ReviewState) -> ReviewState:
        """
        用 LLM 扮演“万能编译器”，在真实工具前判定是否可编译；若判定失败，直接短路后续分析。
        """
        # IMPORTANT: keep prompt ASCII-only to avoid console encoding issues in some environments.
        diff_text = state.get("diff", "")
        # keep file contents bounded
        file_blobs = state.get("file_blobs", [])
        compact_files = []
        for f in file_blobs[:25]:
            compact_files.append(
                {
                    "path": f.get("path"),
                    "status": f.get("status"),
                    "patch": (f.get("patch") or "")[:2000],
                    "content_head": (f.get("content") or "")[:2000],
                }
            )
        prompt = (
            "Role: Universal multi-language compiler + type checker.\n"
            "Task: Given a GitHub PR diff and partial file contents, decide whether the PR would fail to compile/type-check.\n"
            "Rules:\n"
            "- Output MUST be a single JSON object and nothing else.\n"
            "- Schema: {\"compilable\": boolean, \"errors\": [{\"file\": string, \"line\": number, \"type\": \"SyntaxError\"|\"TypeError\"|\"CompileError\"|\"MissingDependency\", \"message\": string}]}\n"
            "- Only include deterministic compile-time errors that follow directly from the diff/content. No runtime speculation.\n"
            "- If you are not certain, set compilable=true and return errors=[].\n"
            "- Prefer SyntaxError/TypeError with exact file+line when possible; keep errors concise (max 10).\n"
            "\nPR_DIFF:\n"
            f"{diff_text[:12000]}\n"
            "\nFILES_CONTEXT(JSON):\n"
            f"{json.dumps(compact_files, ensure_ascii=True)}\n"
        )
        resp = llm.invoke([("user", prompt)])
        content = resp.content if isinstance(resp.content, str) else ""
        data, parse_error = _try_parse_json_object(content)
        compilable = True
        errors: list = []
        if isinstance(data, dict):
            compilable = bool(data.get("compilable", True))
            errors = data.get("errors", []) if isinstance(data.get("errors", []), list) else []
            # hard cap + normalize
            errors = errors[:10]
        return {
            **state,
            "llm_compile_result": {"compilable": compilable, "errors": errors},
            "llm_compile_block": not compilable,
            "llm_compile_parse_error": parse_error,
        }

    def deterministic_analysis_node(state: ReviewState) -> ReviewState:
        file_blobs = state.get("file_blobs", [])
        files_payload = [{"path": f.get("path"), "content": f.get("content", ""), "patch": f.get("patch", "")} for f in file_blobs]
        # NOTE: compile_check path is deprecated; compile-level review relies solely on LLM compile_guard.
        defect_result = mcp_client.static_defect_scan(files_payload)
        dependency_result = mcp_client.dependency_analysis(files_payload)
        security_result = mcp_client.security_signal(files_payload)
        deterministic = {
            "static_defect_scan": defect_result,
            "dependency_analysis": dependency_result,
            "security_signal": security_result,
        }
        return {**state, "deterministic": deterministic}

    def parse_ai_findings(text: str) -> List[Dict[str, Any]]:
        try:
            data = json.loads(text)
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "findings" in data:
                return data["findings"]
        except Exception:
            pass
        return []

    def normalize_finding(raw: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "file": raw.get("file"),
            "line": raw.get("line"),
            "level": raw.get("level", "medium"),
            "category": raw.get("category", "AI Review"),
            "title": raw.get("title", "Issue"),
            "detail": raw.get("detail", ""),
            "suggestion": raw.get("suggestion", ""),
        }

    def ai_review_node(state: ReviewState) -> ReviewState:
        prompt = (
            "你是资深代码审查专家。请基于PR diff、确定性MCP结果、上下文，输出JSON数组 findings，"
            "每个元素包含 file, line, level(critical|high|medium|low), category, title, detail, suggestion。"
            "不要重复MCP已确定的问题；避免无法判断的结论；给出理由。"
            f"\n\nPR diff:\n{state.get('diff','')}\n\n"
            f"MCP结果:\n{json.dumps(state.get('deterministic', {}), ensure_ascii=False)}\n\n"
            f"需求:\n{state.get('requirements') or ''}"
        )
        resp = llm.invoke([("user", prompt)])
        ai_findings = parse_ai_findings(resp.content)
        ai_findings = [normalize_finding(item) for item in ai_findings]
        return {**state, "ai_findings": ai_findings}

    def synthesis_node(state: ReviewState) -> ReviewState:
        deterministic = state.get("deterministic", {})
        ai_findings = state.get("ai_findings", [])
        sections = []
        # 若 LLM 判断不可编译，优先返回其报告
        llm_compile = state.get("llm_compile_result")
        if llm_compile and llm_compile.get("compilable") is False:
            # Build a polished, actionable compile-level report in Chinese Markdown.
            repo = state.get("repo_full_name", "")
            pr = state.get("pr_number", "")
            errors = llm_compile.get("errors", [])[:10]

            def _basename(p: str) -> str:
                return (p or "").replace("\\", "/").split("/")[-1]

            def find_blob(file_path: str) -> dict:
                fps = (file_path or "").replace("\\", "/")
                for fb in state.get("file_blobs", []) or []:
                    if (fb.get("path") or "").replace("\\", "/") == fps:
                        return fb
                # fallback: suffix match
                for fb in state.get("file_blobs", []) or []:
                    p = (fb.get("path") or "").replace("\\", "/")
                    if p.endswith("/" + _basename(fps)) or _basename(p) == _basename(fps):
                        return fb
                return {}

            def find_content(file_path: str) -> str:
                fb = find_blob(file_path)
                return fb.get("content") or ""

            def find_patch(file_path: str) -> str:
                fb = find_blob(file_path)
                return fb.get("patch") or ""

            def snippet(content: str, line: int, context: int = 3, patch_fallback: str = "") -> str:
                if not content:
                    if patch_fallback:
                        return "DIFF PATCH (fallback):\n" + patch_fallback
                    return "(no content available)"
                lines = content.splitlines()
                if line is None or line <= 0:
                    start = 1
                else:
                    start = max(1, line - context)
                end = min(len(lines), (line + context) if line and line > 0 else min(len(lines), context * 2 + 1))
                out = []
                for i in range(start, end + 1):
                    prefix = ">>" if line and i == line else "  "
                    out.append(f"{prefix} {i:4d} | {lines[i-1]}")
                return "\n".join(out)

            md = []
            md.append("# PR 编译级审查报告")
            md.append("")
            md.append(f"- 仓库: `{repo}`")
            md.append(f"- PR: `#{pr}`")
            md.append("")
            md.append("## [BLOCKER] 编译/类型检查无法通过")
            md.append("以下问题会直接阻断合并与后续审查。请先修复这些编译级错误，再重新发起审查。")
            md.append("")

            for idx, err in enumerate(errors, start=1):
                f = err.get("file") or "(unknown)"
                ln = err.get("line") or 0
                typ = err.get("type") or "CompileError"
                msg = err.get("message") or ""
                md.append(f"### 错误 {idx}")
                md.append(f"- 位置: `{f}:{ln}`")
                md.append(f"- 类型: `{typ}`")
                md.append(f"- 信息: {msg}")
                md.append("")
                code = snippet(
                    find_content(f),
                    int(ln) if isinstance(ln, int) else 0,
                    patch_fallback=find_patch(f),
                )
                md.append("#### 相关代码片段")
                md.append("```text")
                md.append(code)
                md.append("```")
                md.append("")

            # Ask LLM to propose concrete fixes with correct syntax examples.
            fix_prompt = (
                "You are a senior engineer. Write a Chinese markdown section titled '## 修复建议' for the compile errors.\n"
                "Requirements:\n"
                "- Provide concrete fixes for each error; reference file and line.\n"
                "- Show a 'wrong' snippet (short) and a 'correct' snippet for each error, using markdown code fences.\n"
                "- Do not speculate runtime issues.\n"
                "- Keep it concise.\n"
                "\nERRORS_JSON:\n"
                f"{json.dumps(errors, ensure_ascii=True)}\n"
                "\nPR_DIFF:\n"
                f"{(state.get('diff','') or '')[:8000]}\n"
            )
            fix_resp = llm.invoke([("user", fix_prompt)])
            fix_md = fix_resp.content if isinstance(fix_resp.content, str) else ""
            if fix_md:
                md.append(fix_md.strip())

            report = "\n".join(md).strip() + "\n"
            return {**state, "report_markdown": report}

        def _basename(p: str) -> str:
            return (p or "").replace("\\", "/").split("/")[-1]

        def find_blob(file_path: str) -> dict:
            fps = (file_path or "").replace("\\", "/")
            for fb in state.get("file_blobs", []) or []:
                if (fb.get("path") or "").replace("\\", "/") == fps:
                    return fb
            # fallback: suffix match
            for fb in state.get("file_blobs", []) or []:
                p = (fb.get("path") or "").replace("\\", "/")
                if p.endswith("/" + _basename(fps)) or _basename(p) == _basename(fps):
                    return fb
            return {}

        def snippet_for(file_path: str, line: int) -> str:
            fb = find_blob(file_path)
            content = fb.get("content") or ""
            patch = fb.get("patch") or ""

            if not content:
                return f"DIFF PATCH (fallback):\n{patch}" if patch else "(no content available)"

            lines = content.splitlines()
            ln = line or 0
            start = max(1, ln - 3) if ln > 0 else 1
            end = min(len(lines), ln + 3) if ln > 0 else min(len(lines), 7)
            out = []
            for i in range(start, end + 1):
                prefix = ">>" if ln > 0 and i == ln else "  "
                out.append(f"{prefix} {i:4d} | {lines[i-1]}")
            return "\n".join(out)

        static_defects = deterministic.get("static_defect_scan", {}).get("defects", [])
        if static_defects:
            md = []
            md.append("[BLOCKER] Static Defects")
            for idx, defect in enumerate(static_defects, start=1):
                f = defect.get("file") or "(unknown)"
                ln = int(defect.get("line") or 0)
                typ = defect.get("type") or "Defect"
                reason = defect.get("reason") or ""
                md.append("")
                md.append(f"###{idx}. {typ}")
                md.append(f"- 位置: `{f}:{ln}`")
                md.append(f"- 原因: {reason}")
                md.append("#### 相关代码片段")
                md.append("```text")
                md.append(snippet_for(f, ln))
                md.append("```")
            sections.append("\n".join(md).strip())
        dep_issues = deterministic.get("dependency_analysis", {}).get("violations", [])
        if dep_issues:
            md = []
            md.append("[ARCH] Architecture / Dependency Issues")
            for idx, v in enumerate(dep_issues, start=1):
                md.append("")
                md.append(f"###{idx}. {v.get('type')}")
                md.append(f"- 详情: {v.get('detail')}")
            sections.append("\n".join(md).strip())
        security = deterministic.get("security_signal", {}).get("signals", [])
        if security:
            md = []
            md.append("[WARN] Security Signals")
            for idx, sig in enumerate(security, start=1):
                md.append("")
                md.append(f"###{idx}. dataflow signal")
                md.append(f"- source: `{sig.get('source')}`")
                md.append(f"- sink: `{sig.get('sink')}`")
                md.append(f"- sanitized: `{sig.get('sanitized')}`")
            sections.append("\n".join(md).strip())
        if ai_findings:
            md = []
            md.append("[WARN] Potential Risks / AI Review")
            for idx, f in enumerate(ai_findings, start=1):
                file_path = f.get("file") or ""
                ln = int(f.get("line") or 0) if f.get("line") is not None else 0
                md.append("")
                md.append(f"###{idx}. {f.get('title')}")
                md.append(f"- 风险级别: `{f.get('level')}`")
                if file_path:
                    md.append(f"- 位置: `{file_path}:{ln}`")
                md.append(f"- 原因: {f.get('detail')}")
                md.append(f"- 建议: {f.get('suggestion')}")
                if file_path:
                    md.append("#### 相关代码片段")
                    md.append("```text")
                    md.append(snippet_for(file_path, ln))
                    md.append("```")
            sections.append("\n".join(md).strip())
        report = "\n\n".join(sections) if sections else "未发现显著问题。"
        return {**state, "report_markdown": report}

    workflow = StateGraph(ReviewState)
    workflow.add_node("pr_context", pr_context_builder)
    workflow.add_node("compile_guard", compile_guard_node)
    workflow.add_node("deterministic", deterministic_analysis_node)
    workflow.add_node("ai_review", ai_review_node)
    workflow.add_node("synthesis", synthesis_node)

    workflow.set_entry_point("pr_context")
    workflow.add_edge("pr_context", "compile_guard")
    workflow.add_conditional_edges(
        "compile_guard",
        lambda state: "block" if state.get("llm_compile_block") else "pass",
        {"block": "synthesis", "pass": "deterministic"},
    )
    workflow.add_edge("deterministic", "ai_review")
    workflow.add_edge("ai_review", "synthesis")
    workflow.add_edge("synthesis", END)
    return workflow.compile()


async def run_review(req: ReviewRequest, settings: Settings, token: Optional[str] = None) -> ReviewResponse:
    graph = build_graph(settings, token=token)
    initial_state: ReviewState = {
        "repo_full_name": req.repo_full_name,
        "pr_number": req.pr_number,
        "requirements": req.requirements,
    }
    result: ReviewState = await graph.ainvoke(initial_state)
    findings: List[Finding] = []

    # Compile deterministic findings into unified Finding list.
    det = result.get("deterministic", {})
    llm_compile = result.get("llm_compile_result")
    if llm_compile and llm_compile.get("compilable") is False:
        for err in llm_compile.get("errors", [])[:10]:
            findings.append(
                Finding(
                    file=err.get("file"),
                    line=err.get("line"),
                    level="critical",
                    category="Compile/Parse",
                    title=err.get("type", "Compile Error"),
                    detail=err.get("message", ""),
                    suggestion="请先修复编译/类型检查错误后再进行后续审查。",
                )
            )
    for defect in det.get("static_defect_scan", {}).get("defects", []):
        findings.append(
            Finding(
                file=defect.get("file"),
                line=defect.get("line"),
                level="high",
                category=f"StaticDefect:{defect.get('type')}",
                title="静态必然缺陷",
                detail=defect.get("reason", ""),
                suggestion="请根据缺陷原因移除死代码或修正逻辑。",
            )
        )
    for dep in det.get("dependency_analysis", {}).get("violations", []):
        findings.append(
            Finding(
                file=None,
                line=None,
                level="medium",
                category="Architecture",
                title=dep.get("type", "Dependency Issue"),
                detail=dep.get("detail", ""),
                suggestion="请按分层/依赖规范调整模块关系。",
            )
        )
    for sig in det.get("security_signal", {}).get("signals", []):
        findings.append(
            Finding(
                file=None,
                line=None,
                level="medium",
                category="SecuritySignal",
                title=f"潜在数据流: {sig.get('source')}->{sig.get('sink')}",
                detail=f"sanitized={sig.get('sanitized')}",
                suggestion="确认输入验证与输出安全处理。",
            )
        )

    for f in result.get("ai_findings", []):
        findings.append(Finding(**f))

    report_markdown = result.get("report_markdown", "")
    saved = save_report_markdown(report_markdown)
    return ReviewResponse(review_id=saved["id"], report_markdown=report_markdown, findings=findings)

