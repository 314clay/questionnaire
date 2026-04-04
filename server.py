import asyncio
import base64
import glob
import json
import logging
import os
import re
import subprocess
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Template
from nanoid import generate as nanoid

import db
from models import CreateRequest, RespondRequest

# --- Config ---

AUDIO_DIR = Path(os.environ.get("AUDIO_DIR", "audio_data"))
PORT = int(os.environ.get("PORT", "3050"))
START_TIME = time.time()

# --- Template Engine ---

TEMPLATES: dict[str, str] = {}

PAYLOAD_RE = re.compile(
    r"const payload = \{.*?\};",
    re.DOTALL,
)

FETCH_RE = re.compile(
    r"// TODO:.*?\n\s*// (fetch\(`/api/respond/\$\{port\}`,.*?\);)",
    re.DOTALL,
)

PORT_EXTRACT_RE = re.compile(
    r"const port = location\.pathname\.split\('/'\)\.filter\(Boolean\)\.pop\(\)[^;]*;",
)

PORT_BADGE_RE = re.compile(
    r"document\.getElementById\('port-badge'\)\.textContent = ':' \+ port;",
)


def load_templates():
    template_dir = Path(__file__).parent / "templates"
    for html_file in template_dir.glob("*.html"):
        raw = html_file.read_text()
        raw = raw.replace('href="../style.css"', 'href="/static/style.css"')
        raw = raw.replace('src="../audio-recorder.js"', 'src="/static/audio-recorder.js"')
        raw = raw.replace('src="../audio-widget.js"', 'src="/static/audio-widget.js"')
        TEMPLATES[html_file.stem] = raw


def render_template(template_type: str, qid: str, payload: dict, closed: bool = False) -> str:
    html = TEMPLATES[template_type]

    # Inject real payload
    inject = {**payload, "_id": qid, "_closed": closed}
    html = PAYLOAD_RE.sub(
        f"const payload = {json.dumps(inject)};",
        html,
    )

    # Replace port extraction with questionnaire ID
    html = PORT_EXTRACT_RE.sub(
        f"const port = '{qid}';",
        html,
    )

    # Update port badge to show ID instead
    html = PORT_BADGE_RE.sub(
        f"document.getElementById('port-badge').textContent = '{qid}';",
        html,
    )

    # Activate fetch calls
    html = FETCH_RE.sub(
        lambda m: m.group(1).replace("${port}", qid),
        html,
    )

    # Inject SSE auto-reload listener (for channel/ask flow)
    reload_script = f"""
<script>
(function() {{
  const es = new EventSource('/api/listen/{qid}');
  es.addEventListener('new_question', () => {{ location.reload(); }});
  es.addEventListener('closed', () => {{ location.reload(); }});
}})();
</script>"""
    html = html.replace("</body>", reload_script + "\n</body>")

    # If closed, inject a disabling script
    if closed:
        close_script = """
<script>
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('button, input, .toggle-track, .option, .hold-btn, .grid-btn, .multi-live-btn')
    .forEach(el => { el.style.pointerEvents = 'none'; el.style.opacity = '0.5'; });
  const banner = document.createElement('div');
  banner.className = 'feedback sent';
  banner.textContent = 'This questionnaire has been closed';
  banner.style.display = 'block';
  const card = document.querySelector('.card');
  if (card) card.prepend(banner);
});
</script>"""
        html = html.replace("</body>", close_script + "\n</body>")

    return html


# --- Index Page ---

