"""Serve the status page from the latest saved run.

The local dashboard command stays on loopback. This process listens on
0.0.0.0:8080 so the container ingress can reach it. It does not approve.
"""

import importlib.util
import os
import sys
from pathlib import Path

PORT = 8080
HOST = "0.0.0.0"


def serve(dest: Path, open_store=None, bind=None):
    files = _files()
    name = os.environ.get("REPORTS_CONTAINER", "").strip()
    if name:
        opener = open_store or (lambda container: files.azure_reports(container, create=False))
        try:
            store = opener(name)
        except Exception:
            store = None
        if store is not None:
            files.download_latest(store, dest)
    reports = dest / "reports"
    submissions = dest / "submissions"
    from dispute_copilot.observability.board import bind_page, load_view, render
    from dispute_copilot.observability.compare_page import load_comparison, render_comparison

    page = render(load_view(reports))
    compare = render_comparison(load_comparison(reports, submissions))
    listener = bind or bind_page
    return listener(page, PORT, compare, HOST)


def main() -> int:
    dest = Path(os.environ.get("DASHBOARD_DIR", "/tmp/cd-dashboard"))
    try:
        server = serve(dest)
    except Exception as exc:
        print(type(exc).__name__, file=sys.stderr)
        return 1
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


def _files():
    path = Path(__file__).resolve().parent / "report_files.py"
    spec = importlib.util.spec_from_file_location("report_files", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("reports")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    raise SystemExit(main())
