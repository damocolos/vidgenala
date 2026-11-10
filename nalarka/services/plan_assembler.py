"""Two-way assembler between SQLite storyboard tables and in-memory plan dict."""
import json
import sqlite3
from typing import Dict, Any, Tuple, Optional, Callable

from ..core.scene_code import build_namespace, new_scene_sources
from ..db.repos.storyboards import update_beat_code

KNOWN_SCENE_KEYS = {
    "beat_id", "start", "end", "layout", "bg_color", "transition",
    "summary", "visual_blueprint", "dot_index", "lines",
}


def save_plan(
    conn: sqlite3.Connection,
    topic_id: int,
    plan: dict,
    *,
    script_id: Optional[int] = None,
    voiceover_id: Optional[int] = None,
    transcript_id: Optional[int] = None,
    ref_id: Optional[int] = None,
    coder_model: str = "default",
    is_current: bool = True,
) -> int:
    """Saves plan into storyboards, storyboard_helpers, and beats tables."""
    total_dur = int(plan.get("total_dur") or plan.get("total_duration") or 60)
    palette = plan.get("palette", {})

    with conn:
        if is_current:
            conn.execute("UPDATE storyboards SET is_current = 0 WHERE topic_id = ? AND is_current = 1", (topic_id,))

        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO storyboards (
                topic_id, script_id, voiceover_id, transcript_id, reference_item_id,
                coder_model, total_dur, palette_json, is_current, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                topic_id, script_id, voiceover_id, transcript_id, ref_id,
                coder_model, total_dur, json.dumps(palette, ensure_ascii=False),
                int(is_current),
            ),
        )
        sb_id = cur.lastrowid

        # Helpers
        store = plan.get("scene_sources") or new_scene_sources()
        helpers = store.get("helpers", {})
        for seq, (h_name, h_src) in enumerate(helpers.items()):
            cur.execute(
                """
                INSERT INTO storyboard_helpers (storyboard_id, name, seq, source)
                VALUES (?, ?, ?, ?)
                """,
                (sb_id, h_name, seq, h_src),
            )

        # Beats
        beats_code = store.get("beats", {})
        scenes = plan.get("scenes", [])
        for idx, sc in enumerate(scenes):
            beat_no = int(sc.get("beat_id", idx + 1))
            fn_key = f"beat_{beat_no:02d}"
            src_code = beats_code.get(fn_key)
            status = "ok" if src_code else "missing"

            extra = {k: v for k, v in sc.items() if k not in KNOWN_SCENE_KEYS}

            cur.execute(
                """
                INSERT INTO beats (
                    storyboard_id, beat_no, start_sec, end_sec, layout, bg_color,
                    transition, summary, visual_blueprint, dot_index, lines_json,
                    extra_json, source_code, code_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sb_id, beat_no, float(sc.get("start", 0.0)), float(sc.get("end", 0.0)),
                    sc.get("layout"), sc.get("bg_color"), sc.get("transition"),
                    sc.get("summary"), sc.get("visual_blueprint"),
                    sc.get("dot_index"),
                    json.dumps(sc.get("lines"), ensure_ascii=False) if sc.get("lines") is not None else None,
                    json.dumps(extra, ensure_ascii=False) if extra else None,
                    src_code, status,
                ),
            )

    return sb_id


def load_plan(
    conn: sqlite3.Connection,
    storyboard_id: int,
    log_fn: Optional[Callable[[str], None]] = None,
) -> Tuple[dict, Dict[str, Any]]:
    """Reconstructs in-memory plan dict and builds executable scene namespace."""
    cur = conn.cursor()
    cur.execute("SELECT * FROM storyboards WHERE id = ?", (storyboard_id,))
    sb_row = cur.fetchone()
    if not sb_row:
        raise ValueError(f"Storyboard id {storyboard_id} not found.")

    total_dur = int(sb_row["total_dur"])
    palette = json.loads(sb_row["palette_json"])

    # Load helpers in exact seq order
    cur.execute("SELECT name, source FROM storyboard_helpers WHERE storyboard_id = ? ORDER BY seq ASC", (storyboard_id,))
    helpers = {r["name"]: r["source"] for r in cur.fetchall()}

    # Load beats in beat_no order
    cur.execute("SELECT * FROM beats WHERE storyboard_id = ? ORDER BY beat_no ASC", (storyboard_id,))
    beats_rows = cur.fetchall()

    scenes = []
    beats_code = {}
    for r in beats_rows:
        beat_no = int(r["beat_no"])
        extra = json.loads(r["extra_json"]) if r["extra_json"] else {}
        sc: Dict[str, Any] = dict(extra)
        sc["beat_id"] = beat_no
        sc["start"] = float(r["start_sec"])
        sc["end"] = float(r["end_sec"])
        sc["bg_color"] = r["bg_color"]
        sc["layout"] = r["layout"]
        if r["dot_index"] is not None:
            sc["dot_index"] = int(r["dot_index"])
        sc["transition"] = r["transition"]
        sc["summary"] = r["summary"]
        sc["visual_blueprint"] = r["visual_blueprint"]
        if r["lines_json"] is not None:
            sc["lines"] = json.loads(r["lines_json"])
        scenes.append(sc)

        if r["source_code"]:
            beats_code[f"beat_{beat_no:02d}"] = r["source_code"]

    plan = {
        "total_dur": total_dur,
        "total_duration": total_dur,
        "palette": palette,
        "scenes": scenes,
        "scene_sources": {
            "helpers": helpers,
            "beats": beats_code,
        },
    }

    effective_log = log_fn if log_fn is not None else (lambda m: None)
    namespace = build_namespace(plan["scene_sources"], effective_log)
    return plan, namespace


def sync_code(conn: sqlite3.Connection, storyboard_id: int, plan: dict, reason: str = "generated") -> int:
    """Writes the beat/helper code currently held in ``plan`` into an EXISTING storyboard (in place).

    Only rows whose code actually changed are touched, so calling this after every AI batch or
    audit fix is cheap and never creates a second storyboard. Returns the number of beats changed.
    """
    store = plan.get("scene_sources") or new_scene_sources()
    changed = 0
    # beat_revisions.reason is CHECK-constrained to pass2 / audit / reroll
    rev_reason = {"generated": "pass2", "pass2": "pass2", "audit": "audit", "final": "audit", "reroll": "reroll"}.get(reason, "pass2")

    cur = conn.cursor()
    cur.execute("SELECT name, source FROM storyboard_helpers WHERE storyboard_id = ?", (storyboard_id,))
    saved_helpers = {r["name"]: r["source"] for r in cur.fetchall()}
    with conn:
        for seq, (name, src) in enumerate(store.get("helpers", {}).items()):
            if saved_helpers.get(name) != src:
                conn.execute(
                    """
                    INSERT INTO storyboard_helpers (storyboard_id, name, seq, source)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(storyboard_id, name) DO UPDATE SET seq = excluded.seq, source = excluded.source
                    """,
                    (storyboard_id, name, seq, src),
                )

    cur.execute("SELECT id, beat_no, source_code FROM beats WHERE storyboard_id = ?", (storyboard_id,))
    rows = {int(r["beat_no"]): (int(r["id"]), r["source_code"]) for r in cur.fetchall()}
    for beat_no, (beat_id, saved_src) in rows.items():
        src = store.get("beats", {}).get(f"beat_{beat_no:02d}")
        if src and src != saved_src:
            update_beat_code(conn, beat_id, src, "ok", reason=rev_reason)
            changed += 1
    return changed


def count_saved_beats(conn: sqlite3.Connection, storyboard_id: int) -> Tuple[int, int]:
    """(beats that have code, total beats) for a storyboard."""
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) AS total, SUM(CASE WHEN source_code IS NOT NULL AND source_code != '' THEN 1 ELSE 0 END) AS done "
        "FROM beats WHERE storyboard_id = ?",
        (storyboard_id,),
    )
    r = cur.fetchone()
    return int(r["done"] or 0), int(r["total"] or 0)


def missing_beat_indices(plan: dict, namespace: Dict[str, Any]) -> list:
    """Indices (0-based, into plan['scenes']) whose beat function is not saved AND callable."""
    beats = (plan.get("scene_sources") or {}).get("beats", {})
    out = []
    for i, sc in enumerate(plan.get("scenes", [])):
        name = f"beat_{int(sc.get('beat_id', i + 1)):02d}"
        if not beats.get(name) or not callable(namespace.get(name)):
            out.append(i)
    return out
