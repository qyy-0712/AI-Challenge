import asyncio
import time
import httpx
from app.config import Settings

async def call(url):
    s = Settings()
    headers = {"Content-Type":"application/json","Authorization": f"Bearer {s.greptile_api_key}"}
    payload = {"jsonrpc":"2.0","id": int(time.time()*1000),"method":"tools/call","params": {"name":"list_custom_context","arguments": {}}}
    async with httpx.AsyncClient(timeout=25.0) as client:
        r = await client.post(url, headers=headers, json=payload)
        print("url=", url, "status=", r.status_code)
        print((r.text or "")[:300])

async def main():
    s = Settings()
    await call(s.greptile_mcp_url)
    await call(s.greptile_mcp_url.rstrip("/") + "/")

asyncio.run(main())
