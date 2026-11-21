"""Local-only HTTP server for region review."""

import hashlib
import hmac
import json
import secrets
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .regions import approval_is_current, atomic_json, validate_annotations, verify_run


class RegionServer(ThreadingHTTPServer):
    def __init__(self, config, config_dir, port=0, config_path=None):
        self.config = config
        self.config_dir = config_dir
        self.config_path = config_path
        self.root, self.manifest = verify_run(config, config_dir, config_path)
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        super().__init__(("127.0.0.1", port), RegionHandler)

    @property
    def origin(self):
        return f"http://127.0.0.1:{self.server_port}"

    def read_draft(self):
        with (self.root / "draft.json").open(encoding="utf-8") as file:
            return json.load(file)


class RegionHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def respond(self, status, body, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src blob:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def json_response(self, status, data):
        self.respond(status, json.dumps(data).encode())

    def authorized(self):
        host = self.headers.get("Host", "")
        token = self.headers.get("Authorization", "")
        return host == self.server.origin.removeprefix("http://") and hmac.compare_digest(
            token, f"Bearer {self.server.token}"
        )

    def do_GET(self):
        if self.headers.get("Host") != self.server.origin.removeprefix("http://"):
            return self.json_response(403, {"error": "Invalid host"})
        if self.path == "/":
            html = Path(__file__).with_name("editor.html").read_bytes()
            return self.respond(200, html, "text/html; charset=utf-8")
        if not self.authorized():
            return self.json_response(403, {"error": "Unauthorized"})
        try:
            with self.server.lock:
                root, manifest = verify_run(self.server.config, self.server.config_dir, self.server.config_path)
                if self.path == "/api/state":
                    draft = self.server.read_draft()
                    expected_questions = []
                    try:
                        from .rubric import read_rubric, rubric_root
                        rubric_path = rubric_root(self.server.config, self.server.config_dir) / "rubric.xlsx"
                        if rubric_path.is_file():
                            expected_questions = [
                                str(q.get("question_number") or q.get("question") or "").strip()
                                for q in read_rubric(rubric_path)
                                if str(q.get("question_number") or q.get("question") or "").strip()
                            ]
                    except Exception:
                        expected_questions = []
                    return self.json_response(200, {
                        "pages": manifest["pages"], "draft": draft,
                        "approved": approval_is_current(root, manifest, draft),
                        "expected_questions": expected_questions,
                    })
                if self.path == "/api/suggestions":
                    suggestion_path = root / "ai_suggestions.json"
                    if not suggestion_path.is_file():
                        return self.json_response(404, {"error": "No AI suggestions exist for this run"})
                    with suggestion_path.open(encoding="utf-8") as file:
                        suggestions = json.load(file)
                    if not isinstance(suggestions, dict):
                        raise ValueError("Invalid suggestions")
                    if (
                        suggestions.get("version") != 1
                        or suggestions.get("pdf_sha256") != manifest["pdf_sha256"]
                        or suggestions.get("config_sha256") != manifest["config_sha256"]
                    ):
                        raise ValueError("Suggestions belong to a different run")
                    validate_annotations(suggestions["annotations"], manifest["pages"])
                    return self.json_response(200, {
                        "annotations": suggestions["annotations"], "model": suggestions["model"]
                    })
                prefix = "/api/images/"
                if self.path.startswith(prefix):
                    page = self.path[len(prefix):]
                    if not page.isascii() or not page.isdigit() or int(page) >= len(manifest["pages"]):
                        return self.json_response(404, {"error": "Page not found"})
                    return self.respond(200, (root / "images" / f"{int(page)}.jpg").read_bytes(), "image/jpeg")
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return self.json_response(409, {"error": "Run changed or is unreadable; stop and inspect it"})
        return self.json_response(404, {"error": "Not found"})

    def do_POST(self):
        if not self.authorized() or self.headers.get("Origin") != self.server.origin:
            return self.json_response(403, {"error": "Unauthorized"})
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.json_response(415, {"error": "Expected JSON"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1024 * 1024:
                return self.json_response(400, {"error": "Invalid request size"})
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Invalid request")
            with self.server.lock:
                root, manifest = verify_run(self.server.config, self.server.config_dir, self.server.config_path)
                draft = self.server.read_draft()
                if type(data.get("revision")) is not int or data["revision"] != draft["revision"]:
                    return self.json_response(409, {"error": "Draft changed in another tab; reload before editing"})
                if self.path == "/api/page":
                    page = data.get("page")
                    if type(page) is not int or not 0 <= page < len(manifest["pages"]):
                        raise ValueError("Invalid page")
                    if type(data.get("reviewed")) is not bool:
                        raise ValueError("Explicit page review choice required")
                    draft["annotations"][str(page)] = data.get("boxes")
                    validate_annotations(draft["annotations"], manifest["pages"])
                    reviewed = set(draft["reviewed"])
                    reviewed.discard(page)
                    if data["reviewed"]:
                        reviewed.add(page)
                    draft["reviewed"] = sorted(reviewed)
                    draft["revision"] += 1
                    (root / "approval.json").unlink(missing_ok=True)
                    atomic_json(root / "draft.json", draft)
                    return self.json_response(200, draft)
                if self.path == "/api/approve":
                    if set(draft["reviewed"]) != set(range(len(manifest["pages"]))):
                        raise ValueError("Review and save every page before approving")
                    validate_annotations(draft["annotations"], manifest["pages"], complete=True)
                    atomic_json(root / "annotations.json", draft["annotations"])
                    digest = hashlib.sha256((root / "annotations.json").read_bytes()).hexdigest()
                    atomic_json(root / "approval.json", {
                        "annotations_sha256": digest,
                        "config_sha256": manifest["config_sha256"],
                        "pdf_sha256": manifest["pdf_sha256"],
                        "revision": draft["revision"],
                        "approved_at": datetime.now(timezone.utc).isoformat(),
                    })
                    return self.json_response(200, {"approved": True})
                return self.json_response(404, {"error": "Not found"})
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            return self.json_response(400, {"error": str(exc)})
        except OSError:
            return self.json_response(409, {"error": "Run changed or is unreadable; stop and inspect it"})
