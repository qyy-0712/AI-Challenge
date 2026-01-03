import asyncio
import json
from app.config import Settings
from app.graph.graph import build_graph

async def main():
    settings = Settings()
    graph = build_graph(settings, token=settings.github_token)
    state = await graph.ainvoke({"repo_full_name":"qyy-0712/test","pr_number":3,"requirements":None})
    print("llm_compile_result=", state.get("llm_compile_result"))
    print("llm_compile_parse_error=", state.get("llm_compile_parse_error"))
    print("deterministic_keys=", list((state.get("deterministic") or {}).keys()))
    print("static_defects_count=", len(((state.get("deterministic") or {}).get("static_defect_scan") or {}).get("defects", [])))
    print("dep_violations_count=", len(((state.get("deterministic") or {}).get("dependency_analysis") or {}).get("violations", [])))
    print("security_signals_count=", len(((state.get("deterministic") or {}).get("security_signal") or {}).get("signals", [])))
    print("ai_findings_count=", len(state.get("ai_findings") or []))
    print("--- report ---")
    print(state.get("report_markdown",""))

asyncio.run(main())
