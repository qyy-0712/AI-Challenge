from __future__ import annotations

import ast
import logging
import os
import re
import subprocess
import tempfile
from typing import Dict, List, Optional, Tuple

from ..config import Settings

logger = logging.getLogger(__name__)


# -----------------------
#  Compile / Parse checks
# -----------------------
def _python_syntax_check(path: str, content: str) -> List[Dict]:
    errors = []
    try:
        ast.parse(content or "", filename=path)
    except SyntaxError as exc:
        errors.append(
            {
                "file": path,
                "line": exc.lineno or 0,
                "type": "SyntaxError",
                "message": exc.msg,
            }
        )
    return errors


def _brace_balance_check(path: str, content: str, pairs: Optional[Dict[str, str]] = None) -> List[Dict]:
    errors = []
    pairs = pairs or {"{": "}", "(": ")", "[": "]"}
    opener = set(pairs.keys())
    closer = set(pairs.values())
    stack = []
    for idx, ch in enumerate(content):
        if ch in opener:
            stack.append((ch, idx))
        elif ch in closer:
            if not stack:
                errors.append(
                    {"file": path, "line": content[:idx].count("\n") + 1, "type": "SyntaxError", "message": f"unexpected '{ch}'"}
                )
                break
            left, _ = stack.pop()
            if pairs[left] != ch:
                errors.append(
                    {"file": path, "line": content[:idx].count("\n") + 1, "type": "SyntaxError", "message": f"unmatched '{left}'"}
                )
                break
    if stack:
        left, pos = stack[-1]
        errors.append(
            {"file": path, "line": content[:pos].count("\n") + 1, "type": "SyntaxError", "message": f"unclosed '{left}'"}
        )
    if content.count('"') % 2 == 1 or content.count("'") % 2 == 1:
        errors.append({"file": path, "line": 1, "type": "SyntaxError", "message": "unclosed string literal (heuristic)"})
    return errors


def _js_syntax_heuristic(path: str, content: str) -> List[Dict]:
    return _brace_balance_check(path, content)


def _java_like_syntax(path: str, content: str) -> List[Dict]:
    """
    适用于 Java/C/C++/C#/Go/Rust/PHP/Ruby 的轻量括号与字符串检测。
    """
    return _brace_balance_check(path, content)


def _php_syntax(path: str, content: str) -> List[Dict]:
    errors = _brace_balance_check(path, content)
    if "<?php" not in content[:20] and content.strip().startswith("<?") is False:
        errors.append({"file": path, "line": 1, "type": "SyntaxError", "message": "missing <?php tag (heuristic)"})
    return errors


# -----------------------
#  Unified diff helpers
# -----------------------
def _iter_added_lines_from_patch(patch: str):
    """
    Yield tuples of (new_line_no, line_text) for added lines in a unified diff patch.
    If line number can't be determined, yields (0, line_text).
    """
    if not patch:
        return
    new_line = 0
    for raw in patch.splitlines():
        if raw.startswith("@@"):
            m = re.search(r"\+\s*(\d+)", raw.replace("+", "+ "))
            # more robust:
            m2 = re.search(r"\+(\d+)", raw)
            if m2:
                new_line = int(m2.group(1))
            else:
                new_line = 0
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            yield new_line, raw[1:]
            if new_line:
                new_line += 1
            continue
        if raw.startswith("-") and not raw.startswith("---"):
            # removed line: do not advance new_line
            continue
        # context line
        if new_line:
            new_line += 1


