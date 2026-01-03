import asyncio
import time
import httpx
from app.config import Settings

async def rpc(client, url, headers, method, params=None, idv=None):
    payload = {"jsonrpc":"2.0","id": idv if idv is not None else int(time.time()*1000),"method": method}
    if params is not None:
        payload["params"] = params
    r = await client.post(url, headers=headers, json=payload)
    return r.status_code, (r.text or "")

async def main():
    s = Settings()
    url = s.greptile_mcp_url
    headers = {"Content-Type":"application/json","Authorization": f"Bearer {s.greptile_api_key}"}
    async with httpx.AsyncClient(timeout=25.0) as client:
        st, txt = await rpc(client, url, headers, "initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "clientInfo": {"name": "mvp6", "version": "0.1"}
        }, idv=1)
        print('initialize status=', st)
        print(txt[:400])

        st, txt = await rpc(client, url, headers, "notifications/initialized", {}, idv=None)
        print('initialized notify status=', st)
        print(txt[:200])

        st, txt = await rpc(client, url, headers, "tools/list", {}, idv=2)
        print('tools/list status=', st)
        print(txt[:400])

        st, txt = await rpc(client, url, headers, "tools/call", {"name":"list_custom_context","arguments": {"limit":1}}, idv=3)
        print('tools/call status=', st)
        print(txt[:500])

asyncio.run(main())
