"""Fresh JMAG -> open project -> long run -> cancel (keep partial) -> verify. usage: cancel_flow2.py <project.jproj> <study>"""
import asyncio, json, sys
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def main(project, study):
    params = StdioServerParameters(command=sys.executable, args=["-m", "jmag_bridge"])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            async def call(name, args, n=700):
                res = await s.call_tool(name, args)
                text = res.content[0].text
                print(f"--- {name}\n{text[:n]}\n", flush=True)
                return json.loads(text)

            c = await call("jmag_connect", {"pid": None}, 300)
            await call("jmag_open_project", {"path": project}, 300)
            await call("jmag_set_step", {"study": study, "steps": 20001, "time_step": 0.0005}, 200)
            await call("jmag_run", {"study": study, "wait_seconds": 6, "check_first": False}, 400)
            await call("jmag_cancel_run", {"keep_partial_results": True})
            await call("jmag_run_status", {}, 1200)
            await call("jmag_dialogs", {}, 400)
            await call("jmag_summary", {}, 600)
            await call("jmag_set_step", {"study": study, "steps": 41, "time_step": 0.0005}, 200)
            await call("jmag_quit", {"save": False}, 200)


asyncio.run(main(sys.argv[1], sys.argv[2]))
