"""Small deterministic demo application for the browser tester."""

from __future__ import annotations

import argparse
import html
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs


def _page(projects: list[str]) -> bytes:
    cards = "".join(
        f'<article class="project-card"><h2>{html.escape(name)}</h2></article>'
        for name in projects
    ) or "<p>No projects yet.</p>"
    links = "".join(
        f'<li><a href="/projects/{html.escape(name, quote=True)}">{html.escape(name)}</a></li>'
        for name in projects
    ) or "<li>No projects yet.</li>"
    document = f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Project dashboard</title></head>
<body>
  <header><h1>Project dashboard</h1></header>
  <aside><h2>Projects</h2><nav aria-label="Project sidebar"><ul>{links}</ul></nav></aside>
  <main>
    <form method="post" action="/projects">
      <label for="project-name">Project name</label>
      <input id="project-name" name="project_name" required>
      <button type="submit">Create</button>
    </form>
    <section aria-label="Dashboard projects"><h2>Dashboard</h2>{cards}</section>
  </main>
</body>
</html>"""
    return document.encode("utf-8")


class DemoHandler(BaseHTTPRequestHandler):
    """Serve the page and keep project state on the server instance."""

    server_version = "WebAppTesterDemo/1.0"

    @property
    def projects(self) -> list[str]:
        return self.server.projects  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802 - required HTTPServer hook
        if self.path != "/" and not self.path.startswith("/projects/"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = _page(self.projects)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 - required HTTPServer hook
        if self.path != "/projects":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        values = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
        name = values.get("project_name", [""])[0].strip()
        if not name:
            self.send_error(HTTPStatus.BAD_REQUEST, "project_name is required")
            return
        if name not in self.projects:
            self.projects.append(name)
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        # Keep demo startup and test output readable while retaining errors.
        if self.command not in {"GET", "POST"} or self.path != "/":
            super().log_message(format, *args)


def serve(port: int = 8765, host: str = "127.0.0.1") -> None:
    server = ThreadingHTTPServer((host, port), DemoHandler)
    server.projects = []  # type: ignore[attr-defined]
    print(f"Demo app listening at http://{host}:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the WebAppTester demo application")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    serve(port=args.port, host=args.host)


if __name__ == "__main__":
    main()
