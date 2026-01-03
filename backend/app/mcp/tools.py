from __future__ import annotations

import ast
import logging
import re
from typing import Dict, List, Optional

from ..config import Settings

logger = logging.getLogger(__name__)


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
class _PyDefectVisitor(ast.NodeVisitor):
    def __init__(self):
        self.defects: List[Dict] = []

    def _line(self, node: ast.AST) -> int:
        return int(getattr(node, "lineno", 0) or 0)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._scan_block_for_dead_code(node.body, node)
        self._scan_uninitialized_in_function(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._scan_block_for_dead_code(node.body, node)
        self._scan_uninitialized_in_function(node)
        self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp):
        # Divide by literal zero: /, //, %
        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and isinstance(node.right, ast.Constant):
            if node.right.value == 0:
                self.defects.append(
                    {
                        "type": "DivideByZero",
                        "file": self._file,
                        "line": self._line(node),
                        "confidence": "high",
                        "reason": "检测到字面量除以 0（编译/解释阶段可确定），将导致运行时报错",
                    }
                )
        self.generic_visit(node)

    def _scan_block_for_dead_code(self, stmts: List[ast.stmt], parent: ast.AST):
        terminated = False
        for st in stmts:
            if terminated:
                self.defects.append(
                    {
                        "type": "DeadCode",
                        "file": self._file,
                        "line": self._line(st),
                        "confidence": "high",
                        "reason": "检测到 return/raise/continue/break 之后仍存在同一代码块语句，属于不可达代码",
                    }
                )
                # continue scanning to flag more dead code lines
                continue
            if isinstance(st, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
                terminated = True

    def _scan_uninitialized_in_function(self, fn: ast.AST):
        # High-confidence uninitialized local usage in Python function scope:
        # if a name is loaded before first assignment in the same function, and it's not a parameter
        # (ignores global/nonlocal and comprehensions for simplicity).
        params = set()
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for a in fn.args.args + fn.args.kwonlyargs:
                params.add(a.arg)
            if fn.args.vararg:
                params.add(fn.args.vararg.arg)
            if fn.args.kwarg:
                params.add(fn.args.kwarg.arg)

        assigned = set()
        declared_global = set()
        declared_nonlocal = set()

        for st in fn.body:  # type: ignore[attr-defined]
            if isinstance(st, ast.Global):
                declared_global.update(st.names)
            if isinstance(st, ast.Nonlocal):
                declared_nonlocal.update(st.names)

        class _LocalWalk(ast.NodeVisitor):
            def __init__(self, outer: "_PyDefectVisitor"):
                self.outer = outer

            def visit_Name(self, n: ast.Name):
                if isinstance(n.ctx, ast.Load):
                    name = n.id
                    if name in params:
                        return
                    if name in declared_global or name in declared_nonlocal:
                        return
                    # Skip builtins-like common names (best-effort)
                    if name in {"True", "False", "None"}:
                        return
                    if name not in assigned:
                        self.outer.defects.append(
                            {
                                "type": "UninitializedVar",
                                "file": self.outer._file,
                                "line": self.outer._line(n),
                                "confidence": "high",
                                "reason": f"检测到局部变量 `{name}` 可能在赋值前被使用（函数作用域内可确定）",
                            }
                        )

            def visit_Assign(self, n: ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        assigned.add(t.id)
                self.generic_visit(n)

            def visit_AnnAssign(self, n: ast.AnnAssign):
                if isinstance(n.target, ast.Name):
                    assigned.add(n.target.id)
                self.generic_visit(n)

            def visit_AugAssign(self, n: ast.AugAssign):
                # x += 1 reads x before write; if x not assigned, flag as uninitialized too
                if isinstance(n.target, ast.Name):
                    name = n.target.id
                    if name not in assigned and name not in params:
                        self.outer.defects.append(
                            {
                                "type": "UninitializedVar",
                                "file": self.outer._file,
                                "line": self.outer._line(n),
                                "confidence": "high",
                                "reason": f"检测到 `{name} += ...` 可能在赋值前使用（aug-assign 读写同名变量）",
                            }
                        )
                    assigned.add(name)
                self.generic_visit(n)

        walker = _LocalWalk(self)
        for st in fn.body:  # type: ignore[attr-defined]
            walker.visit(st)


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
    # AST-based high-confidence checks
    try:
        tree = ast.parse(content or "", filename=path)
        v = _PyDefectVisitor()
        v._file = path  # attach for reporting
        v.visit(tree)
        defects.extend(v.defects)
    except Exception:
        # parsing failed: compile_guard should catch; keep static scan quiet here
        pass
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
    本地 MCP 实现：只做“确定性事实”收集（静态必然缺陷/依赖/安全信号）。
    编译级审查已由 LangGraph 中的 LLM compile_guard 节点负责。
    """

    def __init__(self, settings: Settings):
        self.settings = settings

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