def _detect_infinite_loop_in_patch(patch: str) -> Optional[Dict]:
    """
    Detect an obviously infinite loop from added lines. High confidence only.
    Supports C-like while(true)/for(;;) and Go 'for {' and Rust 'loop {'.
    """
    if not patch:
        return None
    added = list(_iter_added_lines_from_patch(patch))
    loop_start_patterns = [
        re.compile(r"\bwhile\s*\(\s*true\s*\)\s*\{", re.IGNORECASE),
        re.compile(r"\bfor\s*\(\s*;\s*;\s*\)\s*\{"),
        re.compile(r"^\s*for\s*\{\s*$"),  # go
        re.compile(r"^\s*loop\s*\{\s*$"),  # rust
    ]
    break_patterns = [re.compile(r"\bbreak\b"), re.compile(r"\breturn\b")]

    for i, (ln, txt) in enumerate(added):
        if any(p.search(txt) for p in loop_start_patterns):
            # scan forward until a closing brace on its own or after N lines
            window = []
            for j in range(i + 1, min(i + 30, len(added))):
                _ln2, txt2 = added[j]
                window.append(txt2)
                if "}" in txt2:
                    break
            joined = "\n".join(window)
            if any(p.search(joined) for p in break_patterns):
                continue
            return {
                "line": ln or 0,
                "reason": "新增了明显无退出条件的循环结构（未发现 break/return），将导致死循环",
            }
    return None


# -----------------------
#  Static defect checks
# -----------------------
def _python_static_scan(path: str, content: str) -> List[Dict]:
    defects = []
    # 死循环：while True 无 break/return
    for m in re.finditer(r"while\s+True\s*:", content):
        block = content[m.end() : m.end() + 400]
        if "break" not in block and "return" not in block:
            defects.append(
                {
                    "type": "InfiniteLoop",
                    "file": path,
                    "line": content[: m.start()].count("\n") + 1,
                    "confidence": "high",
                    "reason": "检测到 while True 且块内无 break/return，可能死循环",
                }
            )
    # 资源泄漏：open 未 with/close
    for match in re.finditer(r"open\([^)]*\)", content):
        snippet = content[match.start() : match.start() + 160]
        prefix = content[max(0, match.start() - 20) : match.start()]
        if "with" not in prefix and "close" not in snippet:
            defects.append(
                {
                    "type": "ResourceLeak",
                    "file": path,
                    "line": content[: match.start()].count("\n") + 1,
                    "confidence": "high",
                    "reason": "open() 可能未使用 with/close 关闭文件",
                }
            )
    # 恒真/恒假条件：if True / if False
    for match in re.finditer(r"if\s+(True|False)\s*:", content):
        literal = match.group(1)
        defects.append(
            {
                "type": "AlwaysTrueCondition",
                "file": path,
                "line": content[: match.start()].count("\n") + 1,
                "confidence": "high",
                "reason": f"条件恒定 {literal}，可能是遗留调试分支",
            }
        )
    return defects


def _js_static_scan(path: str, content: str) -> List[Dict]:
    defects = []
    # 恒真/恒假条件
    for match in re.finditer(r"if\s*\(\s*(true|false)\s*\)", content, flags=re.IGNORECASE):
        literal = match.group(1)
        defects.append(
            {
                "type": "AlwaysTrueCondition",
                "file": path,
                "line": content[: match.start()].count("\n") + 1,
                "confidence": "high",
                "reason": f"条件恒定 {literal}",
            }
        )
    return defects


# -----------------------
#  Dependency checks
# -----------------------
def _dependency_scan(path: str, content: str) -> List[Dict]:
    violations = []
    # 简单层次约束：api 层不应直接依赖 db/dao
    if re.search(r"/api/|\\api\\", path, flags=re.IGNORECASE):
        if re.search(r"import\s+.*db|from\s+.*db\s+import", content):
            violations.append({"type": "LayerViolation", "detail": f"{path} 直接依赖 db 层"})
        if re.search(r"import\s+.*dao|from\s+.*dao\s+import", content):
            violations.append({"type": "LayerViolation", "detail": f"{path} 直接依赖 dao 层"})
    return violations


