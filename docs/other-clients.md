# Using the server outside Claude Code

The server is a standard MCP server over stdio. Any client that can start a command can use it. Windows only.

Command:

```text
<venv>\Scripts\python.exe -m jmag_bridge
```

Environment (optional): `JMAG_HOME`, the JMAG installation folder. Leave it out to auto-detect. The other variables
(`JMAG_PYTHON`, `JMAG_BRIDGE_HOME`) are in the README.

Install the package first. `.\scripts\install.ps1 -NoMcp` creates `.venv` inside the repository, installs the
package with plotting support and copies the skills, without needing the Claude Code CLI or registering anything
with it. It finishes with a doctor run; `<venv>\Scripts\python.exe -m jmag_bridge --doctor` repeats it any time.

## JSON backslash escaping

In JSON every backslash must be doubled: `C:\path\to\python.exe` is written `C:\\path\\to\\python.exe`. The blocks
below use the placeholder `C:\\path\\to\\jmag-agent-suite\\.venv\\Scripts\\python.exe`; replace it with your own
path. TOML is different: use single quotes and no escaping.

## Claude Desktop

File `claude_desktop_config.json` (usual location `%APPDATA%\Claude`; check your client's docs). Restart the app
afterwards.

```json
{
  "mcpServers": {
    "jmag": {
      "command": "C:\\path\\to\\jmag-agent-suite\\.venv\\Scripts\\python.exe",
      "args": ["-m", "jmag_bridge"],
      "env": { "JMAG_HOME": "C:\\Program Files\\JMAG-Designer23.0" }
    }
  }
}
```

## Cursor

File `.cursor/mcp.json` in the project (a user-level file also exists; check your client's docs).

```json
{
  "mcpServers": {
    "jmag": {
      "command": "C:\\path\\to\\jmag-agent-suite\\.venv\\Scripts\\python.exe",
      "args": ["-m", "jmag_bridge"]
    }
  }
}
```

## VS Code

File `.vscode/mcp.json`. The top-level key is `servers`, not `mcpServers` (check your client's docs for the current
schema).

```json
{
  "servers": {
    "jmag": {
      "type": "stdio",
      "command": "C:\\path\\to\\jmag-agent-suite\\.venv\\Scripts\\python.exe",
      "args": ["-m", "jmag_bridge"]
    }
  }
}
```

## Codex CLI

File `~/.codex/config.toml` (check your client's docs).

```toml
[mcp_servers.jmag]
command = 'C:\path\to\jmag-agent-suite\.venv\Scripts\python.exe'
args = ["-m", "jmag_bridge"]

[mcp_servers.jmag.env]
JMAG_HOME = 'C:\Program Files\JMAG-Designer23.0'
```

## Windsurf

File `mcp_config.json`, usually under `~/.codeium/windsurf/` (check your client's docs). Same shape as Claude
Desktop.

```json
{
  "mcpServers": {
    "jmag": {
      "command": "C:\\path\\to\\jmag-agent-suite\\.venv\\Scripts\\python.exe",
      "args": ["-m", "jmag_bridge"]
    }
  }
}
```

## Any other MCP client

Register a stdio server named `jmag`:

```text
command: C:\path\to\jmag-agent-suite\.venv\Scripts\python.exe   args: -m jmag_bridge   env (optional): JMAG_HOME=...
```

The package also provides a console script, `<venv>\Scripts\jmag-mcp.exe` (same server; the Claude Code plugin
starts it through `uvx`). Both entry points write `<state dir>\server-startup.log` if the server fails to start.

To check the setup without any client: `<venv>\Scripts\python.exe -m jmag_bridge --doctor`.

## Getting the operating manual without Claude skills

The skills are the manual that teaches an agent how to use the tools safely. Clients other than Claude Code do not
load them. Tell the agent, in the system prompt or the first message:

> Before any JMAG work, call `jmag_guide`.

The server's MCP instructions carry the same hint, so clients that pass server instructions to the model will see
it without help. Usage:

```text
jmag_guide                                                  index of skills plus the router skill (jmag)
jmag_guide(topic="jmag-simulation")                         one skill, by name, plus its reference file names
jmag_guide(topic="jmag-simulation", reference="properties") one file from that skill's references/ ('.md' optional)
jmag_guide(reference="tool-map")                            searches every skill for that reference file
```

Skills say "see references/x.md" when the agent should read one; it fetches that file with `jmag_guide`.

Topic names are the ten skill names: `jmag`, `jmag-simulation`, `jmag-geometry`, `jmag-machine-design`,
`jmag-experiments`, `jmag-losses-efficiency`, `jmag-parametric-optimisation`, `jmag-postprocess`, `jmag-scripting`,
`jmag-troubleshooting`. The guide is also available as MCP resources (if your client lists resources): `jmag://guide` (the
router skill), `jmag://guide/{topic}` and `jmag://guide/{topic}/{reference}`.

## Safety rules in one screen

From the router skill (`jmag`):

1. **Work on copies.** Never experiment in the engineer's only copy of a valuable `.jproj`:
   `jmag_save_project(path=<copy>)` first. Save before the first mesh/run of a new setup.
2. **Do not grab the engineer's windows.** `jmag_connect` without `pid` lists running instances instead of
   attaching; attach only to the instance the user names.
3. **`ok: false` is a stop sign.** JMAG logs errors instead of raising them; a failed step leaves an invalid object
   and everything built on it silently does nothing. Read `error.hint` and `jmag_messages`, fix, then continue.
4. **Never claim success because a run finished.** Back every number with checks from the feedback ladder: post-run
   verdict, a physics invariant, and a hand estimate. Report FAIL/CHECK findings; never omit them.
5. **Ask before destructive or expensive actions** (deleting studies, overwriting results, long batch runs, killing
   processes other than orphans).
6. **Know the install.** `jmag_capabilities` lists the study types and API features this JMAG offers before you
   promise a loss study, efficiency map, thermal or optimisation run. JMAG has no licence query: a listed type can
   still fail at Run if the module is not licensed.
   When `jmag_status`/`jmag_connect` report JMAG not found or the worker failed, call `jmag_doctor` and relay each
   `fix` before suggesting a reinstall.
   - If `jmag_capabilities` or `jmag_self_test` returns `first_run_note`, relay it to the user verbatim and follow
     `first_run_action`: wait for the answer, then call `jmag_star_repo` with `star`, `open` or `dismiss`. Never
     call `jmag_star_repo` without an explicit answer.
