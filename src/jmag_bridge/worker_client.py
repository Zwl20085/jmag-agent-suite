"""Client side of the worker protocol (see worker/jmag_worker.py)."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
import weakref
from pathlib import Path

from .config import JmagInstall, state_dir

MAGIC = "@@JMAGRPC@@"
WORKER_SCRIPT = Path(__file__).parent / "worker" / "jmag_worker.py"

#: Every Worker created in this process, so the server can let go of all of them when its MCP client leaves.
_ALL: "weakref.WeakSet[Worker]" = weakref.WeakSet()


class WorkerError(RuntimeError):
    pass


class Worker:
    """One JMAG worker process (JMAG's Python 3.8) speaking line-framed JSON."""

    def __init__(self, install: JmagInstall, name: str = "main"):
        self.install = install
        self.name = name
        self.proc: subprocess.Popen | None = None
        self.log_path = state_dir() / f"worker-{name}.log"
        self._lines: queue.Queue = queue.Queue()
        self._next_id = 1
        self._lock = threading.Lock()
        self.stray_output: list[str] = []
        self._abandoned = False
        _ALL.add(self)

    # ------------------------------------------------------------------ lifecycle
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, timeout: float = 60) -> dict:
        if self.alive():
            return {"already_running": True, "pid": self.proc.pid}
        if not self.install.python.is_file():
            raise WorkerError(f"JMAG Python not found at {self.install.python}")
        env = dict(os.environ)
        for k in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "VIRTUAL_ENV"):
            env.pop(k, None)
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.proc = subprocess.Popen(
            [str(self.install.python), "-I", "-u", str(WORKER_SCRIPT), str(self.install.home), str(self.log_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            cwd=str(state_dir()), env=env, creationflags=flags,
        )
        self._lines = queue.Queue()
        threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()
        hello = self._await(0, timeout)
        if not hello.get("ok"):
            raise WorkerError(f"worker failed to start: {hello.get('error')}")
        return hello["result"]

    def _reader(self, proc: subprocess.Popen) -> None:
        for raw in proc.stdout:  # type: ignore[union-attr]
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            idx = line.find(MAGIC)
            if idx < 0:
                if line.strip():
                    self.stray_output.append(line)
                    del self.stray_output[:-50]
                continue
            try:
                self._lines.put(json.loads(line[idx + len(MAGIC):]))
            except ValueError:
                self.stray_output.append(line)
        self._lines.put(None)

    def stop(self) -> None:
        if self._abandoned or not self.alive():
            return
        try:
            self.call("__exit__", timeout=5)
        except Exception:
            pass
        try:
            self.proc.wait(5)  # type: ignore[union-attr]
        except subprocess.TimeoutExpired:
            self.proc.kill()  # type: ignore[union-attr]

    def kill(self) -> None:
        if self.alive():
            self.proc.kill()  # type: ignore[union-attr]

    def abandon(self) -> None:
        """Let go of this worker without killing it (server shutdown: the MCP client closed stdio).

        A thread blocked in call() raises WorkerError at once instead of waiting out the op timeout (up to 30 days
        for a solve), which is what kept the server process alive after its client had gone; later calls are
        refused. The worker's stdin is closed, so it exits by itself once its current op returns: a runner inside
        Study.Run is not killed mid-solve, and JMAG itself is never touched.
        """
        self._abandoned = True
        self._lines.put(None)  # wake _await now rather than at its next 1 s poll
        proc = self.proc
        if proc is not None and proc.stdin is not None:
            try:
                proc.stdin.close()
            except (OSError, ValueError):
                pass

    # ------------------------------------------------------------------ calls
    def _await(self, rid: int, timeout: float) -> dict:
        deadline = time.time() + timeout
        while True:
            if self._abandoned:
                raise WorkerError(f"worker '{self.name}' was released: the bridge is shutting down")
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError(f"worker '{self.name}' did not answer within {timeout:.0f}s")
            try:
                msg = self._lines.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                if not self.alive():
                    raise WorkerError(f"worker '{self.name}' exited (code {self.proc.returncode}); see {self.log_path}")
                continue
            if msg is None:
                if self._abandoned:
                    raise WorkerError(f"worker '{self.name}' was released: the bridge is shutting down")
                raise WorkerError(f"worker '{self.name}' closed its pipe; see {self.log_path}")
            if msg.get("id") == rid:
                return msg

    def call(self, op: str, timeout: float = 600, **args) -> dict:
        """Send one request and wait for its response (thread-safe, serialised)."""
        with self._lock:
            if self._abandoned:
                raise WorkerError(f"worker '{self.name}' was released: the bridge is shutting down")
            if not self.alive():
                self.start()
            rid = self._next_id
            self._next_id += 1
            payload = json.dumps({"id": rid, "op": op, "args": args}) + "\n"
            try:
                self.proc.stdin.write(payload.encode("utf-8"))  # type: ignore[union-attr]
                self.proc.stdin.flush()  # type: ignore[union-attr]
            except (OSError, ValueError) as e:  # ValueError: stdin closed by abandon()
                raise WorkerError(f"cannot write to worker: {e}") from e
            try:
                return self._await(rid, timeout)
            except TimeoutError:
                # The worker is stuck inside JMAG (often a modal dialog). Kill it so the
                # next call starts clean; the JMAG process itself keeps running.
                self.kill()
                raise


def abandon_all() -> int:
    """abandon() every Worker of this process; returns how many there were."""
    workers = list(_ALL)
    for w in workers:
        w.abandon()
    return len(workers)
