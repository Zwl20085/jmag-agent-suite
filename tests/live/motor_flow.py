"""Run a prepared motor study over MCP and validate it. usage: motor_flow.py <pid> <study> <rpm> <f_e>"""
import asyncio, json, sys
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def main(pid, study, rpm, fe):
    async with stdio_client(StdioServerParameters(command=sys.executable, args=["-m", "jmag_bridge"])) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            async def call(name, args, n=1500):
                res = await s.call_tool(name, args)
                text = res.content[0].text
                print(f"--- {name}\n{text[:n]}\n", flush=True)
                try:
                    return json.loads(text)
                except ValueError:
                    return {}

            await call("jmag_connect", {"pid": pid}, 100)
            await call("jmag_check", {"study": study, "stage": "pre", "electrical_frequency_hz": fe}, 800)
            st = await call("jmag_run", {"study": study, "wait_seconds": 600, "electrical_frequency_hz": fe}, 4000)
            while st.get("state") == "running":
                await asyncio.sleep(5)
                st = await call("jmag_run_status", {}, 600)
            await call("jmag_list_results", {"study": study}, 2500)
            await call("jmag_get_result", {"study": study, "table": "Torque", "electrical_frequency_hz": fe, "max_points": 3}, 1500)
            await call("jmag_physics_check", {"study": study, "check": "power_balance", "speed_rpm": rpm,
                                              "electrical_frequency_hz": fe}, 1000)
            await call("jmag_contour_image", {"study": study}, 400)


asyncio.run(main(int(sys.argv[1]), sys.argv[2], float(sys.argv[3]), float(sys.argv[4])))
