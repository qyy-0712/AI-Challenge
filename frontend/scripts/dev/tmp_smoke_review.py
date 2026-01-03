import asyncio
from app.graph.graph import run_review
from app.schemas import ReviewRequest
from app.config import Settings

async def main():
    req = ReviewRequest(repo_full_name="qyy-0712/test", pr_number=1, requirements=None)
    res = await run_review(req, Settings())
    print(res.review_id)
    print(res.report_markdown[:1200])

asyncio.run(main())
