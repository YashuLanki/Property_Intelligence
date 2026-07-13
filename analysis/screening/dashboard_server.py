"""
analysis/screening/dashboard_server.py
-----------------------------------------
Local-only web server for the screening dashboard. "LOCAL" means only your
own machine can reach it -- nothing is exposed to the internet.

start_dashboard_server() runs the server in a background daemon thread so
it can be called from a long-running MCP server process without blocking
it. Calling it twice is safe -- if the port is already bound (e.g. from a
previous call in this same process), it just returns the existing URL
instead of crashing.
"""

import http.server
import socketserver
import threading
import webbrowser
from pathlib import Path

# analysis/screening/dashboard/vaulter_dashboard.html
DASHBOARD_DIR = Path(__file__).resolve().parent / "dashboard"

_server_lock = threading.Lock()
_running_servers: dict[int, str] = {}  # port -> url


def _make_handler(root_dir: Path):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root_dir), **kwargs)

        def log_message(self, format, *args):
            pass  # quiet -- suppress routine request logging

    return Handler


def start_dashboard_server(root_dir: Path, port: int = 8000) -> str:
    """
    Starts a socketserver.TCPServer in a background daemon thread, bound to
    127.0.0.1:port, serving root_dir. root_dir must be the merged project's
    root directory (the folder containing both analysis/screening/dashboard/
    vaulter_dashboard.html AND data/screening_output/), so the dashboard's
    relative fetches to /data/screening_output/manifest.json and
    /data/screening_output/<workbook>.xlsx resolve correctly, and so its
    "Download Excel" link (/data/screening_output/<filename>) works.
    Returns the dashboard URL string.
    """
    url = f"http://127.0.0.1:{port}/analysis/screening/dashboard/vaulter_dashboard.html"

    with _server_lock:
        if port in _running_servers:
            return _running_servers[port]

        handler = _make_handler(root_dir)
        try:
            httpd = socketserver.TCPServer(("127.0.0.1", port), handler)
        except OSError:
            # Address already in use -- likely already running from a prior
            # call (possibly in a different process). Return the expected
            # URL anyway since something is already bound to this port.
            _running_servers[port] = url
            return url

        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        _running_servers[port] = url
        return url


if __name__ == "__main__":
    # Manual-testing entry point -- old blocking standalone behavior.
    PORT = 8000
    ROOT_DIR = Path(__file__).resolve().parent.parent.parent  # project root

    with socketserver.TCPServer(("127.0.0.1", PORT), _make_handler(ROOT_DIR)) as httpd:
        url = f"http://127.0.0.1:{PORT}/analysis/screening/dashboard/vaulter_dashboard.html"
        print(f"Serving locally at {url}")
        print("This is only reachable from your own machine -- nothing is exposed externally.")
        print("Press Ctrl+C to stop.\n")
        webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
