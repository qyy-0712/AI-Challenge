import asyncio
import time
from app.config import Settings
from app.schemas import ReviewRequest
from app.graph.graph import run_review

async def main():
    s = Settings()
    t0 = time.time()
    res = await run_review(ReviewRequest(repo_full_name="qyy-0712/test", pr_number=3, requirements=None), s, token=s.github_token)
    dt = time.time() - t0
    print("glm_model=", s.llm_model)
    print("deepseek_model=", s.deepseek_model)
    print("elapsed_s=", round(dt, 2))
    print("review_id=", res.review_id)
    print("findings_count=", len(res.findings))
    print("--- report_head ---")
    print(res.report_markdown[:900])

asyncio.run(main())
