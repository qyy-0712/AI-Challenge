import asyncio
import time
from app.config import Settings
from app.github_client import GitHubClient
from app.greptile_client import GreptileMCPClient

async def main():
    s = Settings()
    gh = GitHubClient(token=s.github_token)
    default_branch = await gh.fetch_repo_default_branch('qyy-0712/test')
    gt = GreptileMCPClient(s)
    t0 = time.time()
    try:
        body, comments = await gt.get_pr_review_bundle(name='qyy-0712/test', default_branch=default_branch, pr_number=3, remote='github', poll_timeout_s=8.0)
        print('elapsed_s=', round(time.time()-t0,2))
        print('body_len=', len(body or ''))
        print('comments_count=', len(comments or []))
        if comments:
            c0 = comments[0]
            print('first_comment_keys=', sorted(list(c0.keys()))[:12])
    except Exception as e:
        print('err=', type(e).__name__, str(e)[:400])

asyncio.run(main())
