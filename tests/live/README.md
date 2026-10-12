# Live tests (need a licensed JMAG-Designer)

These scripts start the MCP server over stdio and talk to a real JMAG. They are not collected
by pytest. Paths and PIDs are arguments; nothing here assumes a particular machine.

| Script | What it exercises |
|---|---|
| `mcp_client_flow.py <pid> <study>` | status, connect, pre-check, async run + polling, results, KVL/energy checks, images, typo feedback, journal |
| `cancel_flow2.py <project.jproj> <study>` | detached launch, long run, safe cancel protocol, recovery |
| `motor_flow.py <pid> <study> <rpm> <f_e>` | run a prepared machine study + power-balance check |
| `../../examples/spm_motor/run_mcp.py <work_dir>` | complete SPM motor build from geometry script to validated torque |

The self-test (`jmag_self_test` tool or `selftest.run_self_test`) is the quickest live check.
