"""AI mode: private chats with file uploads, grounded in the user's files and the legal library."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from flask import Blueprint, g, jsonify, request

from lawpilot import activity, assistant
from lawpilot.auth import ApiError, load_matter, require_perm
from lawpilot.db import get_db, new_id, now, query_all, query_one
from lawpilot.extract import ExtractionError, extract
from lawpilot.ingestion import delete_source, ingest
from lawpilot.routes import body, cfg, llm, need

bp = Blueprint("ai", __name__, url_prefix="/api/ai")

MAX_FILES_PER_CHAT = 20
MAX_MESSAGE_CHARS = 12000
FILE_COLUMNS = "id, title, original_filename, page_count, status, security_flags, created_at"


def _ts() -> str:
    """Message timestamps keep microseconds so a question always sorts before its answer."""
    return datetime.now(timezone.utc).isoformat()


def _load_chat(chat_id: str) -> dict:
    """Chats are private to their owner; anyone else gets a 404."""
    chat = query_one("SELECT * FROM ai_chats WHERE id = ? AND user_id = ?", (chat_id, g.user["id"]))
    if not chat:
        raise ApiError("Chat not found.", 404)
    return chat


def _chat_files(chat_id: str) -> list:
    return query_all(f"SELECT {FILE_COLUMNS} FROM sources WHERE chat_id = ? ORDER BY created_at", (chat_id,))


def _linked_matter(chat: dict):
    """The chat's matter, if the user can still open it (access may have been revoked since linking)."""
    if not chat.get("matter_id"):
        return None
    try:
        return load_matter(chat["matter_id"])
    except ApiError:
        return None


def _public_chat(chat: dict) -> dict:
    out = dict(chat)
    out["use_library"] = bool(out["use_library"])
    out["pinned"] = bool(out["pinned"])
    m = _linked_matter(chat)
    out["matter"] = {"id": m["id"], "title": m["title"]} if m else None
    return out


@bp.get("/chats")
@require_perm("research.run")
def list_chats():
    q = (request.args.get("q") or "").strip()
    clauses, params = ["c.user_id = ?"], [g.user["id"]]
    if q:
        clauses.append("(c.title LIKE ? OR EXISTS (SELECT 1 FROM ai_messages m WHERE m.chat_id = c.id AND m.content LIKE ?))")
        params += [f"%{q}%"] * 2
    rows = query_all(f"SELECT c.id, c.title, c.pinned, c.matter_id, c.updated_at, c.created_at FROM ai_chats c "
                     f"WHERE {' AND '.join(clauses)} ORDER BY c.pinned DESC, c.updated_at DESC LIMIT 200", params)
    for r in rows:
        r["pinned"] = bool(r["pinned"])
    return jsonify(items=rows)


@bp.post("/chats")
@require_perm("research.run")
def create_chat():
    data = body()
    matter_id = data.get("matter_id") or None
    if matter_id:
        load_matter(matter_id)
    ts = now()
    chat = {"id": new_id(), "user_id": g.user["id"], "title": (data.get("title") or "New chat").strip()[:120],
            "matter_id": matter_id, "use_library": 1 if data.get("use_library", True) else 0, "pinned": 0,
            "created_at": ts, "updated_at": ts}
    db = get_db()
    db.execute("INSERT INTO ai_chats (id, user_id, title, matter_id, use_library, pinned, created_at, updated_at) "
               "VALUES (?,?,?,?,?,?,?,?)", tuple(chat.values()))
    db.commit()
    return jsonify(chat=_public_chat(chat), messages=[], files=[]), 201


@bp.get("/chats/<chat_id>")
@require_perm("research.run")
def get_chat(chat_id):
    chat = _load_chat(chat_id)
    messages = query_all("SELECT id, role, content, citations, attachments, extra, model, created_at FROM ai_messages "
                         "WHERE chat_id = ? ORDER BY created_at", (chat_id,))
    return jsonify(chat=_public_chat(chat), messages=messages, files=_chat_files(chat_id))


