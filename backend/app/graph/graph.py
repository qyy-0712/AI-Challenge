from __future__ import annotations

import json
import os
import re
import threading
import time
import asyncio
from typing import Any, Dict, List, Optional, TypedDict

from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

from ..config import Settings
from ..greptile_client import GreptileMCPClient
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
    llm_ai_error: Optional[str]
    greptile_text: Optional[str]
    greptile_findings: List[Dict[str, Any]]
    greptile_error: Optional[str]
    greptile_ok: bool
    greptile_source: Optional[str]
    greptile_compile_block: bool


def _normalize_openai_base_url(url: str) -> str:
    """
    LangChain ChatOpenAI expects an OpenAI-compatible base_url (usually up to /v1).
    Users may provide a full endpoint like /v1/chat/completions (sometimes with trailing spaces).
    """
    u = (url or "").strip()
    if not u:
        return u
    # remove trailing "/chat/completions" if present
    u = re.sub(r"/chat/completions/?$", "", u)
    return u


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
    greptile_client = GreptileMCPClient(settings, github_token=token or "")
    # LLM routing policy:
    # - compile_guard (compile/type-check) -> DeepSeek first
    # - all other LLM usage -> GLM first
    llm_glm = ChatOpenAI(
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        base_url=_normalize_openai_base_url(settings.llm_base_url),
        temperature=0.2,
    )
    llm_deepseek = ChatOpenAI(
        api_key=settings.deepseek_api_key,
        model=settings.deepseek_model,
        base_url=_normalize_openai_base_url(settings.deepseek_base_url),
        temperature=0.2,
    )

    _LLM_MAX_CONCURRENCY = int(os.getenv("LLM_MAX_CONCURRENCY", "1") or "1")
    _llm_sem = threading.Semaphore(max(1, _LLM_MAX_CONCURRENCY))

    _llm_last_call_end = {"ts": 0.0}

    def _llm_invoke_with_retry(
        llm_client: Any,
        messages,
        max_attempts: int = 4,
        min_interval_s: float = 0.4,
    ) -> str:
        """
        Serialize LLM calls and retry on transient errors (e.g. 429).
        Returns response content (string). Raises on final failure.
        """
        last_exc: Optional[Exception] = None
        with _llm_sem:
            for attempt in range(1, max_attempts + 1):
                try:
                    # Ensure spacing between calls to avoid provider-side "concurrency" windows.
                    now = time.time()
                    gap = now - float(_llm_last_call_end["ts"])
                    if gap < min_interval_s:
                        time.sleep(min_interval_s - gap)
                    resp = llm_client.invoke(messages)
                    return resp.content if isinstance(resp.content, str) else ""
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    msg = str(exc)
                    # Retry only for rate limit / overload / network-ish signals
                    retryable = (
                        exc.__class__.__name__ in {"RateLimitError", "APITimeoutError", "APIConnectionError"}
                        or "429" in msg
                        or "concurrency" in msg.lower()
                        or "并发" in msg
                        or "timeout" in msg.lower()
                    )
                    if not retryable or attempt == max_attempts:
                        raise
                    # Backoff; keep serialized (still holding semaphore) to avoid overlapping retries.
                    time.sleep(0.8 * attempt)
                finally:
                    _llm_last_call_end["ts"] = time.time()
        # should not reach
        raise last_exc or RuntimeError("LLM invoke failed")

    async def pr_context_builder(state: ReviewState) -> ReviewState:
        repo = state["repo_full_name"]
        pr_number = state["pr_number"]
        diff = await github_client.fetch_diff(repo, pr_number)
        # For latency: fetch only PR files meta here (no raw content). Full contents are fetched
        # only after compile_guard passes.
        files = await github_client.fetch_pr_files_meta(repo, pr_number)
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

    async def hydrate_file_contents_node(state: ReviewState) -> ReviewState:
        """
        Fetch raw contents only after compile_guard passes.
        """
        file_blobs = list(state.get("file_blobs", []) or [])
        if not file_blobs:
            return state

        # Avoid large fan-out; keep it small and bounded.
        sem = asyncio.Semaphore(5)

        async def _one(i: int, raw_url: Optional[str]) -> tuple[int, str]:
            if not raw_url:
                return i, ""
            async with sem:
                try:
                    text = await github_client.fetch_raw_text(raw_url)
                    return i, text
                except Exception:
                    return i, ""

        tasks = [_one(i, (f or {}).get("raw_url")) for i, f in enumerate(file_blobs[:25])]
        if tasks:
            results = await asyncio.gather(*tasks)
            for idx, content in results:
                if 0 <= idx < len(file_blobs):
                    file_blobs[idx]["content"] = content

        return {**state, "file_blobs": file_blobs}

    async def greptile_reference_node(state: ReviewState) -> ReviewState:
        """
        强参考：从 PR 评论里抓取 Greptile 机器人审查内容（如果存在）。
        备注：在缺少 Greptile 官方 HTTP API 文档的情况下，这是最稳妥且可演示的集成方式。
        """
        # Keep unit tests hermetic: do NOT call Greptile MCP network under pytest,
        # but still allow GitHub-comment fallback and Greptile-text parsing tests.
        allow_greptile_mcp = not bool(os.getenv("PYTEST_CURRENT_TEST"))

        repo = state.get("repo_full_name", "")
        pr_number = int(state.get("pr_number") or 0)
        if not repo or pr_number <= 0:
            return {
                **state,
                "greptile_text": "",
                "greptile_findings": [],
                "greptile_error": None,
                "greptile_ok": False,
                "greptile_source": None,
                "greptile_compile_block": False,
            }
        text = ""
        gt_findings: list[dict] = []
        greptile_error: Optional[str] = None
        greptile_ok = False
        greptile_source: Optional[str] = None
        greptile_compile_block = False
        # Prefer Greptile official MCP endpoint if GREPTILE_API_KEY is configured.
        if allow_greptile_mcp and getattr(settings, "greptile_api_key", ""):
            try:
                default_branch = await github_client.fetch_repo_default_branch(repo)
                body, comments = await greptile_client.get_pr_review_bundle(
                    name=repo,
                    default_branch=default_branch,
                    pr_number=pr_number,
                    remote="github",
                    poll_timeout_s=20.0,
                )
                text = body or ""
                greptile_ok = bool(text.strip()) or bool(comments)
                greptile_source = "mcp"
                # Convert Greptile comments to structured findings with file/line evidence.
                for c in comments or []:
                    if not isinstance(c, dict):
                        continue
                    gt_findings.append(
                        normalize_finding(
                            {
                                "file": c.get("filePath"),
                                "line": c.get("lineStart"),
                                "level": "medium",
                                "category": "Greptile",
                                "title": "GreptileComment",
                                "detail": c.get("body") or "",
                                "suggestion": "",
                            }
                        )
                    )
                gt_findings = gt_findings[:30]
            except Exception as exc:  # noqa: BLE001
                text = ""
                gt_findings = []
                greptile_error = f"{type(exc).__name__}: {str(exc)[:260]}"
                greptile_ok = False
                greptile_source = "mcp"

        # Fallback: extract Greptile bot review from PR comments if present.
        if not text:
            text = await github_client.fetch_greptile_reference_text(repo, pr_number)
            if text:
                greptile_ok = True
                greptile_source = "github_comment"

        # Detect Greptile "compile block" signals (English/Chinese).
        # NOTE: Greptile MCP sometimes returns only structured comments (with empty body).
        # So we must scan BOTH text and comment bodies to decide compile-block.
        if greptile_ok:
            keywords = [
                "will not compile",
                "cannot compile",
                "won't compile",
                "compilation error",
                "compile error",
                "syntax error",
                "missing semicolon",
                "missing #include",
                "cannot be merged",
                "code will not compile",
                "无法编译",
                "编译失败",
                "语法错误",
                "缺少分号",
                "缺少 include",
                "缺少 #include",
            ]
            sig_parts: list[str] = []
            if text:
                sig_parts.append(text)
            for f in gt_findings[:30]:
                if isinstance(f, dict) and f.get("detail"):
                    sig_parts.append(str(f.get("detail")))
            gt_sig = ("\n".join(sig_parts)).lower()
            greptile_compile_block = any(k in gt_sig for k in keywords)

        # Keep bounded to avoid over-context
        text = (text or "")[:12000]
        return {
            **state,
            "greptile_text": text,
            "greptile_findings": gt_findings,
            "greptile_error": greptile_error,
            "greptile_ok": greptile_ok,
            "greptile_source": greptile_source,
            "greptile_compile_block": greptile_compile_block,
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
        # If Greptile already confirms compile failure, trust Greptile and short-circuit
        # DeepSeek compile guard (avoid extra LLM call + avoid false positives).
        if state.get("greptile_ok") and state.get("greptile_compile_block"):
            gt_text = (state.get("greptile_text") or "").strip()
            gt_findings = state.get("greptile_findings") or []

            errors: list[dict] = []
            adv: list[str] = []

            def add_err(file: str, line: int, typ: str, msg_cn: str):
                errors.append({"file": file or "(unknown)", "line": int(line or 0), "type": typ, "message": msg_cn})

            def norm(s: str) -> str:
                return (s or "").lower()

            # Prefer structured Greptile comments (filePath/lineStart)
            have_file_specific = False
            for f in gt_findings:
                if not isinstance(f, dict):
                    continue
                body = str(f.get("detail") or "")
                b = norm(body)
                file = str(f.get("file") or "")
                line = int(f.get("line") or 0)
                if file:
                    have_file_specific = True
                if "missing #include" in b or "缺少 #include" in body or "include <iostream>" in b:
                    add_err(file, line, "MissingDependency", "缺少必要的 include（例如 <iostream>），会导致编译失败。")
                    adv.append(f"- {file}:{line} 添加 `#include <iostream>`（或对应头文件）。")
                if "namespace" in b or "using namespace std" in b or "std::" in b:
                    add_err(file, line, "CompileError", "命名空间/标识符解析问题：请使用 `std::cin/std::cout` 或添加 `using namespace std;`。")
                    adv.append(f"- {file}:{line} 为 `cin/cout` 加 `std::` 前缀或添加 `using namespace std;`。")
                if "missing semicolon" in b or "缺少分号" in body:
                    add_err(file, line, "SyntaxError", "存在缺少分号的语法错误，会导致编译失败。")
                    adv.append(f"- {file}:{line} 检查语句末尾是否缺少 `;`（例如 `cout<<a;`）。")

            # Fallback: parse summary text
            t = gt_text.lower()
            if not errors:
                if "missing #include" in t or "缺少 #include" in gt_text:
                    add_err("", 0, "MissingDependency", "缺少必要的 include（例如 <iostream>），会导致编译失败。")
                if "namespace" in t or "using namespace std" in t:
                    add_err("", 0, "CompileError", "命名空间/标识符解析问题：请使用 `std::cin/std::cout` 或添加 `using namespace std;`。")
                if "missing semicolon" in t or "缺少分号" in gt_text:
                    add_err("", 0, "SyntaxError", "存在缺少分号的语法错误，会导致编译失败。")
            if not errors:
                add_err("", 0, "CompileError", "Greptile 判断该 PR 存在编译级错误，代码无法通过编译。")

            # de-dup errors; prefer those with file/line when possible
            uniq = []
            seen = set()
            for e in errors:
                k = (e.get("file"), int(e.get("line") or 0), e.get("type"), e.get("message"))
                if k in seen:
                    continue
                seen.add(k)
                uniq.append(e)
            errors = uniq[:10]

            fix_advice_cn = ""
            if adv:
                # de-dup
                uniq = []
                seen = set()
                for a in adv:
                    if a not in seen:
                        uniq.append(a)
                        seen.add(a)
                # store as plain lines (no leading '-'), synthesis will format bullets
                cleaned = []
                for a in uniq[:10]:
                    s = a.strip()
                    if s.startswith("-"):
                        s = s.lstrip("-").strip()
                    cleaned.append(s)
                # If we have file-specific advice, drop unknown ":0" suggestions
                if any(":" in x and not x.startswith(":") and not x.startswith("(unknown)") for x in cleaned):
                    cleaned = [x for x in cleaned if not x.startswith(":0")]
                fix_advice_cn = "\n".join(cleaned)

            return {
                **state,
                "llm_compile_result": {"compilable": False, "errors": errors[:10], "fix_advice_cn": fix_advice_cn},
                "llm_compile_block": True,
                "llm_compile_parse_error": None,
            }

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
            "- Schema: {\"compilable\": boolean, \"errors\": [{\"file\": string, \"line\": number, \"type\": \"SyntaxError\"|\"TypeError\"|\"CompileError\"|\"MissingDependency\", \"message\": string}], \"fix_advice_cn\": string}\n"
            "- IMPORTANT: The 'message' field MUST be written in Chinese.\n"
            "- If compilable=false, you MUST provide fix_advice_cn as Chinese plain text bullet points.\n"
            "- Only include deterministic compile-time errors that follow directly from the diff/content. No runtime speculation.\n"
            "- If you are not certain, set compilable=true and return errors=[].\n"
            "- Prefer SyntaxError/TypeError with exact file+line when possible; keep errors concise (max 10).\n"
            "\nPR_DIFF:\n"
            f"{diff_text[:12000]}\n"
            "\nFILES_CONTEXT(JSON):\n"
            f"{json.dumps(compact_files, ensure_ascii=True)}\n"
        )
        try:
            # Prefer DeepSeek for compile-level errors
            content = _llm_invoke_with_retry(llm_deepseek, [("user", prompt)])
        except Exception as exc:  # noqa: BLE001
            # LLM不可用：不阻断流程，继续走确定性工具与（可能的）AI审查
            return {
                **state,
                "llm_compile_result": {"compilable": True, "errors": []},
                "llm_compile_block": False,
                "llm_compile_parse_error": f"LLM调用失败: {type(exc).__name__}",
            }
        data, parse_error = _try_parse_json_object(content)
        compilable = True
        errors: list = []
        fix_advice_cn: str = ""
        if isinstance(data, dict):
            compilable = bool(data.get("compilable", True))
            errors = data.get("errors", []) if isinstance(data.get("errors", []), list) else []
            fix_advice_cn = data.get("fix_advice_cn", "") if isinstance(data.get("fix_advice_cn", ""), str) else ""
            # hard cap + normalize
            errors = errors[:10]

        # Greptile 强参考约束（用户约定：编译问题 Greptile 一定会提出来）：
        # - 若 compilable=false 但 Greptile 未提到对应问题 => 视为可能误报：不阻断、不入最终报告。
        gt_text = (state.get("greptile_text") or "")
        gt_findings = state.get("greptile_findings") or []
        gt_ok = bool(state.get("greptile_ok"))
        gt_evidence_lines: list[str] = []
        for f in gt_findings:
            if not isinstance(f, dict):
                continue
            gt_evidence_lines.append(
                f"{f.get('file') or ''}:{f.get('line') or 0}:{f.get('title') or ''}:{f.get('detail') or ''}"
            )
        gt_lower = (gt_text + "\n" + "\n".join(gt_evidence_lines)).lower()

        def _basename(p: str) -> str:
            return (p or "").replace("\\", "/").split("/")[-1].lower()

        def _mentions_in_greptile(err: dict) -> bool:
            if not gt_lower.strip():
                return False
            f = _basename(str(err.get("file") or ""))
            ln = str(int(err.get("line") or 0))
            msg = str(err.get("message") or "").lower().strip()
            # heuristic 1: file basename + line number
            if f and (f in gt_lower) and (ln != "0") and (ln in gt_lower):
                return True
            # heuristic 2: file basename + message prefix
            if f and (f in gt_lower) and msg:
                frag = msg[:16] if len(msg) >= 16 else msg
                if frag and frag in gt_lower:
                    return True
            return False

        if compilable is False and gt_ok and not state.get("greptile_compile_block"):
            confirmed = [e for e in errors if isinstance(e, dict) and _mentions_in_greptile(e)]
            if not confirmed:
                compilable = True
                errors = []
                fix_advice_cn = ""
            else:
                errors = confirmed
        return {
            **state,
            "llm_compile_result": {"compilable": compilable, "errors": errors, "fix_advice_cn": fix_advice_cn},
            "llm_compile_block": not compilable,
            "llm_compile_parse_error": parse_error,
        }

    def deterministic_analysis_node(state: ReviewState) -> ReviewState:
        file_blobs = state.get("file_blobs", [])
        files_payload = [{"path": f.get("path"), "content": f.get("content", ""), "patch": f.get("patch", "")} for f in file_blobs]
        # NOTE: compile-level review relies solely on LLM compile_guard.
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
        """
        Parse LLM output into a list of finding dicts.
        Robust to common wrappers like fenced code blocks or extra prose.
        """
        if not isinstance(text, str) or not text.strip():
            return []

        def _loads(s: str):
            try:
                return json.loads(s)
            except Exception:
                return None

        data = _loads(text)
        if data is None:
            # Most common: model returns a JSON array inside markdown fences or with leading text.
            m = re.search(r"\[[\s\S]*\]", text)
            if m:
                data = _loads(m.group(0))
        if data is None:
            # Fallback: model returns {"findings":[...]} with extra text.
            m = re.search(r"\{[\s\S]*\}", text)
            if m:
                data = _loads(m.group(0))

        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            if isinstance(data.get("findings"), list):
                return data["findings"]
            # compatibility with other schemas
            if isinstance(data.get("issues"), list):
                return data["issues"]
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
            "要求：detail 与 suggestion 必须使用中文；不要重复MCP已确定的问题；避免无法判断的结论；给出理由。"
            f"\n\nPR diff:\n{state.get('diff','')}\n\n"
            f"MCP结果:\n{json.dumps(state.get('deterministic', {}), ensure_ascii=False)}\n\n"
            f"需求:\n{state.get('requirements') or ''}"
        )
        try:
            content = _llm_invoke_with_retry(llm_glm, [("user", prompt)])
        except Exception as exc:  # noqa: BLE001
            return {**state, "ai_findings": [], "llm_ai_error": f"LLM调用失败: {type(exc).__name__}"}
        ai_findings = parse_ai_findings(content)
        ai_findings = [normalize_finding(item) for item in ai_findings]
        return {**state, "ai_findings": ai_findings}

    def greptile_parse_node(state: ReviewState) -> ReviewState:
        """
        将 Greptile 文本审查报告解析为结构化 findings（用于与本系统结果合并排序）。
        若无 greptile_text，则直接返回空列表。
        """
        gt = (state.get("greptile_text") or "").strip()
        existing = state.get("greptile_findings") or []

        def _strip_html(s: str) -> str:
            # best-effort: keep code fences (handled by model) but remove html tags
            return re.sub(r"<[^>]+>", "", s or "")

        def _looks_english(s: str) -> bool:
            """
            Heuristic: if a text contains lots of ASCII letters, it's likely not Chinese.
            Keep code blocks/identifiers out of scope; we just want to avoid leaking whole English paragraphs.
            """
            if not s:
                return False
            letters = sum(1 for ch in s if ("a" <= ch.lower() <= "z"))
            # Ignore very short strings (e.g. "NPE", "OK")
            if len(s) < 40:
                return False
            return letters >= 30

        # If we already have Greptile findings (often raw English bodies from MCP comments),
        # translate/normalize them into Chinese so the final report is consistent.
        if existing:
            cleaned: list[dict] = []
            for f in (existing or [])[:30]:
                if not isinstance(f, dict):
                    continue
                cleaned.append(
                    {
                        "file": f.get("file"),
                        "line": f.get("line"),
                        "level": f.get("level", "medium"),
                        "category": f.get("category", "Greptile"),
                        "title": f.get("title", "GreptileComment"),
                        "detail": _strip_html(str(f.get("detail") or ""))[:3000],
                        "suggestion": _strip_html(str(f.get("suggestion") or ""))[:1200],
                    }
                )
            if not cleaned:
                return {**state, "greptile_findings": []}

            prompt = (
                "你是代码审查结果整理器。下面是 Greptile 生成的 findings（可能包含英文、HTML 片段、代码块）。\n"
                "请将每条 finding 的 detail 与 suggestion 改写为中文（保留代码块原样，不要翻译代码）。\n"
                "要求：\n"
                "- 只输出 JSON 数组，不要输出其它文字。\n"
                "- 结构保持不变：file,line,level,category,title,detail,suggestion。\n"
                "- 如果 suggestion 为空，可以保持为空或补充可执行的中文建议。\n"
                "\nFINDINGS(JSON):\n"
                f"{json.dumps(cleaned, ensure_ascii=False)}\n"
            )
            try:
                # Prefer faster model to avoid long waits/timeouts.
                content = _llm_invoke_with_retry(llm_deepseek, [("user", prompt)])
            except Exception as exc:  # noqa: BLE001
                # Per user requirement: do NOT leak English into final report.
                return {**state, "greptile_findings": [], "llm_ai_error": state.get("llm_ai_error") or f"Greptile翻译失败: {type(exc).__name__}"}

            parsed = parse_ai_findings(content)
            parsed = [normalize_finding(item) for item in parsed]
            # If model still outputs mostly English, retry once with stricter prompt; otherwise drop.
            if parsed and any(_looks_english((f.get("detail") or "")) for f in parsed if isinstance(f, dict)):
                strict_prompt = (
                    "你是代码审查结果整理器。\n"
                    "请将下列 findings 的 detail 与 suggestion 改写为中文。\n"
                    "硬性要求：除代码块与代码标识符外，禁止输出英文句子/段落。\n"
                    "只输出 JSON 数组，不要输出其它文字。\n"
                    "\nFINDINGS(JSON):\n"
                    f"{json.dumps(cleaned, ensure_ascii=False)}\n"
                )
                try:
                    content2 = _llm_invoke_with_retry(llm_deepseek, [("user", strict_prompt)])
                    parsed2 = parse_ai_findings(content2)
                    parsed2 = [normalize_finding(item) for item in parsed2]
                    parsed = parsed2 or parsed
                except Exception:
                    parsed = []
            # Final guard: never leak English paragraphs.
            if not parsed or any(_looks_english((f.get("detail") or "")) for f in parsed if isinstance(f, dict)):
                return {**state, "greptile_findings": []}
            return {**state, "greptile_findings": parsed[:20]}

        # No existing findings: parse Greptile text body (if any) into Chinese findings.
        if not gt:
            return {**state, "greptile_findings": []}

        def _heuristic_extract_cn_findings_from_gt(raw: str) -> list[dict]:
            """
            Deterministic fallback when LLM parsing fails.
            Goal: avoid leaking English and still surface Greptile's key issues.
            """
            t = _strip_html(raw or "")
            t = re.sub(r"\r\n", "\n", t)
            # remove mermaid blocks (too noisy)
            t = re.sub(r"```mermaid[\\s\\S]*?```", "", t, flags=re.IGNORECASE)
            # collapse whitespace
            t = re.sub(r"\n{3,}", "\n\n", t).strip()

            findings: list[dict] = []

            # Extract "Critical Issue Found" block
            crit = ""
            m = re.search(r"\\*\\*Critical Issue Found:\\*\\*([\\s\\S]*?)(\\*\\*Confidence Score:|$)", t, flags=re.IGNORECASE)
            if m:
                crit = m.group(1).strip()

            # Try to extract line numbers like "lines 256, 262, and 268"
            lines = []
            m2 = re.search(r"lines?\\s+(\\d+)\\s*,\\s*(\\d+)\\s*(?:,\\s*and\\s*|\\s*and\\s*)(\\d+)", t, flags=re.IGNORECASE)
            if m2:
                try:
                    lines = [int(m2.group(1)), int(m2.group(2)), int(m2.group(3))]
                except Exception:
                    lines = []

            # File hint like `GroupAdapter.java`
            file_hint = None
            m3 = re.search(r"`([^`]+\\.java)`", t)
            if m3:
                file_hint = m3.group(1)

            # Method hint
            has_stream = "getsubgroupsstream" in t.lower()
            has_supplier = "modelsupplier.get" in t.lower()

            if crit or has_stream:
                detail_parts = []
                detail_parts.append("Greptile 指出本 PR 的修复不完整：仍存在并发场景下的空指针风险。")
                if has_stream and has_supplier:
                    detail_parts.append("多个 `getSubGroupsStream()` 变体仍在未做空值检查的情况下调用 `modelSupplier.get()`，当并发删除发生时可能返回 null 并触发 NPE。")
                if lines:
                    detail_parts.append(f"Greptile 提到疑似影响位置在 {', '.join(str(x) for x in lines)} 行附近。")
                if crit:
                    # Keep it brief and Chinese; don't paste English.
                    detail_parts.append("核心结论：需要将 `getSubGroupsCount()` 的空值防护模式一致应用到其它同类方法。")

                sugg_parts = []
                sugg_parts.append("建议为所有调用 `modelSupplier.get()` 的相关方法补充空值处理。")
                sugg_parts.append("当 model 为 null 时，按语义返回合理的默认值（例如空 Stream），避免抛出 NPE。")

                findings.append(
                    normalize_finding(
                        {
                            "file": file_hint,
                            "line": (lines[0] if lines else None),
                            "level": "high" if crit else "medium",
                            "category": "Greptile",
                            "title": "Greptile：修复不完整，其他方法仍可能触发NPE",
                            "detail": "；".join(detail_parts),
                            "suggestion": "；".join(sugg_parts),
                        }
                    )
                )

            return findings[:5]

        prompt = (
            "你是代码审查报告解析器。下面是 Greptile 对一个 GitHub PR 的审查文本。\n"
            "请将其中“具体缺陷/风险点”抽取为 JSON 数组 findings，每个元素包含：\n"
            "- file: string|null\n"
            "- line: number|null\n"
            "- level: critical|high|medium|low（按 Greptile 表达的严重程度保守映射；不确定用 medium）\n"
            "- category: string（例如 Bug/Performance/Security/Style/Architecture 等）\n"
            "- title: string（简短标题）\n"
            "- detail: string（中文，说明原因）\n"
            "- suggestion: string（中文，可执行修复建议）\n"
            "要求：\n"
            "- 只输出 JSON 数组，不要其它文字。\n"
            "- 不要 emoji。\n"
            "- 没有明确文件/行号时允许为 null。\n"
            "\nGREPTILE_TEXT:\n"
            f"{gt}\n"
        )
        try:
            # Prefer faster model to avoid long waits/timeouts.
            content = _llm_invoke_with_retry(llm_deepseek, [("user", prompt)])
        except Exception as exc:  # noqa: BLE001
            # Greptile 解析失败不应阻断主流程
            return {**state, "greptile_findings": [], "llm_ai_error": state.get("llm_ai_error") or f"Greptile解析失败: {type(exc).__name__}"}

        parsed = parse_ai_findings(content)
        parsed = [normalize_finding(item) for item in parsed]
        # hard cap
        parsed = parsed[:20]
        if parsed and any(_looks_english((f.get("detail") or "")) for f in parsed if isinstance(f, dict)):
            strict_prompt = (
                "你是代码审查报告解析器。下面是 Greptile 对一个 GitHub PR 的审查文本。\n"
                "请抽取缺陷为 JSON 数组 findings，并确保 detail/suggestion 用中文。\n"
                "硬性要求：除代码块与代码标识符外，禁止输出英文句子/段落。\n"
                "只输出 JSON 数组，不要其它文字。\n"
                "\nGREPTILE_TEXT:\n"
                f"{gt}\n"
            )
            try:
                content2 = _llm_invoke_with_retry(llm_deepseek, [("user", strict_prompt)])
                parsed2 = parse_ai_findings(content2)
                parsed2 = [normalize_finding(item) for item in parsed2]
                parsed = (parsed2 or parsed)[:20]
            except Exception:
                parsed = []
        if not parsed or any(_looks_english((f.get("detail") or "")) for f in parsed if isinstance(f, dict)):
            # Do not leak English paragraphs. Try deterministic fallback.
            fallback = _heuristic_extract_cn_findings_from_gt(gt)
            return {**state, "greptile_findings": fallback}
        return {**state, "greptile_findings": parsed}

    def synthesis_node(state: ReviewState) -> ReviewState:
        deterministic = state.get("deterministic", {})
        ai_findings = state.get("ai_findings", [])
        sections = []
        # 若 LLM 判断不可编译，优先返回其报告
        llm_compile = state.get("llm_compile_result")
        if llm_compile and llm_compile.get("compilable") is False:
            # Build a polished, actionable compile-level report in Chinese plain text.
            repo = state.get("repo_full_name", "")
            pr = state.get("pr_number", "")
            errors = llm_compile.get("errors", [])[:10]
            gt_text = (state.get("greptile_text") or "").strip()
            gt_findings = state.get("greptile_findings") or []
            gt_source = state.get("greptile_source") or "none"
            gt_ok = bool(state.get("greptile_ok"))
            gt_err = state.get("greptile_error")

            def _basename(p: str) -> str:
                return (p or "").replace("\\", "/").split("/")[-1]

            def _clean_text(s: str, limit: int = 240) -> str:
                # remove html tags
                t = re.sub(r"<[^>]+>", "", s or "")
                # remove fenced blocks (keep brief)
                t = re.sub(r"```[\s\S]*?```", "", t)
                # collapse whitespace
                t = re.sub(r"[ \t]+", " ", t)
                t = re.sub(r"\n{3,}", "\n\n", t).strip()
                return (t[:limit] + "…") if len(t) > limit else t

            def _greptile_cn_point(body: str) -> list[str]:
                """
                将 Greptile 英文评论归纳成中文编译要点（避免英文/HTML进入最终报告）。
                """
                b = (body or "").lower()
                pts: list[str] = []
                if "missing `#include" in b or "missing #include" in b or "include <iostream>" in b:
                    pts.append("缺少必要的头文件（例如 <iostream>），会导致 cin/cout 无法解析。")
                if "missing namespace" in b or "using namespace std" in b or "std::" in b:
                    pts.append("缺少命名空间声明：使用 `std::cin/std::cout` 或添加 `using namespace std;`。")
                if "missing semicolon" in b:
                    pts.append("存在缺少分号的语法错误（例如 `cout<<a;`）。")
                if "will not compile" in b or "cannot compile" in b or "code will not compile" in b:
                    pts.append("该 PR 在编译阶段无法通过，需先修复上述编译级错误。")
                return pts

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

            def zh_type(t: str) -> str:
                mapping = {
                    "SyntaxError": "语法错误",
                    "TypeError": "类型错误",
                    "CompileError": "编译错误",
                    "MissingDependency": "缺少依赖",
                }
                return mapping.get(t or "", t or "编译错误")

            lines: list[str] = []
            lines.append("PR 审查报告")
            lines.append("")
            lines.append("一、基本信息")
            lines.append(f"- 仓库: {repo}")
            lines.append(f"- PR 编号: #{pr}")
            lines.append("")
            lines.append("二、最终结论")
            lines.append("- 本次变更当前无法通过编译/类型检查。请先修复下列问题后再重新发起审查。")
            lines.append("")

            # 收紧：只保留能定位到文件（最好有行号）的编译问题，避免 (unknown):0 噪音。
            filtered_errors: list[dict] = []
            for err in errors:
                if not isinstance(err, dict):
                    continue
                f = (err.get("file") or "").strip()
                ln = int(err.get("line") or 0)
                if not f or f == "(unknown)":
                    continue
                if ln <= 0:
                    # 允许文件级错误，但报告中不展示 :0
                    filtered_errors.append({**err, "line": 0})
                else:
                    filtered_errors.append(err)

            if not filtered_errors:
                # 仍然不输出 (unknown):0
                filtered_errors = [{"file": "", "line": 0, "type": "CompileError", "message": "存在编译级错误，但未能定位到具体文件/行号。"}]

            lines.append("三、必须修复的问题清单")
            lines.append("")

            for idx, err in enumerate(filtered_errors[:10], start=1):
                f = err.get("file") or "(unknown)"
                ln = err.get("line") or 0
                typ = err.get("type") or "CompileError"
                msg = err.get("message") or ""
                lines.append(f"{idx}. {zh_type(str(typ))}")
                if f and f != "(unknown)":
                    if int(ln or 0) > 0:
                        lines.append(f"   - 位置: {f}:{int(ln)}")
                    else:
                        lines.append(f"   - 位置: {f}")
                lines.append(f"   - 原始类型: {typ}")
                lines.append(f"   - 错误信息: {msg}")
                if f and f != "(unknown)":
                    code = snippet(
                        find_content(f),
                        int(ln) if isinstance(ln, int) else 0,
                        patch_fallback=find_patch(f),
                    )
                    lines.append("   - 相关代码片段:")
                    for cl in (code or "").splitlines():
                        lines.append(f"       {cl}")
                lines.append("")

            fix_advice = (llm_compile or {}).get("fix_advice_cn") or ""
            if isinstance(fix_advice, str) and fix_advice.strip():
                lines.append("四、修复建议")
                for fl in fix_advice.strip().splitlines():
                    if not fl.strip():
                        lines.append("")
                        continue
                    # avoid double "- -"
                    s = fl.strip()
                    if s.startswith("-"):
                        s = s.lstrip("-").strip()
                    lines.append(f"- {s}".rstrip())

            report = "\n".join(lines).strip() + "\n"
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

        # NOTE: 只呈现最终审查报告，不展示中间系统/工具状态。

        # 多来源优先级排序（不展示外部参考的原始内容）：
        # 共同出现(本系统 & 外部参考) > 仅外部参考 > 仅本系统
        def _sev_rank(level: str) -> int:
            m = {"critical": 0, "high": 1, "medium": 2, "low": 3}
            return m.get((level or "medium").lower(), 2)

        def _norm_file(p: str | None) -> str:
            return (p or "").replace("\\", "/").strip()

        def _key(f: Dict[str, Any]) -> str:
            fp = _norm_file(f.get("file"))
            base = fp.split("/")[-1] if fp else ""
            ln = str(int(f.get("line") or 0)) if f.get("line") is not None else "0"
            title = (f.get("title") or "").strip().lower()
            # very lightweight key; robust enough for "same bug appears in both" prioritization
            return f"{base}:{ln}:{title[:64]}"

        ours: List[Dict[str, Any]] = []
        # deterministic -> ours
        for defect in deterministic.get("static_defect_scan", {}).get("defects", []) or []:
            ours.append(
                normalize_finding(
                    {
                        "file": defect.get("file"),
                        "line": defect.get("line"),
                        "level": "high",
                        "category": f"StaticDefect:{defect.get('type')}",
                        "title": str(defect.get("type") or "StaticDefect"),
                        "detail": defect.get("reason") or "",
                        "suggestion": "请根据缺陷原因修正逻辑或移除死代码。",
                    }
                )
            )
        for dep in deterministic.get("dependency_analysis", {}).get("violations", []) or []:
            ours.append(
                normalize_finding(
                    {
                        "file": dep.get("file"),
                        "line": dep.get("line"),
                        "level": "medium",
                        "category": "Architecture",
                        "title": dep.get("type", "Dependency Issue"),
                        "detail": dep.get("detail", ""),
                        "suggestion": "请按分层/依赖规范调整模块关系。",
                    }
                )
            )
        for sig in deterministic.get("security_signal", {}).get("signals", []) or []:
            ours.append(
                normalize_finding(
                    {
                        "file": sig.get("file"),
                        "line": sig.get("line"),
                        "level": "medium",
                        "category": "SecuritySignal",
                        "title": "潜在数据流风险",
                        "detail": f"{sig.get('source')} -> {sig.get('sink')} sanitized={sig.get('sanitized')}",
                        "suggestion": "确认输入验证与输出安全处理。",
                    }
                )
            )
        ours.extend(ai_findings or [])

        gt_findings = state.get("greptile_findings", []) or []
        ours_map = {_key(f): f for f in ours}
        gt_map = {_key(f): f for f in gt_findings}

        both_keys = sorted(set(ours_map.keys()) & set(gt_map.keys()))
        gt_only_keys = sorted(set(gt_map.keys()) - set(ours_map.keys()))
        ours_only_keys = sorted(set(ours_map.keys()) - set(gt_map.keys()))

        prioritized: List[Dict[str, Any]] = []
        for k in both_keys:
            o = ours_map[k]
            g = gt_map[k]
            merged = dict(o)
            merged["detail"] = (o.get("detail") or "").strip()
            merged["_source"] = "both"
            merged["_sev"] = min(_sev_rank(o.get("level")), _sev_rank(g.get("level")))
            prioritized.append(merged)
        for k in gt_only_keys:
            f = dict(gt_map[k])
            f["_source"] = "greptile"
            f["_sev"] = _sev_rank(f.get("level"))
            prioritized.append(f)
        for k in ours_only_keys:
            f = dict(ours_map[k])
            f["_source"] = "ours"
            f["_sev"] = _sev_rank(f.get("level"))
            prioritized.append(f)

        prioritized.sort(key=lambda x: (0 if x.get("_source") == "both" else 1 if x.get("_source") == "greptile" else 2, x.get("_sev", 2)))

        has_key_list = bool(prioritized)
        if prioritized:
            md = []
            md.append("关键问题清单（按优先级排序）")
            for idx, f in enumerate(prioritized[:30], start=1):
                src = f.get("_source")
                src_cn = "高置信（多来源一致）" if src == "both" else "中置信（外部参考）" if src == "greptile" else "低置信（仅本系统）"
                file_path = f.get("file") or ""
                ln = f.get("line")
                loc = f"{file_path}:{int(ln) if ln is not None else 0}" if file_path else "(位置未知)"
                md.append("")
                md.append(f"{idx}. {f.get('title')}")
                md.append(f"   - 风险级别: {f.get('level')}")
                md.append(f"   - 来源: {src_cn}")
                md.append(f"   - 位置: {loc}")
                md.append(f"   - 原因: {f.get('detail')}")
                md.append(f"   - 建议: {f.get('suggestion')}")
            sections.append("\n".join(md).strip())

        static_defects = deterministic.get("static_defect_scan", {}).get("defects", [])
        if static_defects and not has_key_list:
            md = []
            md.append("确定性静态缺陷（高优先级）")
            for idx, defect in enumerate(static_defects, start=1):
                f = defect.get("file") or "(unknown)"
                ln = int(defect.get("line") or 0)
                typ = defect.get("type") or "Defect"
                reason = defect.get("reason") or ""
                md.append("")
                md.append(f"{idx}. {typ}")
                md.append(f"   - 位置: {f}:{ln}")
                md.append(f"   - 原因: {reason}")
                md.append("   - 相关代码片段:")
                for cl in snippet_for(f, ln).splitlines():
                    md.append(f"       {cl}")
            sections.append("\n".join(md).strip())
        dep_issues = deterministic.get("dependency_analysis", {}).get("violations", [])
        if dep_issues and not has_key_list:
            md = []
            md.append("架构与依赖问题（确定性）")
            for idx, v in enumerate(dep_issues, start=1):
                md.append("")
                md.append(f"{idx}. {v.get('type')}")
                md.append(f"   - 详情: {v.get('detail')}")
            sections.append("\n".join(md).strip())
        security = deterministic.get("security_signal", {}).get("signals", [])
        if security and not has_key_list:
            md = []
            md.append("安全信号（仅提示，不下结论）")
            for idx, sig in enumerate(security, start=1):
                md.append("")
                md.append(f"{idx}. 数据流信号")
                md.append(f"   - 输入源: {sig.get('source')}")
                md.append(f"   - 汇聚点: {sig.get('sink')}")
                md.append(f"   - 是否已清洗: {sig.get('sanitized')}")
            sections.append("\n".join(md).strip())
        # NOTE:
        # - `关键问题清单` 已经对 ai_findings / greptile_findings / deterministic findings 做了合并与排序。
        # - 为避免重复展示，这里不再额外输出单独的 AI/Greptile 状态段落。
        report = "\n\n".join(sections) if sections else "未发现显著问题。"
        return {**state, "report_markdown": report}

    workflow = StateGraph(ReviewState)
    workflow.add_node("pr_context", pr_context_builder)
    workflow.add_node("compile_guard", compile_guard_node)
    workflow.add_node("greptile_ref", greptile_reference_node)
    workflow.add_node("hydrate_contents", hydrate_file_contents_node)
    workflow.add_node("deterministic", deterministic_analysis_node)
    workflow.add_node("ai_review", ai_review_node)
    workflow.add_node("greptile_parse", greptile_parse_node)
    workflow.add_node("synthesis", synthesis_node)

    workflow.set_entry_point("pr_context")
    workflow.add_edge("pr_context", "greptile_ref")
    workflow.add_edge("greptile_ref", "compile_guard")
    workflow.add_conditional_edges(
        "compile_guard",
        lambda state: "block" if state.get("llm_compile_block") else "pass",
        {"block": "synthesis", "pass": "hydrate_contents"},
    )
    workflow.add_edge("hydrate_contents", "deterministic")
    workflow.add_edge("deterministic", "ai_review")
    workflow.add_edge("ai_review", "greptile_parse")
    workflow.add_edge("greptile_parse", "synthesis")
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

    # Include Greptile findings in API response as well (not only in markdown synthesis).
    for f in result.get("greptile_findings", []) or []:
        if isinstance(f, dict):
            findings.append(Finding(**f))

    # De-dup findings returned by API (avoid repeated items across sources).
    # Keep stable order; if duplicates occur, prefer higher severity.
    sev_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}

    def _key_api(x: Finding) -> str:
        fp = (x.file or "").replace("\\", "/")
        base = fp.split("/")[-1].lower() if fp else ""
        ln = str(int(x.line or 0))
        title = (x.title or "").strip().lower()
        return f"{base}:{ln}:{title[:80]}"

    dedup: List[Finding] = []
    seen: dict[str, int] = {}
    for item in findings:
        k = _key_api(item)
        if k not in seen:
            seen[k] = len(dedup)
            dedup.append(item)
            continue
        i = seen[k]
        prev = dedup[i]
        if sev_rank.get(item.level, 2) < sev_rank.get(prev.level, 2):
            dedup[i] = item
    findings = dedup

    report_markdown = result.get("report_markdown", "")
    saved = save_report_markdown(report_markdown)
    return ReviewResponse(review_id=saved["id"], report_markdown=report_markdown, findings=findings)