INDEX_TEMPLATE = Template("""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Questionnaires</title>
  <link rel="stylesheet" href="/static/style.css">
  <style>
    .q-list { display: flex; flex-direction: column; gap: 1rem; padding: 2rem; max-width: 700px; margin: 0 auto; }
    .q-card { background: var(--surface); border-radius: 20px; padding: 1.5rem; box-shadow: var(--shadow-raised); text-decoration: none; color: inherit; transition: transform 0.15s ease; }
    .q-card:active { transform: scale(0.98); box-shadow: var(--shadow-pressed); }
    .q-card h3 { margin: 0 0 0.5rem 0; font-size: 1.1rem; }
    .q-meta { display: flex; gap: 0.75rem; align-items: center; flex-wrap: wrap; }
    .q-badge { background: var(--accent-blue); color: white; padding: 0.2rem 0.6rem; border-radius: 999px; font-size: 0.75rem; font-weight: 600; }
    .q-badge.persistent { background: var(--accent-green); }
    .q-stat { color: var(--text-secondary); font-size: 0.85rem; }
    .q-empty { text-align: center; color: var(--text-secondary); padding: 4rem 2rem; font-size: 1.1rem; }
    .q-header { text-align: center; padding: 2rem 2rem 0; }
    .q-header h1 { font-size: 1.5rem; margin: 0; }
    .q-header p { color: var(--text-secondary); margin: 0.5rem 0 0; }
  </style>
</head>
<body>
  <div class="q-header">
    <h1>Questionnaires</h1>
    <p>{{ total }} active</p>
  </div>
  <div class="q-list">
    {% if questionnaires %}
      {% for q in questionnaires %}
      <a class="q-card" href="/{{ q.id }}">
        <h3>{{ q.title }}</h3>
        <div class="q-meta">
          <span class="q-badge {{ 'persistent' if q.is_persistent else '' }}">{{ q.type }}</span>
          <span class="q-stat">{{ q.response_count }} response{{ 's' if q.response_count != 1 else '' }}</span>
          <span class="q-stat">{{ q.age }}</span>
        </div>
      </a>
      {% endfor %}
    {% else %}
      <div class="q-empty">No active questionnaires</div>
    {% endif %}
  </div>
</body>
</html>""")


# --- SSE ---

sse_listeners: dict[str, set[asyncio.Queue]] = defaultdict(set)


async def broadcast(qid: str, event_type: str, data: dict):
    message = f"event: {event_type}\ndata: {json.dumps(data, default=str)}\n\n"
    dead = set()
    for queue in sse_listeners.get(qid, set()):
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            dead.add(queue)
    if dead:
        sse_listeners[qid] -= dead


# --- Audio ---

async def process_audio(response_id: int, audio_clips: list[dict]) -> int:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    for i, clip in enumerate(audio_clips or []):
        b64 = clip.get("base64", "")
        if not b64:
            continue
        if "," in b64:
            _, b64_data = b64.split(",", 1)
        else:
            b64_data = b64
        mime_type = clip.get("mimeType", "audio/webm")
        ext = "webm" if "webm" in mime_type else "mp4" if "mp4" in mime_type else "ogg"
        file_name = f"{response_id}_{i}.{ext}"
        file_path = AUDIO_DIR / file_name

        audio_bytes = base64.b64decode(b64_data)
        file_path.write_bytes(audio_bytes)

        await db.store_audio_clip(
            response_id, i, str(file_path), mime_type,
            clip.get("duration"), len(audio_bytes),
        )
        count += 1
    return count


# --- Actions ---

log = logging.getLogger("questionnaire")


def get_xauthority() -> str | None:
    matches = glob.glob("/tmp/serverauth.*") + glob.glob("/tmp/host-tmp/serverauth.*")
    return matches[0] if matches else None


def execute_dpms(response_data: dict):
    value = response_data.get("value", False)
    state = "on" if value else "off"
    xauth = get_xauthority()
    env = {**os.environ, "DISPLAY": ":0"}
    if xauth:
        env["XAUTHORITY"] = xauth
    try:
        result = subprocess.run(
            ["xset", "dpms", "force", state],
            env=env, timeout=5, capture_output=True, text=True,
        )
        log.info(f"DPMS {state}: rc={result.returncode} stderr={result.stderr.strip()}")
    except Exception as e:
        log.error(f"DPMS {state} failed: {e}")


ACTIONS = {
    "dpms": execute_dpms,
}


def execute_action(questionnaire: dict, response_data: dict):
    payload = json.loads(questionnaire["payload"]) if isinstance(questionnaire["payload"], str) else questionnaire["payload"]
    action = payload.get("_action")
    if not action:
        return
    handler = ACTIONS.get(action)
    if handler:
        handler(response_data)
    else:
        log.warning(f"Unknown action: {action}")


# --- App ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()
    load_templates()
    yield
    await db.close_pool()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


# --- Helper ---

