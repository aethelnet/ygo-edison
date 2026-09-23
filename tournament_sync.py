"""
tournament_sync.py
------------------
Local SQLite Authority & Cloud Delta-Sync Protocol for Yu-Gi-Oh Store Locals (Phase 94).
Enables local Edge Nodes (Stefan's Laptop / Local Store LAN) to run fully offline
and reliably push completed tournament states, deck snapshots, match results,
and audit logs to the centralized cloud server (server-141 / play.aethelburg.network).
"""

import os
import time
import json
import sqlite3
import urllib.request
import urllib.error
from typing import Dict, Any, Optional, List
from loguru import logger

try:
    from tournament_engine import TournamentEngine, tournament_engine
except ImportError:
    from apps.ygo_rl.tournament_engine import TournamentEngine, tournament_engine

try:
    from replay_engine import ReplayEngine
except ImportError:
    try:
        from apps.ygo_rl.replay_engine import ReplayEngine
    except ImportError:
        ReplayEngine = None



def get_sqlite_columns(conn: sqlite3.Connection, table_name: str) -> List[str]:
    """Returns list of column names for a given SQLite table."""
    cursor = conn.execute(f"PRAGMA table_info({table_name});")
    return [row[1] for row in cursor.fetchall()]


def export_tournament_bundle(tournament_id: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Serializes a tournament and all its related records (players, matches, audit logs)
    into a portable JSON-compatible bundle dictionary.
    """
    if db_path is None:
        db_path = tournament_engine.db_path

    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found at {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()

        # 1. Fetch Tournament
        cur.execute("SELECT * FROM tournaments WHERE id = ?", (tournament_id,))
        t_row = cur.fetchone()
        if not t_row:
            raise ValueError(f"Tournament '{tournament_id}' not found in database {db_path}")
        tournament_data = dict(t_row)

        # 2. Fetch Players
        cur.execute("SELECT * FROM tournament_players WHERE tournament_id = ? ORDER BY match_points DESC", (tournament_id,))
        players_data = [dict(r) for r in cur.fetchall()]

        # 3. Fetch Matches
        cur.execute("SELECT * FROM tournament_matches WHERE tournament_id = ? ORDER BY round_num ASC, table_num ASC", (tournament_id,))
        matches_data = [dict(r) for r in cur.fetchall()]

        # 4. Fetch Audit Logs
        cur.execute("SELECT * FROM tournament_audit_log WHERE tournament_id = ? ORDER BY id ASC", (tournament_id,))
        audit_data = [dict(r) for r in cur.fetchall()]

        # 5. Fetch Replays if available in replays.db
        base_dir = os.path.dirname(os.path.abspath(db_path))
        replay_db = os.path.join(base_dir, "replays.db")
        replays_data = []
        if os.path.exists(replay_db):
            try:
                r_conn = sqlite3.connect(replay_db)
                r_conn.row_factory = sqlite3.Row
                r_cur = r_conn.cursor()
                r_cur.execute("SELECT * FROM replays WHERE tournament_id = ?", (tournament_id,))
                replays_data = [dict(r) for r in r_cur.fetchall()]
                r_conn.close()
            except Exception as e:
                logger.warning(f"[SYNC] Error loading replays for {tournament_id}: {e}")

        bundle = {
            "version": "1.0",
            "exported_at": time.time(),
            "tournament_id": tournament_id,
            "tournament": tournament_data,
            "players": players_data,
            "matches": matches_data,
            "audit_logs": audit_data,
            "replays": replays_data,
            "metadata": {
                "player_count": len(players_data),
                "match_count": len(matches_data),
                "audit_count": len(audit_data),
                "replay_count": len(replays_data),
                "status": tournament_data.get("status"),
                "stage": tournament_data.get("stage"),
                "champion_name": tournament_data.get("champion_name"),
                "source_host": os.uname().nodename if hasattr(os, "uname") else "local-edge"
            }
        }
        logger.info(f"[SYNC] Exported tournament {tournament_id}: {len(players_data)} players, {len(matches_data)} matches, {len(audit_data)} audit entries, {len(replays_data)} replays.")
        return bundle
    finally:
        conn.close()


def import_tournament_bundle(
    bundle: Dict[str, Any],
    db_path: Optional[str] = None,
    overwrite: bool = True
) -> Dict[str, Any]:
    """
    Atomically imports a tournament bundle into the target SQLite database.
    If overwrite is True, replaces any pre-existing records for that tournament_id.
    """
    version = bundle.get("version")
    if version != "1.0":
        raise ValueError(f"Unsupported bundle version: {version}. Expected '1.0'")

    t_id = bundle.get("tournament_id")
    if not t_id:
        raise ValueError("Bundle missing 'tournament_id'")

    t_data = bundle.get("tournament")
    if not t_data or not isinstance(t_data, dict):
        raise ValueError("Bundle missing valid 'tournament' record")

    players = bundle.get("players", [])
    matches = bundle.get("matches", [])
    audit_logs = bundle.get("audit_logs", [])

    if db_path is None:
        db_path = tournament_engine.db_path

    # Ensure target engine schema is initialized
    engine = TournamentEngine(db_path=db_path)
    engine._init_db()

    conn = sqlite3.connect(db_path, timeout=15.0)
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        with conn:
            cur = conn.cursor()

            # Check existing tournament
            cur.execute("SELECT id, status FROM tournaments WHERE id = ?", (t_id,))
            existing = cur.fetchone()

            if existing:
                if not overwrite:
                    return {
                        "status": "SKIPPED",
                        "tournament_id": t_id,
                        "message": f"Tournament '{t_id}' already exists and overwrite is False."
                    }
                logger.warning(f"[SYNC] Overwriting existing tournament '{t_id}' (previous status: {existing[1]}).")
                cur.execute("DELETE FROM tournament_matches WHERE tournament_id = ?", (t_id,))
                cur.execute("DELETE FROM tournament_players WHERE tournament_id = ?", (t_id,))
                cur.execute("DELETE FROM tournament_audit_log WHERE tournament_id = ?", (t_id,))
                cur.execute("DELETE FROM tournaments WHERE id = ?", (t_id,))

            # 1. Insert Tournament
            t_cols = get_sqlite_columns(conn, "tournaments")
            filtered_t = {k: v for k, v in t_data.items() if k in t_cols}
            col_names = list(filtered_t.keys())
            placeholders = ", ".join(["?"] * len(col_names))
            sql = f"INSERT INTO tournaments ({', '.join(col_names)}) VALUES ({placeholders})"
            cur.execute(sql, list(filtered_t.values()))

            # 2. Insert Players
            p_cols = get_sqlite_columns(conn, "tournament_players")
            for p in players:
                filtered_p = {k: v for k, v in p.items() if k in p_cols}
                p_names = list(filtered_p.keys())
                p_placeholders = ", ".join(["?"] * len(p_names))
                p_sql = f"INSERT INTO tournament_players ({', '.join(p_names)}) VALUES ({p_placeholders})"
                cur.execute(p_sql, list(filtered_p.values()))

            # 3. Insert Matches
            m_cols = get_sqlite_columns(conn, "tournament_matches")
            for m in matches:
                filtered_m = {k: v for k, v in m.items() if k in m_cols}
                m_names = list(filtered_m.keys())
                m_placeholders = ", ".join(["?"] * len(m_names))
                m_sql = f"INSERT INTO tournament_matches ({', '.join(m_names)}) VALUES ({m_placeholders})"
                cur.execute(m_sql, list(filtered_m.values()))

            # 4. Insert Audit Logs (strip old primary key 'id' to let target autoincrement)
            a_cols = [c for c in get_sqlite_columns(conn, "tournament_audit_log") if c != "id"]
            for a in audit_logs:
                filtered_a = {k: v for k, v in a.items() if k in a_cols}
                a_names = list(filtered_a.keys())
                a_placeholders = ", ".join(["?"] * len(a_names))
                a_sql = f"INSERT INTO tournament_audit_log ({', '.join(a_names)}) VALUES ({a_placeholders})"
                cur.execute(a_sql, list(filtered_a.values()))

            # 5. Log SYNC_IMPORTED in audit trail
            src_host = bundle.get("metadata", {}).get("source_host", "remote-edge")
            now_ts = time.time()
            cur.execute("""
                INSERT INTO tournament_audit_log (tournament_id, action, details, reason, performed_at)
                VALUES (?, ?, ?, ?, ?)
            """, (
                t_id,
                "SYNC_IMPORTED",
                f"Tournament imported from node '{src_host}'. Players: {len(players)}, Matches: {len(matches)}",
                "CLOUD_DELTA_SYNC",
                now_ts
            ))

        # 6. Insert Replays into target replays.db if provided
        replays = bundle.get("replays", [])
        imported_replays_count = 0
        if replays:
            base_dir = os.path.dirname(os.path.abspath(db_path))
            replay_db = os.path.join(base_dir, "replays.db")
            try:
                if ReplayEngine:
                    r_engine = ReplayEngine(db_path=replay_db)
                r_conn = sqlite3.connect(replay_db, timeout=15.0)
                try:
                    with r_conn:
                        r_cur = r_conn.cursor()
                        r_cols = get_sqlite_columns(r_conn, "replays")
                        for rep in replays:
                            filtered_rep = {k: v for k, v in rep.items() if k in r_cols}
                            r_names = list(filtered_rep.keys())
                            r_placeholders = ", ".join(["?"] * len(r_names))
                            r_sql = f"INSERT OR REPLACE INTO replays ({', '.join(r_names)}) VALUES ({r_placeholders})"
                            r_cur.execute(r_sql, list(filtered_rep.values()))
                            imported_replays_count += 1
                finally:
                    r_conn.close()
                logger.info(f"[SYNC] Successfully imported {imported_replays_count} replays for tournament '{t_id}'.")
            except Exception as e:
                logger.warning(f"[SYNC] Error importing replays for {t_id}: {e}")

        logger.info(f"[SYNC] Successfully imported tournament '{t_id}' (Players: {len(players)}, Matches: {len(matches)}, Replays: {imported_replays_count}).")
        return {
            "status": "SUCCESS",
            "tournament_id": t_id,
            "imported_players": len(players),
            "imported_matches": len(matches),
            "imported_audit_logs": len(audit_logs),
            "imported_replays": imported_replays_count,
            "timestamp": time.time()
        }
    finally:
        conn.close()


def push_to_cloud(
    tournament_id: str,
    cloud_url: str = "https://play.aethelburg.network",
    referee_pin: str = "1234",
    local_db_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Client-side utility: Exports the specified tournament bundle from the local node
    and POSTs it to the cloud endpoint.
    """
    bundle = export_tournament_bundle(tournament_id, db_path=local_db_path)
    clean_url = cloud_url.rstrip("/") + "/api/tournaments/sync_import"

    payload = {
        "pin": referee_pin,
        "bundle": bundle,
        "overwrite": True
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        clean_url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "AethelEdgeNode/1.0"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        raw_err = e.read().decode("utf-8")
        try:
            err_data = json.loads(raw_err)
            raise RuntimeError(f"Cloud sync rejected ({e.code}): {err_data.get('detail', raw_err)}")
        except Exception:
            raise RuntimeError(f"Cloud sync rejected ({e.code}): {raw_err}")
