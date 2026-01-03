import asyncio
import time
import httpx
from app.config import Settings
from app.github_client import GitHubClient

async def main():
    s = Settings()
    gh = GitHubClient(token=s.github_token)
    default_branch = await gh.fetch_repo_default_branch('qyy-0712/test')
    url = s.greptile_mcp_url
    headers = {"Content-Type":"application/json","Authorization": f"Bearer {s.greptile_api_key}", "Accept":"application/json"}

    payload = {
        "jsonrpc":"2.0",
        "id": int(time.time()*1000),
        "method":"tools/call",
        "params": {
            "name":"list_merge_request_comments",
            "arguments": {
                "name":"qyy-0712/test",
                "remote":"github",
                "defaultBranch": default_branch,
                "prNumber": 3,
                "greptileGenerated": True,
                "addressed": False
            }
        }
    }

    async with httpx.AsyncClient(timeout=25.0) as client:
        r = await client.post(url, headers=headers, json=payload)
        print('default_branch=', default_branch)
        print('status=', r.status_code)
        print((r.text or '')[:800])

asyncio.run(main())
