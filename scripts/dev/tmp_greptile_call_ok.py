import asyncio
import time
import httpx
from app.config import Settings

async def main():
    s = Settings()
    url = s.greptile_mcp_url
    headers = {"Content-Type":"application/json","Authorization": f"Bearer {s.greptile_api_key}", "Accept":"application/json"}

    payload = {
        "jsonrpc":"2.0",
        "id": int(time.time()*1000),
        "method":"tools/call",
        "params": {"name":"list_custom_context", "arguments": {"limit": 1}}
    }

    async with httpx.AsyncClient(timeout=25.0) as client:
        r = await client.post(url, headers=headers, json=payload)
        print('status=', r.status_code)
        print((r.text or '')[:500])

asyncio.run(main())
