"""Parse a JSONL event stream into a structured activity summary.

Used by peek_codex / wait_for_codex / poll_codex_job to report what codex has
done so far — agent messages, tool calls, file changes, retry count — without
needing to monitor the codex process directly.
"""
from __future__ import annotations

import json
from pathlib import Path


def parse_codex_activity(stream_path: Path) -> dict:
    activity = {
        "agent_messages": [],
        "tool_calls": [],
        "file_changes": [],
        "reasoning_excerpts": [],
        "errors": [],
        "retry_count": 0,
    }
    if not stream_path.exists():
        return activity
    try:
        with stream_path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                t = e.get("type", "")
                if t == "error":
                    msg = e.get("message", "")
                    if "Reconnecting" in msg or "stream disconnected" in msg:
                        activity["retry_count"] += 1
                    else:
                        activity["errors"].append({"type": "error", "message": msg[:500]})
                elif t == "turn.failed":
                    activity["errors"].append(
                        {"type": "turn.failed", "message": e.get("message", "")[:500]}
                    )
                elif t == "item.completed":
                    item = e.get("item", {}) or {}
                    it = item.get("type", "")
                    text = item.get("text") or item.get("message") or ""
                    if it == "agent_message" and text:
                        activity["agent_messages"].append(text)
                    elif it == "reasoning" and text:
                        activity["reasoning_excerpts"].append(text[:600])
                    elif it in ("function_call", "tool_call", "command_execution"):
                        activity["tool_calls"].append(
                            {
                                "kind": it,
                                "name": item.get("name") or item.get("tool") or "",
                                "summary": str(
                                    item.get("arguments")
                                    or item.get("args")
                                    or item.get("command")
                                    or ""
                                )[:300],
                            }
                        )
                    elif it == "file_change":
                        # Codex JSONL schema: item.changes = [{path, kind}, ...]
                        changes = item.get("changes") or []
                        if changes:
                            for ch in changes:
                                activity["file_changes"].append(
                                    {
                                        "path": ch.get("path") or ch.get("file") or "",
                                        "action": ch.get("kind") or ch.get("action") or "",
                                    }
                                )
                        else:
                            activity["file_changes"].append(
                                {
                                    "path": item.get("path") or item.get("file") or "",
                                    "action": item.get("kind") or item.get("action") or "",
                                }
                            )
                    else:
                        activity["tool_calls"].append(
                            {"kind": it or "?", "name": "", "summary": str(item)[:300]}
                        )
    except Exception:
        pass
    return activity


def parse_rollout_activity(rollout_path: Path) -> dict:
    """Parse codex's native session rollout jsonl.

    Schema is richer than `codex exec --json`: response_item carries the
    actual shell command + output, event_msg/patch_apply_end carries the
    unified diff per file, event_msg/task_complete carries the final agent
    message and signals completion.

    Returns the same dict shape as parse_codex_activity, with two extras:
      - completed: bool (task_complete seen)
      - final_message: last_agent_message from task_complete
    """
    activity = {
        "agent_messages": [],
        "tool_calls": [],
        "file_changes": [],
        "reasoning_excerpts": [],
        "errors": [],
        "retry_count": 0,
        "completed": False,
        "final_message": "",
    }
    if not rollout_path.exists():
        return activity
    try:
        with rollout_path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                t = e.get("type", "")
                payload = e.get("payload") if isinstance(e.get("payload"), dict) else {}
                pt = payload.get("type", "")

                if t == "event_msg":
                    if pt == "agent_message":
                        msg = payload.get("message") or ""
                        if msg:
                            activity["agent_messages"].append(msg)
                    elif pt == "task_complete":
                        activity["completed"] = True
                        activity["final_message"] = payload.get("last_agent_message") or ""
                    elif pt == "patch_apply_end":
                        changes = payload.get("changes") or {}
                        if isinstance(changes, dict):
                            for path, info in changes.items():
                                kind = (info or {}).get("type", "update") if isinstance(info, dict) else "update"
                                activity["file_changes"].append({
                                    "path": path,
                                    "action": kind,
                                })
                        if not payload.get("success", True):
                            activity["errors"].append({
                                "type": "patch_apply_failed",
                                "message": (payload.get("stderr") or "")[:500],
                            })
                    elif pt == "error":
                        activity["errors"].append({
                            "type": "error",
                            "message": str(payload.get("message", ""))[:500],
                        })

                elif t == "response_item":
                    if pt == "function_call":
                        name = payload.get("name", "")
                        args = payload.get("arguments", "")
                        if isinstance(args, dict):
                            args = json.dumps(args, ensure_ascii=False)
                        activity["tool_calls"].append({
                            "kind": "function_call",
                            "name": name,
                            "summary": str(args)[:300],
                        })
                    elif pt == "custom_tool_call":
                        name = payload.get("name", "")
                        inp = payload.get("input", "")
                        activity["tool_calls"].append({
                            "kind": "custom_tool_call",
                            "name": name,
                            "summary": str(inp)[:300],
                        })
                    elif pt == "reasoning":
                        # Rollout reasoning items often have only encrypted_content;
                        # the visible summary lives in `summary` (list of {text}).
                        summary = payload.get("summary") or []
                        if isinstance(summary, list) and summary:
                            for s in summary:
                                if isinstance(s, dict):
                                    txt = s.get("text") or ""
                                elif isinstance(s, str):
                                    txt = s
                                else:
                                    txt = ""
                                if txt:
                                    activity["reasoning_excerpts"].append(txt[:600])

    except Exception:
        pass
    return activity


def peek_last_jsonl_event(stream_path: Path) -> dict:
    """Look at the tail of the stream and return the most recent meaningful event."""
    info = {
        "last_event_type": "",
        "last_item_type": "",
        "last_item_status": "",
        "last_item_summary": "",
    }
    try:
        with stream_path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 16384))
            tail = f.read().decode("utf-8", errors="replace")
        lines = [l for l in tail.split("\n") if l.strip()]
        for line in reversed(lines):
            try:
                e = json.loads(line)
            except Exception:
                continue
            t = e.get("type", "")
            if t == "error" and "Reconnecting" in str(e.get("message", "")):
                continue
            info["last_event_type"] = t
            item = e.get("item") or {}
            if item:
                info["last_item_type"] = item.get("type", "") or ""
                info["last_item_status"] = item.get("status", "") or ""
                summary = (
                    item.get("command")
                    or item.get("name")
                    or item.get("text")
                    or item.get("arguments")
                    or ""
                )
                info["last_item_summary"] = str(summary)[:200]
            break
    except Exception:
        pass
    return info