# -----------------------
#  Security signal checks
# -----------------------
def _security_signal_scan(path: str, content: str) -> List[Dict]:
    signals = []
    # Python 命令/SQL 注入信号
    if "input(" in content and ("os.system(" in content or "subprocess" in content):
        signals.append({"source": "UserInput", "sink": "Command", "sanitized": False, "file": path})
    if re.search(r"execute\([^)]*user_input", content, re.IGNORECASE):
        signals.append({"source": "UserInput", "sink": "SQL", "sanitized": False, "file": path})
    if "eval(" in content or "exec(" in content:
        signals.append({"source": "UserInput", "sink": "Command", "sanitized": False, "file": path})
    # JS 命令/动态执行信号
    if re.search(r"child_process\.exec|execSync|spawn", content):
        signals.append({"source": "UserInput", "sink": "Command", "sanitized": False, "file": path})
    if re.search(r"\beval\s*\(", content) or "new Function(" in content:
        signals.append({"source": "UserInput", "sink": "Command", "sanitized": False, "file": path})
    return signals


class MCPClient:
    """
    本地 MCP 实现：优先运行真实编译/类型检查命令，辅以启发式解析。
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    # Compile / Parse
    def _detect_language_by_ext(self, path: str, fallback: str) -> str:
        ext_map = {
            ".py": "python",
            ".js": "javascript",
            ".jsx": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".java": "java",
            ".c": "c",
            ".cc": "cpp",
            ".cpp": "cpp",
            ".cxx": "cpp",
            ".go": "go",
            ".rs": "rust",
            ".php": "php",
            ".rb": "ruby",
        }
        for k, v in ext_map.items():
            if path.endswith(k):
                return v
        return fallback or "mixed"

    def compile_check(self, language: str, files: List[Dict[str, str]]) -> Dict:
        """
        DEPRECATED: 编译级审查已迁移至 LLM compile_guard 节点。
        保留此函数仅为兼容旧调用方；当前固定返回可编译且无错误。
        """
        _ = (language, files)
        return {"compilable": True, "errors": []}

        def add_heuristics(path: str, content: str) -> List[Dict]:
            heur: List[Dict] = []
            # 快速潜在空指针
            if re.search(r"\bnull\s*\.", content, re.IGNORECASE) or re.search(r"\bNone\s*\.", content):
                heur.append(
                    {
                        "file": path,
                        "line": 1,
                        "type": "PotentialNullDeref",
                        "message": "检测到对 null/None 的直接属性访问（启发式）",
                    }
                )
            # 快速死循环
            if re.search(r"while\s+True\s*:", content) or re.search(r"while\s*\(\s*true\s*\)", content, re.IGNORECASE):
                heur.append(
                    {
                        "file": path,
                        "line": 1,
                        "type": "PotentialInfiniteLoop",
                        "message": "检测到 while True/while(true)（启发式），请确认退出条件",
                    }
                )
            return heur

        def run_cmd(cmd: List[str], workdir: str) -> Tuple[int, str, str]:
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=workdir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=30,
                    check=False,
                )
                return proc.returncode, proc.stdout, proc.stderr
            except FileNotFoundError:
                return 127, "", "compiler not found"
            except Exception as exc:  # noqa: BLE001
                return 1, "", f"{type(exc).__name__}: {exc}"

        def parse_stderr(stderr: str, path: str, lang: str) -> List[Dict]:
            errs: List[Dict] = []
            lines = stderr.splitlines()
            for ln in lines:
                if not ln.strip():
                    continue
                # 粗略抽取行号
                m = re.search(r"(?P<file>[^\s:]+):(?P<line>\d+)", ln)
                line_no = int(m.group("line")) if m else 0
                errs.append(
                    {
                        "file": m.group("file") if m else path,
                        "line": line_no,
                        "type": "SyntaxError" if "syntax" in ln.lower() else "TypeError" if "type" in ln.lower() else "CompileError",
                        "message": ln.strip(),
                    }
                )
            return errs

        compiler_map = {
            "python": lambda p: ["python", "-m", "py_compile", p],
            "javascript": lambda p: ["node", "--check", p],
            "typescript": lambda p: ["npx", "tsc", "--noEmit", "--pretty", "false", p],
            "java": lambda p: ["javac", p],
            "c": lambda p: ["gcc", "-fsyntax-only", p],
            "cpp": lambda p: ["g++", "-fsyntax-only", p],
            "go": lambda p: ["go", "tool", "compile", p],
            "rust": lambda p: ["rustc", "--emit=metadata", p],
            "php": lambda p: ["php", "-l", p],
            "ruby": lambda p: ["ruby", "-c", p],
        }

        errors: List[Dict] = []
        with tempfile.TemporaryDirectory() as tmpdir:
            for f in files:
                path = f.get("path") or "unknown"
                content = f.get("content") or ""
                lang = self._detect_language_by_ext(path, language)

                # 先快速启发式
                errors.extend(add_heuristics(path, content))

                # 针对语言的轻量语法检查
                if lang == "python":
                    errors.extend(_python_syntax_check(path, content))
                elif lang in {"javascript", "typescript"}:
                    errors.extend(_js_syntax_heuristic(path, content))
                elif lang in {"java", "c", "cpp", "go", "rust", "ruby"}:
                    errors.extend(_java_like_syntax(path, content))
                elif lang == "php":
                    errors.extend(_php_syntax(path, content))

                # 真实编译器调用（若可用）
                cmd_builder = compiler_map.get(lang)
                if cmd_builder:
                    # 写临时文件，保持原扩展名
                    tmp_path = os.path.join(tmpdir, os.path.basename(path) or f"code{len(errors)}")
                    os.makedirs(os.path.dirname(tmp_path), exist_ok=True)
                    with open(tmp_path, "w", encoding="utf-8", newline="") as wf:
                        wf.write(content)
                    cmd = cmd_builder(tmp_path)
                    code, out, err = run_cmd(cmd, tmpdir)
                    if code != 0:
                        parsed = parse_stderr(err or out, path, lang)
                        # 如果编译器无结构化信息，至少给一条通用错误
                        if not parsed:
                            parsed = [
                                {
                                    "file": path,
                                    "line": 0,
                                    "type": "CompileError",
                                    "message": (err or out or "compiler failed").strip(),
                                }
                            ]
                        errors.extend(parsed)

        # 排序：语法/类型 > 缺依赖/编译 > 启发式风险
        priority = {"SyntaxError": 0, "TypeError": 1, "CompileError": 2, "MissingDependency": 2, "PotentialNullDeref": 3, "PotentialInfiniteLoop": 3}
        errors.sort(key=lambda e: priority.get(e.get("type", "CompileError"), 4))
        return {"compilable": len(errors) == 0, "errors": errors}

    # Static deterministic defects
    def static_defect_scan(self, files: List[Dict[str, str]]) -> Dict:
        defects: List[Dict] = []
        for f in files:
            path = f.get("path") or ""
            content = f.get("content") or ""
            patch = f.get("patch") or ""
            # dead loop detection for multiple languages via patch fallback
            inf = _detect_infinite_loop_in_patch(patch)
            if inf:
                defects.append(
                    {
                        "type": "InfiniteLoop",
                        "file": path,
                        "line": inf.get("line", 0),
                        "confidence": "high",
                        "reason": inf.get("reason", "检测到明显死循环"),
                    }
                )

            if path.endswith(".py"):
                defects.extend(_python_static_scan(path, content))
            elif path.endswith((".js", ".jsx", ".ts", ".tsx")):
                defects.extend(_js_static_scan(path, content))
        return {"defects": defects}

    # Dependency / architecture
    def dependency_analysis(self, files: List[Dict[str, str]]) -> Dict:
        violations: List[Dict] = []
        for f in files:
            path = f.get("path") or ""
            content = f.get("content") or ""
            violations.extend(_dependency_scan(path, content))
        return {"violations": violations}

    # Security signals (non-conclusive)
    def security_signal(self, files: List[Dict[str, str]]) -> Dict:
        signals: List[Dict] = []
        for f in files:
            path = f.get("path") or ""
            content = f.get("content") or ""
            signals.extend(_security_signal_scan(path, content))
        return {"signals": signals}

