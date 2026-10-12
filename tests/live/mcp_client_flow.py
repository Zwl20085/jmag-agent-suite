"""Drive the bridge over a real MCP stdio session (needs a licensed JMAG).

usage: python tests/live/mcp_client_flow.py <jmag_pid> <study>
"""
import asyncio
import json
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def show(name, res, limit=1200):
    kinds = [c.type for c in res.content]
    text = next((c.text for c in res.content if c.type == "text"), "")
    print(f"--- {name} isError={getattr(res, 'is_error', None)} content={kinds}\n{text[:limit]}\n", flush=True)
    try:
        return json.loads(text)
    except ValueError:
        return None


async def main(pid: int, study: str) -> None:
    params = StdioServerParameters(command=sys.executable, args=["-m", "jmag_bridge"])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            print("tools:", len(tools.tools))
            show("status", await s.call_tool("jmag_status", {}), 600)
            show("connect", await s.call_tool("jmag_connect", {"pid": pid}), 500)
            show("check pre", await s.call_tool("jmag_check", {"study": study, "stage": "pre", "electrical_frequency_hz": 50}))
            show("run", await s.call_tool("jmag_run", {"study": study, "wait_seconds": 0.5, "electrical_frequency_hz": 50}), 800)
            for _ in range(60):
                st = show("run_status", await s.call_tool("jmag_run_status", {}), 600)
                if st and st.get("state") != "running":
                    break
                await asyncio.sleep(1)
            show("results", await s.call_tool("jmag_list_results", {"study": study}), 800)
            show("get_result", await s.call_tool("jmag_get_result", {"study": study, "table": "Coil Flux-Linkage",
                                                                     "electrical_frequency_hz": 50, "max_points": 5}), 1500)
            show("kvl", await s.call_tool("jmag_physics_check", {"study": study, "check": "kvl", "coil": "Coil1",
                                                                 "probe": "VP_Coil1", "resistance": 0.1}))
            show("energy", await s.call_tool("jmag_physics_check", {"study": study, "check": "energy"}))
            show("contour", await s.call_tool("jmag_contour_image", {"study": study, "step": 11}), 400)
            show("circuit img", await s.call_tool("jmag_circuit_image", {"study": study}), 300)
            show("typo", await s.call_tool("jmag_set_properties", {"study": study, "values": {"ModelThikness": 40}}), 600)
            show("api", await s.call_tool("jmag_api_lookup", {"query": "Study.CreateCondition"}), 500)
            show("dialogs", await s.call_tool("jmag_dialogs", {}), 300)
            show("journal", await s.call_tool("jmag_journal", {"export_script": "replay_test.py"}), 500)


asyncio.run(main(int(sys.argv[1]), sys.argv[2]))
