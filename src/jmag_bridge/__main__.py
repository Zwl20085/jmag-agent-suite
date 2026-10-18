"""`python -m jmag_bridge [--doctor [--json] | --version]`.

The server module is imported inside a guard: when it cannot even be imported (missing/broken `mcp`, bad
environment) the failure is logged to <state dir>/server-startup.log, and --doctor still runs because
doctor.py needs only the standard library.
"""
import sys


def main() -> None:
    argv = sys.argv[1:]
    try:
        from .server import main as server_main
    except Exception as e:
        from . import __version__, doctor
        if "--version" in argv:
            print(f"jmag-agent-suite {__version__}")
            sys.exit(0)
        if "--doctor" in argv:
            print(f"note: the server module failed to import ({type(e).__name__}: {e})", file=sys.stderr)
            sys.exit(doctor.cli(as_json="--json" in argv))
        doctor.record_startup_failure(e)
        raise
    server_main(argv)


if __name__ == "__main__":
    main()
