import asyncio
import time
import httpx
from app.config import Settings

async def post(payload, extra_headers=None):
    s = Settings()
    url = s.greptile_mcp_url
    headers = {"Content-Type":"application/json","Authorization": f"Bearer {s.greptile_api_key}", "Accept": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    async with httpx.AsyncClient(timeout=25.0) as client:
        r = await client.post(url, headers=headers, json=payload)
        return r.status_code, (r.text or "")

async def main():
    base = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "list_custom_context", "arguments": {"limit": 1}}
    }

    cases = []
    cases.append(("id_int", base))
    b2 = dict(base); b2["id"] = "1"; cases.append(("id_str", b2))
    b3 = dict(base); b3.pop("jsonrpc", None); cases.append(("no_jsonrpc", b3))
    b4 = dict(base); b4["params"] = [{"name":"list_custom_context","arguments": {"limit":1}}]; cases.append(("params_array", b4))
    b5 = {"method":"tools/call","params": {"name":"list_custom_context","arguments": {"limit":1}}}; cases.append(("bare_method", b5))

    for name, payload in cases:
        st, txt = await post(payload)
        print("===", name, "status=", st)
        print(txt[:200])

asyncio.run(main())