def relative_time(dt: datetime) -> str:
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    diff = now - dt
    seconds = int(diff.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        m = seconds // 60
        return f"{m}m ago"
    if seconds < 86400:
        h = seconds // 3600
        return f"{h}h ago"
    d = seconds // 86400
    return f"{d}d ago"


# --- Routes ---

@app.get("/api/state")
async def health():
    return {
        "status": "ok",
        "service": "questionnaire",
        "db": "connected" if db.pool else "disconnected",
        "uptime_seconds": int(time.time() - START_TIME),
    }


@app.post("/api/create")
async def create_questionnaire(req: CreateRequest):
    qid = req.id or nanoid(size=8)
    qtype = req.type
    is_persistent = qtype in db.PERSISTENT_TYPES
    allow_multiple = req.allow_multiple if req.allow_multiple is not None else is_persistent
    title = req.payload.get("question") or req.payload.get("steps", [{}])[0].get("question", "Untitled")

    if qtype not in TEMPLATES:
        return JSONResponse({"error": f"unknown template type: {qtype}"}, 400)

    existing = await db.get_questionnaire(qid)
    if existing:
        return JSONResponse({"error": f"id '{qid}' already exists"}, 409)

    row = await db.create_questionnaire(qid, qtype, title, req.payload, is_persistent, allow_multiple)
    return JSONResponse({
        "id": row["id"],
        "url": f"/{ row['id']}",
        "type": row["type"],
        "is_persistent": row["is_persistent"],
        "created_at": row["created_at"].isoformat(),
    }, 201)


@app.post("/api/ask/{qid}")
async def ask(qid: str, req: CreateRequest):
    """Replace the questionnaire at this ID with a new question. iPad auto-reloads."""
    qtype = req.type
    if qtype not in TEMPLATES:
        return JSONResponse({"error": f"unknown template type: {qtype}"}, 400)

    is_persistent = qtype in db.PERSISTENT_TYPES
    allow_multiple = req.allow_multiple if req.allow_multiple is not None else is_persistent
    title = req.payload.get("question") or req.payload.get("steps", [{}])[0].get("question", "Untitled")

    row = await db.replace_questionnaire(qid, qtype, title, req.payload, is_persistent, allow_multiple)

    # Notify any iPads viewing this path to reload
    await broadcast(qid, "new_question", {
        "id": qid,
        "type": qtype,
        "title": title,
    })

    return JSONResponse({
        "id": row["id"],
        "url": f"/{row['id']}",
        "type": row["type"],
        "is_persistent": row["is_persistent"],
        "created_at": row["created_at"].isoformat(),
    }, 200)


@app.get("/api/questionnaires")
async def list_questionnaires(active: bool = True, type: str | None = None):
    rows = await db.list_questionnaires(active_only=active, qtype=type)
    return {
        "questionnaires": [
            {
                "id": r["id"],
                "type": r["type"],
                "title": r["title"],
                "is_persistent": r["is_persistent"],
                "response_count": r["response_count"],
                "closed": r["closed_at"] is not None,
                "created_at": r["created_at"].isoformat(),
            }
            for r in rows
        ]
    }


@app.post("/api/respond/{qid}")
async def respond(qid: str, req: RespondRequest):
    q = await db.get_questionnaire(qid)
    if not q:
        return JSONResponse({"error": "not found"}, 404)
    if q["closed_at"]:
        return JSONResponse({"error": "questionnaire_closed", "closed_at": q["closed_at"].isoformat()}, 410)

    # Build response_data from all fields except audio base64
    response_data = req.model_dump(exclude={"audio"}, exclude_none=True)
    # Include extra fields that were passed through
    if hasattr(req, "model_extra") and req.model_extra:
        response_data.update(req.model_extra)

    row = await db.store_response(qid, response_data, q["is_persistent"], q["allow_multiple"])
    if not row:
        existing_count = await db.get_response_count(qid)
        if existing_count > 0:
            return JSONResponse({"error": "already_responded"}, 409)
        return JSONResponse({"error": "questionnaire_closed"}, 410)

    response_id = row["id"]

    # Process audio clips
    audio_count = 0
    if req.audio:
        audio_count = await process_audio(response_id, [c.model_dump() for c in req.audio])

    # Execute action if configured
    execute_action(q, response_data)

    # Broadcast to SSE listeners
    await broadcast(qid, "response", {
        "id": response_id,
        "response_data": response_data,
        "audio_clip_count": audio_count,
        "created_at": row["created_at"].isoformat(),
    })

    # Auto-close one-shot questionnaires
    if not q["is_persistent"] and not q["allow_multiple"]:
        await db.close_questionnaire(qid)
        await broadcast(qid, "closed", {"questionnaire_id": qid})

    return JSONResponse({
        "response_id": response_id,
        "questionnaire_id": qid,
        "audio_clip_count": audio_count,
        "created_at": row["created_at"].isoformat(),
    }, 201)


@app.get("/api/response/{qid}")
async def get_responses(qid: str, since: str | None = None, latest: bool = False):
    q = await db.get_questionnaire(qid)
    if not q:
        return JSONResponse({"error": "not found"}, 404)

    since_dt = None
    if since:
        since_dt = datetime.fromisoformat(since)

    responses = await db.get_responses(qid, since=since_dt, latest=latest)

    return {
        "questionnaire_id": qid,
        "type": q["type"],
        "is_persistent": q["is_persistent"],
        "closed": q["closed_at"] is not None,
        "responses": [
            {
                "id": r["id"],
                "response_data": json.loads(r["response_data"]) if isinstance(r["response_data"], str) else r["response_data"],
                "audio_clips": [
                    {"id": c["id"], "url": f"/api/audio/{c['id']}", "mime_type": c["mime_type"], "duration_ms": c["duration_ms"]}
                    for c in (json.loads(r["audio_clips"]) if isinstance(r["audio_clips"], str) else r["audio_clips"])
                ],
                "created_at": r["created_at"].isoformat() if hasattr(r["created_at"], "isoformat") else r["created_at"],
            }
            for r in responses
        ],
        "response_count": len(responses),
    }


@app.get("/api/listen/{qid}")
async def listen(qid: str, request: Request, replay: bool = False):
    q = await db.get_questionnaire(qid)
    if not q:
        return JSONResponse({"error": "not found"}, 404)

    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    sse_listeners[qid].add(queue)

    async def event_generator():
        try:
            if replay:
                rows = await db.get_responses(qid)
                for r in rows:
                    data = {
                        "id": r["id"],
                        "response_data": json.loads(r["response_data"]) if isinstance(r["response_data"], str) else r["response_data"],
                        "created_at": r["created_at"].isoformat() if hasattr(r["created_at"], "isoformat") else r["created_at"],
                    }
                    yield f"event: response\ndata: {json.dumps(data, default=str)}\n\n"

            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield message
                except asyncio.TimeoutError:
                    yield f"event: heartbeat\ndata: {json.dumps({'time': datetime.now(timezone.utc).isoformat()})}\n\n"

                if await request.is_disconnected():
                    break
        finally:
            sse_listeners[qid].discard(queue)
            if not sse_listeners[qid]:
                del sse_listeners[qid]

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/audio/{clip_id}")
async def serve_audio(clip_id: int):
    clip = await db.get_audio_clip(clip_id)
    if not clip:
        return JSONResponse({"error": "not found"}, 404)
    return FileResponse(clip["file_path"], media_type=clip["mime_type"])


@app.delete("/api/questionnaire/{qid}")
async def delete_questionnaire(qid: str):
    result = await db.close_questionnaire(qid)
    if not result:
        q = await db.get_questionnaire(qid)
        if not q:
            return JSONResponse({"error": "not found"}, 404)
        return JSONResponse({"error": "already closed", "closed_at": q["closed_at"].isoformat()}, 409)

    count = await db.get_response_count(qid)
    await broadcast(qid, "closed", {"questionnaire_id": qid, "closed_at": result["closed_at"].isoformat()})

    return {
        "id": qid,
        "closed_at": result["closed_at"].isoformat(),
        "response_count": count,
    }


@app.get("/")
async def index():
    rows = await db.list_questionnaires(active_only=True)
    questionnaires = [
        {**r, "age": relative_time(r["created_at"])}
        for r in rows
    ]
    html = INDEX_TEMPLATE.render(questionnaires=questionnaires, total=len(questionnaires))
    return HTMLResponse(html)


@app.get("/{qid}")
async def serve_questionnaire(qid: str):
    q = await db.get_questionnaire(qid)
    if not q:
        return HTMLResponse("<h1>Not found</h1>", 404)

    payload = json.loads(q["payload"]) if isinstance(q["payload"], str) else q["payload"]
    closed = q["closed_at"] is not None

    # For persistent types, sync initial_state with the latest response
    if q["is_persistent"]:
        latest = await db.get_responses(qid, latest=True)
        if latest:
            last_data = json.loads(latest[0]["response_data"]) if isinstance(latest[0]["response_data"], str) else latest[0]["response_data"]
            if "value" in last_data:
                payload["initial_state"] = last_data["value"]

    html = render_template(q["type"], qid, payload, closed=closed)
    return HTMLResponse(html)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
