# -*- coding: utf-8 -*-
"""JMAG worker process.

Runs under JMAG's *bundled* Python 3.8 interpreter (``<JMAG>/python3.8/python.exe``)
because ``PyJmag.dll`` is only ABI-compatible with CPython 3.8: under 3.11 every
void-returning call crashes with an access violation.

Keep this file and ``ops.py`` Python 3.8 compatible (no ``match``, no ``X | Y``
annotations, no ``list[str]`` at runtime).

Protocol
--------
One JSON object per line on the *original* stdin/stdout pipes, which are dup'ed
before anything JMAG-related is imported. Afterwards fd 0 -> NUL and fd 1/2 -> a
log file, so the chatter printed by PyJmag.dll / designer.exe can never corrupt
the protocol, and designer.exe (if launched from here) does not inherit our pipes.
Every response line is prefixed with ``MAGIC`` so the client can ignore any
stray output that still reaches the pipe.

Request : {"id": 1, "op": "name", "args": {...}}
Response: {"id": 1, "ok": true, "result": ..., "jmag": {...}, "elapsed": 0.12}
"""
import io
import json
import os
import sys
import time
import traceback

MAGIC = "@@JMAGRPC@@"


def _redirect_std_streams(log_path):
    proto_in = io.open(os.dup(0), "r", encoding="utf-8", newline="\n")
    proto_out = io.open(os.dup(1), "w", encoding="utf-8", newline="\n")
    log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    null_fd = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null_fd, 0)
    os.dup2(log_fd, 1)
    os.dup2(log_fd, 2)
    if os.name == "nt":
        # Native code that asks Win32 for the std handles gets the log, too.
        import ctypes
        import msvcrt
        k32 = ctypes.windll.kernel32
        k32.SetStdHandle(-10, msvcrt.get_osfhandle(0))
        k32.SetStdHandle(-11, msvcrt.get_osfhandle(1))
        k32.SetStdHandle(-12, msvcrt.get_osfhandle(2))
    sys.stdin = io.open(0, "r", closefd=False)
    sys.stdout = io.open(1, "w", encoding="utf-8", errors="replace", closefd=False, buffering=1)
    sys.stderr = io.open(2, "w", encoding="utf-8", errors="replace", closefd=False, buffering=1)
    return proto_in, proto_out


def main():
    jmag_home = sys.argv[1]
    log_path = sys.argv[2]
    proto_in, proto_out = _redirect_std_streams(log_path)

    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    if jmag_home not in sys.path:
        sys.path.insert(1, jmag_home)

    def send(obj):
        proto_out.write(MAGIC + json.dumps(obj, default=str) + "\n")
        proto_out.flush()

    try:
        import ops
        ctx = ops.Context(jmag_home)
    except Exception:
        send({"id": 0, "ok": False, "error": {"type": "ImportError", "message": traceback.format_exc()}})
        return 2
    send({"id": 0, "ok": True, "result": {"ready": True, "python": sys.version.split()[0], "pid": os.getpid()}})

    for line in proto_in:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            send({"id": -1, "ok": False, "error": {"type": "ProtocolError", "message": "bad json"}})
            continue
        rid = req.get("id")
        op_name = req.get("op")
        if op_name == "__exit__":
            send({"id": rid, "ok": True, "result": "bye"})
            break
        t0 = time.time()
        print("[%s] -> %s %s" % (time.strftime("%H:%M:%S"), op_name, json.dumps(req.get("args"), default=str)[:400]))
        resp = ctx.dispatch(op_name, req.get("args") or {})
        resp["id"] = rid
        resp["elapsed"] = round(time.time() - t0, 3)
        print("[%s] <- %s ok=%s %.2fs" % (time.strftime("%H:%M:%S"), op_name, resp.get("ok"), resp["elapsed"]))
        send(resp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