@bp.patch("/chats/<chat_id>")
@require_perm("research.run")
def update_chat(chat_id):
    chat = _load_chat(chat_id)
    data = body()
    sets, params = [], []
    if "title" in data:
        title = str(data["title"] or "").strip()[:120]
        if not title:
            raise ApiError("Title cannot be empty.")
        sets.append("title = ?")
        params.append(title)
    if "pinned" in data:
        sets.append("pinned = ?")
        params.append(1 if data["pinned"] else 0)
    if "use_library" in data:
        sets.append("use_library = ?")
        params.append(1 if data["use_library"] else 0)
    if "matter_id" in data:
        if data["matter_id"]:
            load_matter(data["matter_id"])
        sets.append("matter_id = ?")
        params.append(data["matter_id"] or None)
    if sets:
        db = get_db()
        db.execute(f"UPDATE ai_chats SET {', '.join(sets)} WHERE id = ?", params + [chat_id])
        db.commit()
    return jsonify(chat=_public_chat(_load_chat(chat["id"])))


@bp.delete("/chats/<chat_id>")
@require_perm("research.run")
def delete_chat(chat_id):
    _load_chat(chat_id)
    db = get_db()
    for f in _chat_files(chat_id):
        delete_source(db, cfg(), f["id"])
    db.execute("DELETE FROM ai_messages WHERE chat_id = ?", (chat_id,))
    db.execute("DELETE FROM ai_chats WHERE id = ?", (chat_id,))
    db.commit()
    return jsonify(ok=True)


# ── Files ──────────────────────────────────────────────────────────────────

@bp.post("/chats/<chat_id>/files")
@require_perm("research.run")
def upload_files(chat_id):
    chat = _load_chat(chat_id)
    uploads = [f for f in request.files.getlist("file") if f and f.filename]
    if not uploads:
        raise ApiError("Choose a file to upload.")
    existing = len(_chat_files(chat_id))
    if existing + len(uploads) > MAX_FILES_PER_CHAT:
        raise ApiError(f"A chat can hold up to {MAX_FILES_PER_CHAT} files. Start a new chat for more.")
    db = get_db()
    results = []
    for f in uploads:
        data = f.read()
        try:
            pages = extract(data, f.filename)
        except ExtractionError as exc:
            results.append({"filename": f.filename, "status": "failed", "message": str(exc)})
            continue
        title = f.filename.rsplit(".", 1)[0][:300] or f.filename
        # The file itself is not kept (only its text), so chats stay light on serverless hosts.
        res = ingest(db, cfg(), pages, {"title": title, "kind": "user_document", "authority_class": "user_upload",
                                        "verification": "unverified"},
                     origin="chat_upload", user_id=g.user["id"], filename=f.filename, mime=f.mimetype, chat_id=chat_id)
        results.append({"filename": f.filename, "status": res.status, "message": res.message, "id": res.source_id})
    if any(r["status"] == "succeeded" for r in results):
        db.execute("UPDATE ai_chats SET updated_at = ? WHERE id = ?", (now(), chat["id"]))
        db.commit()
        activity.log("ai.file_uploaded", f"Added {sum(r['status'] == 'succeeded' for r in results)} file(s) to an AI chat",
                     "ai_chat", chat_id)
    return jsonify(results=results, files=_chat_files(chat_id))


@bp.delete("/chats/<chat_id>/files/<source_id>")
@require_perm("research.run")
def delete_file(chat_id, source_id):
    _load_chat(chat_id)
    if not query_one("SELECT 1 FROM sources WHERE id = ? AND chat_id = ?", (source_id, chat_id)):
        raise ApiError("File not found.", 404)
    delete_source(get_db(), cfg(), source_id)
    return jsonify(ok=True, files=_chat_files(chat_id))


# ── Messages ───────────────────────────────────────────────────────────────

def _history(chat_id: str) -> list:
    return query_all("SELECT id, role, content FROM ai_messages WHERE chat_id = ? ORDER BY created_at", (chat_id,))


