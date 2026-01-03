import asyncio
import time
from app.config import Settings
from app.greptile_client import GreptileMCPClient

async def main():
    s = Settings()
    c = GreptileMCPClient(s)
    t0 = time.time()
    try:
        tools = await c.list_tools()
        dt = time.time() - t0
        print('tools_list_ok=', True, 'elapsed_s=', round(dt, 2), 'count=', len(tools))
        print('first_tools=', [t.get('name') for t in tools[:10]])
    except Exception as e:
        dt = time.time() - t0
        print('tools_list_ok=', False, 'elapsed_s=', round(dt, 2), 'err=', type(e).__name__, str(e)[:400])

asyncio.run(main())
