import argparse
import os
import sys
import threading
import webbrowser
from pathlib import Path
from . import __version__
from .server import make_server, periodic
from .service import Service
from .store import Store


def default_state():
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Kosuzu"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Kosuzu"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "kosuzu"


def main():
    parser = argparse.ArgumentParser(description="Kosuzu electronics inventory")
    parser.add_argument("command", choices=["client", "server", "keys"], nargs="?", default="client")
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--public-url", default="", help="HTTPS origin used by your reverse proxy")
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args()
    if args.interval < 5 or not 0 <= args.port <= 65535:
        parser.error("Interval must be >= 5 seconds and port must be 0–65535")
    if args.command == "client" and args.host not in {"127.0.0.1", "localhost"}:
        parser.error("Desktop client must bind to loopback; use server mode for network access")
    state = args.state_dir or default_state() / ("server" if args.command == "server" else "client")
    first = not (state / "state.sqlite3").exists()
    store = Store(state)
    if args.command == "keys" or first:
        print(f"Administrator access key: {store.setting('admin_key')}")
        print(f"Client access key: {store.setting('client_key')}")
        if args.command == "keys":
            return
    service = Service(store, args.command)
    server = make_server(service, args.host, args.port, args.public_url)
    stop = threading.Event()
    worker = threading.Thread(target=periodic, args=(service, stop, args.interval), daemon=True)
    worker.start()
    print(f"Kosuzu {__version__} {args.command}: {server.public_url}", flush=True)
    if args.command == "client" and not args.no_browser:
        webbrowser.open(server.public_url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        worker.join(timeout=30)


if __name__ == "__main__":
    main()