def _respond(chat: dict, question: str, attachment_ids: list, history: list) -> dict:
    files = _chat_files(chat["id"])
    ready = [f for f in files if f["status"] == "ready"]
    by_id = {f["id"]: f for f in ready}
    attachments = [by_id[a] for a in attachment_ids if a in by_id]
    result = assistant.answer(
        get_db(), cfg(), llm(), question=question, history=history, files=ready, attachments=attachments,
        use_library=bool(chat["use_library"]), matter=_linked_matter(chat), chat_id=chat["id"],
        language=(g.user.get("prefs") or {}).get("language", "en"))
    extra = {k: result.get(k) for k in ("mode", "query", "retrieval", "notes", "verification", "cited", "error")
             if result.get(k) is not None}
    msg = {"id": new_id(), "chat_id": chat["id"], "role": "assistant", "content": result["content"],
           "citations": assistant.public_sources(result["sources"]), "attachments": [], "extra": extra,
           "model": result.get("model"), "created_at": _ts()}
    db = get_db()
    db.execute("INSERT INTO ai_messages (id, chat_id, role, content, citations, attachments, extra, model, created_at) "
               "VALUES (?,?,?,?,?,?,?,?,?)",
               (msg["id"], chat["id"], "assistant", msg["content"], json.dumps(msg["citations"]), "[]",
                json.dumps(extra), msg["model"], msg["created_at"]))
    db.execute("UPDATE ai_chats SET updated_at = ? WHERE id = ?", (msg["created_at"], chat["id"]))
    db.commit()
    return msg


@bp.post("/chats/<chat_id>/messages")
@require_perm("research.run")
def send_message(chat_id):
    chat = _load_chat(chat_id)
    data = body()
    need(data, "content")
    question = data["content"].strip()
    if len(question) > MAX_MESSAGE_CHARS:
        raise ApiError(f"Message is too long ({MAX_MESSAGE_CHARS} characters max).")
    attachment_ids = [a for a in (data.get("attachments") or []) if isinstance(a, str)][:10]
    files = {f["id"]: f for f in _chat_files(chat_id)}
    attachments = [{"id": a, "title": files[a]["title"]} for a in attachment_ids if a in files]
    history = _history(chat_id)

    db = get_db()
    user_msg = {"id": new_id(), "chat_id": chat_id, "role": "user", "content": question, "citations": [],
                "attachments": attachments, "extra": {}, "model": None, "created_at": _ts()}
    db.execute("INSERT INTO ai_messages (id, chat_id, role, content, citations, attachments, extra, model, created_at) "
               "VALUES (?,?,?,?,?,?,?,?,?)", (user_msg["id"], chat_id, "user", question, "[]",
                                              json.dumps(attachments), "{}", None, user_msg["created_at"]))
    if not history and chat["title"] in ("New chat", ""):
        db.execute("UPDATE ai_chats SET title = ? WHERE id = ?", (assistant.chat_title(question), chat_id))
    db.commit()

    reply = _respond(chat, question, [a["id"] for a in attachments], history)
    activity.log("ai.message", f"AI mode: {question[:120]}", "ai_chat", chat_id)
    return jsonify(user_message=user_msg, message=reply, chat=_public_chat(_load_chat(chat_id)))


@bp.post("/chats/<chat_id>/regenerate")
@require_perm("research.run")
def regenerate(chat_id):
    chat = _load_chat(chat_id)
    history = _history(chat_id)
    if history and history[-1]["role"] == "assistant":
        db = get_db()
        db.execute("DELETE FROM ai_messages WHERE id = ?", (history[-1]["id"],))
        db.commit()
        history = history[:-1]
    if not history or history[-1]["role"] != "user":
        raise ApiError("There is no question to answer again.")
    last = query_one("SELECT content, attachments FROM ai_messages WHERE id = ?", (history[-1]["id"],))
    reply = _respond(chat, last["content"], [a["id"] for a in last["attachments"] or []], history[:-1])
    return jsonify(message=reply, chat=_public_chat(_load_chat(chat_id)))
