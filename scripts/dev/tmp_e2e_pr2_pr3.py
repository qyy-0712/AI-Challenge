import asyncio
import time
from app.config import Settings
from app.schemas import ReviewRequest
from app.graph.graph import run_review

async def one(pr):
    s = Settings()
    t0 = time.time()
    res = await run_review(ReviewRequest(repo_full_name="qyy-0712/test", pr_number=pr, requirements=None), s, token=s.github_token)
    dt = time.time() - t0
    print("PR", pr, "elapsed_s=", round(dt, 2), "findings=", len(res.findings), "review_id=", res.review_id)
    print(res.report_markdown[:900])
    print("---")

async def main():
    await one(3)
    await one(2)

asyncio.run(main())
