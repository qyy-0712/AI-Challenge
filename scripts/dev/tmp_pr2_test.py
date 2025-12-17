import asyncio
import json
from app.graph.graph import run_review
from app.schemas import ReviewRequest
from app.config import Settings

async def main():
    req = ReviewRequest(repo_full_name="qyy-0712/test", pr_number=2, requirements=None)
    res = await run_review(req, Settings())
    print("review_id=", res.review_id)
    print("findings_count=", len(res.findings))
    print(json.dumps([f.model_dump() for f in res.findings], ensure_ascii=False, indent=2))
    print("--- report_head ---")
    print(res.report_markdown[:800])

asyncio.run(main())
