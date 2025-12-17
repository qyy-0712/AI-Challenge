import asyncio
from app.github_client import GitHubClient
from app.config import Settings

async def main():
    s=Settings()
    gh=GitHubClient(token=s.github_token)
    diff=await gh.fetch_diff("qyy-0712/test", 2)
    files=await gh.fetch_pr_files_with_content("qyy-0712/test", 2)
    print("changed_files:", [f.get('path') for f in files])
    print("--- diff head ---")
    print(diff[:1200])
    for f in files:
        print("\n===", f.get('path'), "status=", f.get('status'))
        patch=f.get('patch') or ''
        print("patch_head:\n", patch[:800])
        content=f.get('content') or ''
        print("content_len=", len(content))

asyncio.run(main())
