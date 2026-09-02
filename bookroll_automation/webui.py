from __future__ import annotations

import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .core import DEFAULT_BASE_URL, build_plan, extract_collection, format_plan, plan_payload


MAX_BODY_BYTES = 1024 * 1024
_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.RLock()


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>BookRoll PDF automation</title>
  <style>
    :root { color-scheme: light; --ink:#18212b; --muted:#64748b; --line:#d7dee8; --accent:#1769aa; --soft:#edf6ff; }
    * { box-sizing:border-box; }
    body { margin:0; background:#f5f7fa; color:var(--ink); font-family:system-ui,-apple-system,"Segoe UI",sans-serif; }
    main { max-width:980px; margin:32px auto; padding:0 18px 48px; }
    h1 { margin:0 0 8px; font-size:28px; }
    .lead { margin:0 0 24px; color:var(--muted); }
    .card { background:#fff; border:1px solid var(--line); border-radius:14px; padding:22px; margin:16px 0; box-shadow:0 5px 20px rgba(24,33,43,.04); }
    label { display:block; font-weight:650; margin:14px 0 6px; }
    input[type=text], input[type=password], input[type=number] { width:100%; padding:10px 11px; border:1px solid #b9c5d4; border-radius:8px; font:inherit; }
    .grid { display:grid; grid-template-columns:1fr 1fr; gap:0 16px; }
    .hint { color:var(--muted); font-size:13px; margin-top:5px; }
    .checks { display:flex; flex-wrap:wrap; gap:18px; margin:18px 0; }
    .checks label { margin:0; font-weight:500; }
    button { border:0; border-radius:8px; padding:10px 16px; color:#fff; background:var(--accent); font:inherit; font-weight:700; cursor:pointer; }
    button.secondary { color:var(--accent); background:var(--soft); }
    button:disabled { opacity:.55; cursor:wait; }
    pre { overflow:auto; white-space:pre-wrap; background:#101821; color:#e7edf4; border-radius:10px; padding:15px; min-height:120px; }
    .status { font-weight:700; }
    .warning { border-left:4px solid #d97706; padding:10px 12px; background:#fff8eb; }
    @media (max-width:700px) { .grid { grid-template-columns:1fr; } main { margin-top:18px; } }
  </style>
</head>
<body>
<main>
  <h1>BookRoll PDF automation</h1>
  <p class="lead">A local-first extraction UI. Dry-run is enabled by default.</p>
  <section class="card">
    <div class="warning">Cookies are never stored. They are used only for the current run. Using the <code>BOOKROLL_SESSION_COOKIE</code> environment variable is recommended.</div>
    <form id="job-form">
      <label for="base_url">Your BookRoll base URL</label>
      <input id="base_url" name="base_url" type="text" required value="https://your-bookroll-host.example/bookroll">
      <div class="hint">Enter your own authorized deployment URL. This value is used for this run and is not included in job output.</div>
      <div class="grid">
        <div>
          <label for="home_html">Saved course-list HTML</label>
          <input id="home_html" name="home_html" type="text" required placeholder="C:\\path\\home.html">
          <div class="hint">Save the authorized material-list page as UTF-8 HTML.</div>
        </div>
        <div>
          <label for="batch_dir">Output folder (new or empty)</label>
          <input id="batch_dir" name="batch_dir" type="text" required placeholder="C:\\private\\bookroll\\output_YYYYMMDD_01">
          <div class="hint">The extractor will not write into a non-empty folder.</div>
        </div>
      </div>
      <div class="grid">
        <div>
          <label for="known_index">Known page-count JSON (optional)</label>
          <input id="known_index" name="known_index" type="text" placeholder="既存のcollection_index.json">
        </div>
        <div>
          <label for="select">Material selection (optional)</label>
          <input id="select" name="select" type="text" placeholder="例: 1,3-5（空欄=全部）">
        </div>
      </div>
      <label for="cookie">Cookie (optional, not stored)</label>
      <input id="cookie" name="cookie" type="password" autocomplete="off" placeholder="環境変数を使う場合は空欄">
      <div class="hint">The value is not returned in responses, job state, or logs.</div>
      <div class="grid">
        <div>
          <label for="delay">Delay between pages (seconds)</label>
          <input id="delay" name="delay" type="number" min="0" step="0.05" value="0.15">
        </div>
        <div class="checks">
          <label><input id="dry_run" name="dry_run" type="checkbox" checked> dry-run (no network or file writes)</label>
          <label><input id="combine" name="combine" type="checkbox"> Create combined PDF</label>
        </div>
      </div>
      <div class="checks">
        <button id="submit" type="submit">Run</button>
        <button id="clear" class="secondary" type="button">Clear output</button>
      </div>
    </form>
  </section>
  <section class="card">
    <div>Status: <span id="status" class="status">Idle</span></div>
    <pre id="output">The dry-run plan or extraction log will appear here.</pre>
  </section>
</main>
<script>
const form = document.getElementById('job-form');
const output = document.getElementById('output');
const status = document.getElementById('status');
const submit = document.getElementById('submit');
document.getElementById('clear').addEventListener('click', () => { output.textContent = ''; status.textContent = 'Idle'; });
function formBody() {
  const data = new URLSearchParams(new FormData(form));
  if (!document.getElementById('dry_run').checked) data.delete('dry_run');
  if (!document.getElementById('combine').checked) data.delete('combine');
  return data;
}
async function poll(id) {
  const response = await fetch('/api/jobs/' + encodeURIComponent(id), {cache:'no-store'});
  const job = await response.json();
  status.textContent = job.status;
  output.textContent = (job.messages || []).join('\n') + (job.error ? '\nERROR: ' + job.error : '');
  if (job.result) output.textContent += '\n\n' + JSON.stringify(job.result, null, 2);
  if (job.status === 'queued' || job.status === 'running') setTimeout(() => poll(id), 900);
  else submit.disabled = false;
}
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  submit.disabled = true;
  status.textContent = 'Submitting';
  output.textContent = '';
  try {
    const response = await fetch('/api/jobs', {method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'}, body:formBody(), cache:'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'request failed');
    poll(data.id);
  } catch (error) {
    status.textContent = 'Error';
    output.textContent = error.message;
    submit.disabled = false;
  }
});
</script>
</body>
</html>
"""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    allowed = {"id", "status", "created_at", "updated_at", "messages", "result", "error", "plan"}
    return {key: value for key, value in job.items() if key in allowed}


def _set_job(job_id: str, **changes: Any) -> None:
    with _jobs_lock:
        job = _jobs[job_id]
        job.update(changes)
        job["updated_at"] = _now()


def _add_message(job_id: str, message: str) -> None:
    with _jobs_lock:
        messages = _jobs[job_id].setdefault("messages", [])
        messages.append(message)
        del messages[:-200]
        _jobs[job_id]["updated_at"] = _now()


def _value(form: dict[str, list[str]], key: str, default: str = "") -> str:
    return (form.get(key) or [default])[0].strip()


def _truthy(form: dict[str, list[str]], key: str) -> bool:
    return _value(form, key).lower() in {"1", "true", "on", "yes"}


def _build_request_plan(form: dict[str, list[str]]):
    home_html = Path(_value(form, "home_html"))
    batch_dir = Path(_value(form, "batch_dir"))
    known_index_text = _value(form, "known_index")
    known_index = Path(known_index_text) if known_index_text else None
    plans = build_plan(home_html, batch_dir, _value(form, "select") or None, known_index)
    return plans, plan_payload(plans, home_html, batch_dir)


def _run_extract_job(job_id: str, plans, batch_dir: Path, form: dict[str, list[str]], cookie: str) -> None:
    try:
        _set_job(job_id, status="running")
        delay = float(_value(form, "delay", "0.15"))
        if delay < 0:
            raise ValueError("delay must be non-negative")
        result = extract_collection(
            plans,
            batch_dir,
            cookie=cookie,
            base_url=_value(form, "base_url") or os.environ.get("BOOKROLL_BASE_URL", DEFAULT_BASE_URL),
            delay=delay,
            progress=lambda message: _add_message(job_id, message),
            combine=_truthy(form, "combine"),
        )
        _set_job(job_id, status="completed" if result["summary"]["failed"] == 0 else "failed", result=result["summary"])
    except Exception as error:
        _set_job(job_id, status="failed", error=f"{type(error).__name__}: {error}")


class Handler(BaseHTTPRequestHandler):
    server_version = "BookRollWebUI/0.1"

    def log_message(self, format: str, *args) -> None:
        # Never log request bodies or query strings: they could contain credentials.
        print(f"[{_now()}] {self.command} {self.path.split('?', 1)[0]}", flush=True)

    def _headers(self, content_type: str, length: int) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._headers("application/json; charset=utf-8", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self) -> None:
        body = INDEX_HTML.encode("utf-8")
        self.send_response(200)
        self._headers("text/html; charset=utf-8", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _read_form(self) -> dict[str, list[str]]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("invalid Content-Length") from error
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError("request body is too large")
        raw = self.rfile.read(length)
        return parse_qs(raw.decode("utf-8"), keep_blank_values=True)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._send_html()
            return
        if path.startswith("/api/jobs/"):
            job_id = path.rsplit("/", 1)[-1]
            with _jobs_lock:
                job = _jobs.get(job_id)
                if job is None:
                    self._send_json({"error": "job not found"}, 404)
                else:
                    self._send_json(_public_job(job))
            return
        self._send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            form = self._read_form()
            plans, payload = _build_request_plan(form)
            if path == "/api/plan":
                self._send_json(payload)
                return
            if path != "/api/jobs":
                self._send_json({"error": "not found"}, 404)
                return
            job_id = uuid.uuid4().hex[:12]
            dry_run = _truthy(form, "dry_run")
            job = {
                "id": job_id,
                "status": "queued",
                "created_at": _now(),
                "updated_at": _now(),
                "messages": [],
                "plan": payload,
            }
            with _jobs_lock:
                _jobs[job_id] = job
            if dry_run:
                _set_job(job_id, status="completed", result={"dry_run": True, "plan": payload})
                _add_message(job_id, "dry-run: network access and file writes were disabled")
            else:
                cookie = _value(form, "cookie") or os.environ.get("BOOKROLL_SESSION_COOKIE", "")
                if not cookie.strip():
                    _set_job(job_id, status="failed", error="Cookie is empty; set BOOKROLL_SESSION_COOKIE or enter it for this run")
                else:
                    thread = threading.Thread(
                        target=_run_extract_job,
                        args=(job_id, plans, Path(_value(form, "batch_dir")), form, cookie),
                        name=f"bookroll-job-{job_id}",
                        daemon=True,
                    )
                    thread.start()
            self._send_json({"id": job_id}, 202)
        except Exception as error:
            self._send_json({"error": f"{type(error).__name__}: {error}"}, 400)


def run_server(host: str = "127.0.0.1", port: int = 51837) -> None:
    if not (10000 <= port <= 99999):
        raise ValueError("port must be a five-digit number")
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"BookRoll WebUI: http://{host}:{port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopping", flush=True)
    finally:
        server.server_close()
