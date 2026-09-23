"""
Replay Vault & Match Exporter Engine (`replays.db`) (Phase 86)
Stores full Bo3 match step snapshots, decklists (.ydk format),
and provides a decoupled, deterministic Annotation Overlay Engine.
"""

import os
import json
import time
import random
import sqlite3
import threading
import logging
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger("replay_engine")
logger.setLevel(logging.INFO)

def to_ydk(main: list, extra: list, side: list, deck_name: str = "Deck") -> str:
    """Formats main, extra, and side card IDs into official .ydk format."""
    lines = [f"#created by Aethelburg Prophit Engine - {deck_name}", "#main"]
    for cid in main:
        if cid:
            lines.append(str(cid))
    lines.append("#extra")
    for cid in extra:
        if cid:
            lines.append(str(cid))
    lines.append("!side")
    for cid in side:
        if cid:
            lines.append(str(cid))
    return "\n".join(lines) + "\n"


class ReplayEngine:
    def __init__(self, db_path: Optional[str] = None):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        if db_path is None:
            db_path = os.path.join(base_dir, "replays.db")
        self.db_path = db_path
        self.lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self):
        with self.lock:
            conn = self._get_connection()
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS replays (
                        id TEXT PRIMARY KEY,
                        room_id TEXT NOT NULL,
                        tournament_id TEXT,
                        player0_id TEXT NOT NULL,
                        player0_name TEXT NOT NULL,
                        player0_deck_name TEXT NOT NULL,
                        player1_id TEXT NOT NULL,
                        player1_name TEXT NOT NULL,
                        player1_deck_name TEXT NOT NULL,
                        player0_ydk TEXT,
                        player1_ydk TEXT,
                        score TEXT NOT NULL,
                        winner_id TEXT,
                        total_games INTEGER DEFAULT 1,
                        total_steps INTEGER DEFAULT 0,
                        frames_json TEXT NOT NULL,
                        created_at REAL NOT NULL
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_replays_room ON replays(room_id);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_replays_tournament ON replays(tournament_id);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_replays_created ON replays(created_at DESC);")

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS replay_annotations (
                        id TEXT PRIMARY KEY,
                        replay_id TEXT NOT NULL,
                        game_index INTEGER NOT NULL,
                        turn_num INTEGER NOT NULL,
                        step_index INTEGER NOT NULL,
                        author_name TEXT NOT NULL,
                        tag TEXT NOT NULL,
                        comment TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        FOREIGN KEY (replay_id) REFERENCES replays(id) ON DELETE CASCADE
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ann_replay ON replay_annotations(replay_id, step_index);")
                conn.commit()
            finally:
                conn.close()

    def save_replay(
        self,
        room_id: str,
        tournament_id: Optional[str],
        player0_id: str,
        player0_name: str,
        player0_deck_name: str,
        player1_id: str,
        player1_name: str,
        player1_deck_name: str,
        player0_deck_raw: dict,
        player1_deck_raw: dict,
        score: str,
        winner_id: Optional[str],
        total_games: int,
        frames: List[dict]
    ) -> str:
        """Stores a complete match replay with board frames and .ydk decklists."""
        rep_id = f"rep_{int(time.time())}_{random.randint(1000, 9999)}"
        now = time.time()

        p0_ydk = to_ydk(
            player0_deck_raw.get("main", []),
            player0_deck_raw.get("extra", []),
            player0_deck_raw.get("side", []),
            player0_deck_name
        )
        p1_ydk = to_ydk(
            player1_deck_raw.get("main", []),
            player1_deck_raw.get("extra", []),
            player1_deck_raw.get("side", []),
            player1_deck_name
        )

        frames_str = json.dumps(frames)

        with self.lock:
            conn = self._get_connection()
            try:
                conn.execute("""
                    INSERT INTO replays (
                        id, room_id, tournament_id,
                        player0_id, player0_name, player0_deck_name,
                        player1_id, player1_name, player1_deck_name,
                        player0_ydk, player1_ydk,
                        score, winner_id, total_games, total_steps,
                        frames_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    rep_id, room_id, tournament_id,
                    player0_id, player0_name, player0_deck_name,
                    player1_id, player1_name, player1_deck_name,
                    p0_ydk, p1_ydk,
                    score, winner_id, total_games, len(frames),
                    frames_str, now
                ))
                conn.commit()
                logger.info(f"[REPLAY] Saved replay {rep_id} ({len(frames)} frames) for room {room_id}.")
                return rep_id
            finally:
                conn.close()

    def get_replay(self, replay_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a full replay including all step frames and annotations."""
        with self.lock:
            conn = self._get_connection()
            try:
                row = conn.execute("SELECT * FROM replays WHERE id = ?", (replay_id,)).fetchone()
                if not row:
                    return None
                r = dict(row)
                frames = json.loads(r["frames_json"])

                ann_rows = conn.execute("""
                    SELECT * FROM replay_annotations 
                    WHERE replay_id = ?
                    ORDER BY step_index ASC, created_at ASC
                """, (replay_id,)).fetchall()
                annotations = [dict(a) for a in ann_rows]

                r["replay_id"] = r["id"]
                r["frames"] = frames
                r["frames_count"] = len(frames)
                r["annotations"] = annotations
                r["annotations_count"] = len(annotations)
                del r["frames_json"]
                return r
            finally:
                conn.close()

    def list_replays(
        self,
        tournament_id: Optional[str] = None,
        player_filter: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Returns metadata list of recent replays without the heavy frames payload."""
        with self.lock:
            conn = self._get_connection()
            try:
                query = """
                    SELECT r.id, r.room_id, r.tournament_id,
                           r.player0_id, r.player0_name, r.player0_deck_name,
                           r.player1_id, r.player1_name, r.player1_deck_name,
                           r.score, r.winner_id, r.total_games, r.total_steps,
                           r.created_at, COUNT(a.id) as annotation_count
                    FROM replays r
                    LEFT JOIN replay_annotations a ON r.id = a.replay_id
                """
                params = []
                where_clauses = []
                if tournament_id:
                    where_clauses.append("r.tournament_id = ?")
                    params.append(tournament_id)
                if player_filter:
                    where_clauses.append("(r.player0_name LIKE ? OR r.player1_name LIKE ? OR r.player0_id = ? OR r.player1_id = ?)")
                    p_like = f"%{player_filter}%"
                    params.extend([p_like, p_like, player_filter, player_filter])

                if where_clauses:
                    query += " WHERE " + " AND ".join(where_clauses)

                query += " GROUP BY r.id ORDER BY r.created_at DESC LIMIT ?"
                params.append(limit)

                rows = conn.execute(query, params).fetchall()
                results = []
                for row in rows:
                    item = dict(row)
                    item["replay_id"] = item["id"]
                    item["frames_count"] = item.get("total_steps", 0)
                    item["annotations_count"] = item.get("annotation_count", 0)
                    results.append(item)
                return results
            finally:
                conn.close()

    def add_annotation(
        self,
        replay_id: str,
        game_index: int,
        turn_num: int,
        step_index: int,
        author_name: str,
        tag: str,
        comment: str
    ) -> Dict[str, Any]:
        """Attaches a coach / player annotation to a deterministic step in the replay."""
        clean_tag = tag.strip().upper()
        if clean_tag not in ["BLUNDER", "KEY_PLAY", "SIDEDECK_NOTE", "RULE_QUESTION"]:
            clean_tag = "KEY_PLAY"

        clean_author = (author_name or "Anonymous").strip()[:32]
        clean_comment = comment.strip()
        if not clean_comment:
            raise ValueError("Comment text cannot be empty.")

        ann_id = f"ann_{int(time.time())}_{random.randint(100, 999)}"
        now = time.time()

        with self.lock:
            conn = self._get_connection()
            try:
                # Verify replay exists
                exists = conn.execute("SELECT id FROM replays WHERE id = ?", (replay_id,)).fetchone()
                if not exists:
                    raise ValueError(f"Replay {replay_id} not found.")

                conn.execute("""
                    INSERT INTO replay_annotations (
                        id, replay_id, game_index, turn_num, step_index,
                        author_name, tag, comment, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    ann_id, replay_id, game_index, turn_num, step_index,
                    clean_author, clean_tag, clean_comment, now
                ))
                conn.commit()
                return {
                    "id": ann_id,
                    "replay_id": replay_id,
                    "game_index": game_index,
                    "turn_num": turn_num,
                    "step_index": step_index,
                    "author_name": clean_author,
                    "tag": clean_tag,
                    "comment": clean_comment,
                    "created_at": now
                }
            finally:
                conn.close()

    def delete_annotation(self, annotation_id: str) -> bool:
        """Deletes an annotation by ID."""
        with self.lock:
            conn = self._get_connection()
            try:
                cur = conn.execute("DELETE FROM replay_annotations WHERE id = ?", (annotation_id,))
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def get_ydk(self, replay_id: str, player_idx: int) -> Optional[Tuple[str, str]]:
        """Returns (filename, ydk_text) for a player in a replay."""
        with self.lock:
            conn = self._get_connection()
            try:
                row = conn.execute("SELECT * FROM replays WHERE id = ?", (replay_id,)).fetchone()
                if not row:
                    return None
                if player_idx == 0:
                    pname = row["player0_name"]
                    dname = row["player0_deck_name"]
                    ydk = row["player0_ydk"] or ""
                else:
                    pname = row["player1_name"]
                    dname = row["player1_deck_name"]
                    ydk = row["player1_ydk"] or ""

                safe_p = "".join([c for c in pname if c.isalnum() or c in ['_', '-']])
                safe_d = "".join([c for c in dname if c.isalnum() or c in ['_', '-']])
                filename = f"{safe_p}_{safe_d}.ydk"
                return filename, ydk
            finally:
                conn.close()


# Global Singleton
replay_engine = ReplayEngine()
