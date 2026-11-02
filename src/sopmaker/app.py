"""Cross-platform CLI and portable desktop entry point."""

import argparse
import json
import sys
import webbrowser
from pathlib import Path

from filelock import FileLock, Timeout

from . import __version__
from .credentials import Credentials
from .exporter import export_notes
from .server import ReviewServer
from .store import Store
from .worker import Engine


def default_workspace():
    return Path.home() / "Documents" / "Sopmaker" / "Notebook"


def parser():
    result = argparse.ArgumentParser(description="Local inspection notebook. AI processing uses your OpenAI key.")
    result.add_argument("--version", action="version", version=f"sopmaker {__version__}")
    commands = result.add_subparsers(dest="command")
    for name, help_text in [("serve", "Open the local review app"),
                            ("import", "Copy a folder into an inspection without API calls"),
                            ("process", "Process imported fragments with OpenAI (paid API calls)"),
                            ("export", "Export selected observations without API calls"),
                            ("demo", "Open a synthetic offline example; no API calls")]:
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("--workspace", type=Path, default=default_workspace())
        if name in ("serve", "demo"):
            sub.add_argument("--no-open", action="store_true")
            sub.add_argument("--port", type=int, default=0)
        if name == "import":
            sub.add_argument("folder", type=Path)
        if name == "process":
            sub.add_argument("--retry", action="store_true", help="Explicitly retry failed/interrupted files")
            sub.add_argument("--use-saved-key", action="store_true")
        if name == "export":
            sub.add_argument("--ids", nargs="+")
            sub.add_argument("--include-drafts", action="store_true")
    return result


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    args = parser().parse_args(arguments or ["serve"])
    root = args.workspace.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    lock = FileLock(root / ".sopmaker.lock")
    try:
        with lock.acquire(timeout=0):
            store = Store(root)
            credentials = Credentials()
            engine = Engine(store, credentials)
            if args.command == "import":
                print(json.dumps(store.import_folder(args.folder), indent=2))
            elif args.command == "process":
                if args.use_saved_key and not credentials.key:
                    credentials.load_saved()
                ids = [a["id"] for a in store.assets() if a["state"] in ("imported", "error", "interrupted")] if args.retry else None
                engine.start(ids)
                while engine.thread.is_alive():
                    engine.thread.join(0.5)
                failures = [a for a in store.assets() if a["state"] in ("error", "interrupted")]
                print(json.dumps({"observations": len(store.observations()), "failed_files": len(failures)}))
                if failures:
                    raise SystemExit(1)
            elif args.command == "export":
                ids = args.ids or [o["id"] for o in store.observations() if args.include_drafts or o["state"] == "reviewed"]
                print(export_notes(store, ids, args.include_drafts))
            else:
                if args.command == "demo":
                    from .demo import seed_demo

                    seed_demo(store)
                with ReviewServer(store, engine, credentials, args.port) as server:
                    print(f"Sopmaker {__version__}\nWorkspace: {root}\nOpen: {server.url}", flush=True)
                    if not args.no_open:
                        webbrowser.open(server.url)
                    try:
                        server.serve_forever(poll_interval=0.3)
                    except KeyboardInterrupt:
                        engine.stop()
                        if engine.thread:
                            engine.thread.join(timeout=185)
    except Timeout:
        print("This inspection is already open in another Sopmaker process.", file=sys.stderr)
        raise SystemExit(1) from None
    except (ValueError, OSError) as exc:
        print(f"Sopmaker: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
