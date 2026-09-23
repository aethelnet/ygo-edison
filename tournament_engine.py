"""
Swiss Tournament Engine for Yu-Gi-Oh Locals & Store Events (Phase 85)
Supports standard Swiss system pairings, strict rematch avoidance,
automatic Bye allocation, Konami tiebreaker metrics (OMW%, GW%, OGW%),
and automatic room generation with live score callback integration.
"""

import os
import time
import math
import random
import sqlite3
import threading
import json
from typing import List, Dict, Any, Optional, Tuple
from loguru import logger

try:
    from edison_banlist import check_edison_legality
    from deck_manager_sqlite import DeckDatabase
except ImportError:
    from apps.ygo_rl.edison_banlist import check_edison_legality
    from apps.ygo_rl.deck_manager_sqlite import DeckDatabase


class TournamentEngine:
    def __init__(self, db_path: Optional[str] = None):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.base_dir = base_dir
        if db_path is None:
            db_path = os.path.join(base_dir, "tournaments.db")
        self.db_path = db_path
        self.lock = threading.Lock()
        self.deck_db = DeckDatabase(
            db_path=os.path.join(base_dir, "meta_decks.db"),
            legacy_dir=os.path.join(base_dir, "meta_decks")
        )
        self._init_db()


    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self.lock:
            conn = self._get_connection()
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS tournaments (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        format TEXT DEFAULT 'Edison 2010',
                        status TEXT DEFAULT 'REGISTRATION',
                        current_round INTEGER DEFAULT 0,
                        total_rounds INTEGER DEFAULT 3,
                        created_at REAL,
                        updated_at REAL
                    );
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS tournament_players (
                        tournament_id TEXT NOT NULL,
                        player_id TEXT NOT NULL,
                        player_name TEXT NOT NULL,
                        deck_name TEXT NOT NULL,
                        match_points INTEGER DEFAULT 0,
                        game_wins INTEGER DEFAULT 0,
                        game_losses INTEGER DEFAULT 0,
                        matches_played INTEGER DEFAULT 0,
                        byes INTEGER DEFAULT 0,
                        dropped INTEGER DEFAULT 0,
                        registered_at REAL,
                        PRIMARY KEY (tournament_id, player_id),
                        FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
                    );
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS tournament_matches (
                        match_id TEXT PRIMARY KEY,
                        tournament_id TEXT NOT NULL,
                        round_num INTEGER NOT NULL,
                        table_num INTEGER NOT NULL,
                        room_id TEXT NOT NULL,
                        player1_id TEXT NOT NULL,
                        player1_name TEXT NOT NULL,
                        player2_id TEXT,
                        player2_name TEXT,
                        p1_score INTEGER DEFAULT 0,
                        p2_score INTEGER DEFAULT 0,
                        winner_id TEXT,
                        status TEXT DEFAULT 'PENDING',
                        reported_at REAL,
                        replay_id TEXT,
                        FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
                    );
                """)
                try:
                    conn.execute("ALTER TABLE tournament_matches ADD COLUMN replay_id TEXT;")
                except Exception:
                    pass

                # Phase 88: Schema migrations for Referee & Round Timer
                for col, ctype in [
                    ("round_timer_start", "REAL"),
                    ("round_timer_duration", "INTEGER DEFAULT 2700"),
                    ("round_timer_status", "TEXT DEFAULT 'STOPPED'"),
                    ("round_timer_paused_remaining", "INTEGER DEFAULT 2700"),
                    ("referee_pin", "TEXT DEFAULT '1234'")
                ]:
                    try:
                        conn.execute(f"ALTER TABLE tournaments ADD COLUMN {col} {ctype};")
                    except Exception:
                        pass

                try:
                    conn.execute("ALTER TABLE tournament_players ADD COLUMN disqualified INTEGER DEFAULT 0;")
                except Exception:
                    pass

                # Phase 89: Audit Log & Single Elimination Top-Cut Engine
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS tournament_audit_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        tournament_id TEXT NOT NULL,
                        action TEXT NOT NULL,
                        details TEXT,
                        reason TEXT,
                        performed_at REAL,
                        FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
                    );
                """)
                for col, ctype in [
                    ("stage", "TEXT DEFAULT 'SWISS'"),
                    ("has_top_cut", "INTEGER DEFAULT 0"),
                    ("top_cut_size", "INTEGER DEFAULT 0"),
                    ("champion_id", "TEXT DEFAULT NULL"),
                    ("champion_name", "TEXT DEFAULT NULL")
                ]:
                    try:
                        conn.execute(f"ALTER TABLE tournaments ADD COLUMN {col} {ctype};")
                    except Exception:
                        pass

                for col, ctype in [
                    ("stage", "TEXT DEFAULT 'SWISS'"),
                    ("bracket_label", "TEXT DEFAULT NULL")
                ]:
                    try:
                        conn.execute(f"ALTER TABLE tournament_matches ADD COLUMN {col} {ctype};")
                    except Exception:
                        pass

                # Phase 90: Schema migrations for Deck Pre-Flight Gate & Snapshot Locking
                for col, ctype in [
                    ("deck_snapshot", "TEXT DEFAULT NULL"),
                    ("deck_legality", "TEXT DEFAULT NULL"),
                    ("deck_card_count", "INTEGER DEFAULT 0"),
                ]:
                    try:
                        conn.execute(f"ALTER TABLE tournament_players ADD COLUMN {col} {ctype};")
                    except Exception:
                        pass

                conn.commit()

            finally:
                conn.close()

    def _log_audit_conn(self, conn: sqlite3.Connection, tournament_id: str, action: str, details: str, reason: str, now: float):
        """Internal helper to write audit entry on existing connection."""
        try:
            conn.execute("""
                INSERT INTO tournament_audit_log (tournament_id, action, details, reason, performed_at)
                VALUES (?, ?, ?, ?, ?)
            """, (tournament_id, action, details, reason, now))
        except Exception as e:
            logger.warning(f"[AUDIT] Failed to write audit log: {e}")

    def log_audit(self, tournament_id: str, action: str, details: str, reason: str = ""):
        """Appends an immutable entry to the tournament audit log."""
        now = time.time()
        with self.lock:
            conn = self._get_connection()
            try:
                self._log_audit_conn(conn, tournament_id, action, details, reason, now)
                conn.commit()
            finally:
                conn.close()

    def get_audit_log(self, tournament_id: str) -> List[Dict[str, Any]]:
        """Returns all audit log entries for a tournament in reverse chronological order."""
        with self.lock:
            conn = self._get_connection()
            try:
                rows = conn.execute("""
                    SELECT * FROM tournament_audit_log
                    WHERE tournament_id = ?
                    ORDER BY performed_at DESC
                """, (tournament_id,)).fetchall()
                res = []
                for r in rows:
                    d = dict(r)
                    d["formatted_time"] = time.strftime("%H:%M:%S", time.localtime(d["performed_at"]))
                    res.append(d)
                return res
            finally:
                conn.close()

    def get_match_by_room_id(self, room_id: str) -> Optional[Dict[str, Any]]:
        """Returns match row dict if room_id belongs to a tournament match, else None."""
        with self.lock:
            conn = self._get_connection()
            try:
                row = conn.execute("SELECT * FROM tournament_matches WHERE room_id = ?", (room_id,)).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

    def create_tournament(
        self,
        name: str,
        format_name: str = "Edison 2010",
        total_rounds: int = 3,
        referee_pin: str = "1234",
        top_cut_size: int = 0
    ) -> Dict[str, Any]:
        """Creates a new tournament in REGISTRATION state with optional Top-Cut size (0, 4, 8)."""
        t_id = f"t_{int(time.time())}_{random.randint(1000, 9999)}"
        now = time.time()
        has_top_cut = 1 if top_cut_size > 0 else 0
        with self.lock:
            conn = self._get_connection()
            try:
                conn.execute("""
                    INSERT INTO tournaments (
                        id, name, format, status, current_round, total_rounds,
                        referee_pin, stage, has_top_cut, top_cut_size, created_at, updated_at
                    )
                    VALUES (?, ?, ?, 'REGISTRATION', 0, ?, ?, 'SWISS', ?, ?, ?, ?)
                """, (t_id, name, format_name, total_rounds, referee_pin, has_top_cut, top_cut_size, now, now))
                self._log_audit_conn(
                    conn, t_id, "CREATE",
                    f"Turnier '{name}' erstellt ({total_rounds} Runden, Top-Cut: {top_cut_size})",
                    "Turnierstart", now
                )
                conn.commit()
            finally:
                conn.close()
        logger.info(f"[TOURNAMENT] Created tournament '{name}' ({t_id}) with {total_rounds} rounds (PIN: {referee_pin}, Top-Cut: {top_cut_size}).")
        return self.get_tournament(t_id)

    def register_player(
        self,
        tournament_id: str,
        player_name: str,
        deck_name: str,
        deck_cards: Optional[Dict[str, List[int]]] = None,
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Registers a player with their deck to an open tournament after strict Pre-Flight banlist validation."""
        clean_name = player_name.strip()
        if not clean_name:
            raise ValueError("[REGISTRATION ERROR] Player name cannot be empty.")
        player_id = clean_name.lower().replace(" ", "_")
        now = time.time()

        with self.lock:
            conn = self._get_connection()
            try:
                t = conn.execute("SELECT * FROM tournaments WHERE id = ?", (tournament_id,)).fetchone()
                if not t:
                    raise ValueError(f"Tournament {tournament_id} does not exist.")
                if t["status"] != "REGISTRATION":
                    raise ValueError(f"Tournament {tournament_id} is already in state '{t['status']}'. Registration closed.")

                # 1. Deck Resolution
                main: List[int] = []
                extra: List[int] = []
                side: List[int] = []
                dname = (deck_name or "").strip()

                if deck_cards and isinstance(deck_cards, dict) and "main" in deck_cards:
                    main = [int(x) for x in deck_cards.get("main", []) if str(x).isdigit()]
                    extra = [int(x) for x in deck_cards.get("extra", []) if str(x).isdigit()]
                    side = [int(x) for x in deck_cards.get("side", []) if str(x).isdigit()]
                    if not dname:
                        dname = "Custom_Deck"
                else:
                    if not dname:
                        raise ValueError("[PRE-FLIGHT REJECTED] Deck name is required.")
                    # Lookup in DeckDatabase
                    deck_info = self.deck_db.get_deck(dname, user_id=user_id)
                    if deck_info and len(deck_info.get("main", [])) > 0:
                        main = list(deck_info["main"])
                        extra = list(deck_info.get("extra", []))
                        side = list(deck_info.get("side", []))
                        dname = deck_info.get("deck_name", dname)
                    else:
                        # Try legacy ydk fallback
                        safe_name = "".join([c for c in dname if c.isalnum() or c in ['_', '-']])
                        ydk_path = os.path.join(self.base_dir, "meta_decks", f"{safe_name}.ydk")
                        if os.path.exists(ydk_path):
                            main, extra, side = self.deck_db._parse_ydk_file(ydk_path)
                        else:
                            raise ValueError(f"[PRE-FLIGHT REJECTED] Deck '{dname}' nicht in meta_decks.db gefunden. Bitte registriere das Deck zuerst im Deck-Manager.")

                # 2. Strict Edison Banlist Pre-Flight Gate
                val = check_edison_legality(main, extra, side)
                if not val.get("is_legal", False):
                    violations = val.get("violations", [])
                    v_msgs = [v.get("message", str(v)) for v in violations]
                    err_details = "; ".join(v_msgs[:3]) if v_msgs else "Unbekannter Regelverstoß"
                    self._log_audit_conn(
                        conn, tournament_id, "PRE_FLIGHT_FAILED",
                        f"Anmeldung abgewiesen für '{clean_name}' (Deck '{dname}'): {err_details}",
                        "BANLIST_VIOLATION", now
                    )
                    conn.commit()
                    raise ValueError(f"[BANLIST VIOLATION] Deck '{dname}' ist im Edison-Format illegal: {err_details}")

                # 3. Snapshot Creation & Locking
                snapshot = {
                    "main": main,
                    "extra": extra,
                    "side": side,
                    "deck_name": dname
                }
                snapshot_json = json.dumps(snapshot)
                legality_status = val.get("status_text", "EDISON LEGAL (April 2010)")

                conn.execute("""
                    INSERT INTO tournament_players 
                    (tournament_id, player_id, player_name, deck_name, deck_snapshot, deck_legality, deck_card_count, match_points, game_wins, game_losses, matches_played, byes, dropped, registered_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, 0, 0, ?)
                    ON CONFLICT(tournament_id, player_id) DO UPDATE SET
                    player_name = excluded.player_name,
                    deck_name = excluded.deck_name,
                    deck_snapshot = excluded.deck_snapshot,
                    deck_legality = excluded.deck_legality,
                    deck_card_count = excluded.deck_card_count,
                    registered_at = excluded.registered_at
                """, (tournament_id, player_id, clean_name, dname, snapshot_json, legality_status, len(main), now))

                self._log_audit_conn(
                    conn, tournament_id, "PRE_FLIGHT_PASSED",
                    f"Spieler '{clean_name}' mit Deck '{dname}' ({len(main)} Main, {len(extra)} Extra, {len(side)} Side) verifiziert & registriert.",
                    "DECK_LOCKED", now
                )
                conn.commit()
            finally:
                conn.close()

        logger.info(f"[TOURNAMENT] Pre-flight verified & registered '{clean_name}' (Deck: {dname}, Cards: {len(main)}) for {tournament_id}")
        return {
            "status": "REGISTERED",
            "player_id": player_id,
            "player_name": clean_name,
            "deck_name": dname,
            "deck_card_count": len(main),
            "deck_legality": legality_status,
            "deck_snapshot": snapshot
        }

    def get_player_deck_snapshot(self, tournament_id: str, player_id: str) -> Optional[Dict[str, Any]]:
        """Returns the locked deck snapshot for a tournament player."""
        with self.lock:
            conn = self._get_connection()
            try:
                row = conn.execute("""
                    SELECT deck_snapshot FROM tournament_players
                    WHERE tournament_id = ? AND player_id = ?
                """, (tournament_id, player_id)).fetchone()
                if row and row["deck_snapshot"]:
                    return json.loads(row["deck_snapshot"])
                return None
            except Exception as e:
                logger.error(f"[TOURNAMENT] Error fetching deck snapshot: {e}")
                return None
            finally:
                conn.close()


    def start_tournament(self, tournament_id: str) -> Dict[str, Any]:
        """Locks registration, computes rounds based on player count, and generates Round 1 pairings."""
        with self.lock:
            conn = self._get_connection()
            try:
                t = conn.execute("SELECT * FROM tournaments WHERE id = ?", (tournament_id,)).fetchone()
                if not t:
                    raise ValueError(f"Tournament {tournament_id} not found.")
                if t["status"] != "REGISTRATION":
                    raise ValueError(f"Tournament already started or finished ({t['status']}).")

                players = conn.execute("SELECT * FROM tournament_players WHERE tournament_id = ? AND dropped = 0", (tournament_id,)).fetchall()
                p_count = len(players)
                if p_count < 2:
                    raise ValueError(f"Need at least 2 players to start a tournament (currently {p_count}).")

                # Determine ideal Swiss round count if not explicitly configured
                calculated_rounds = max(2, math.ceil(math.log2(p_count)))
                total_rounds = max(t["total_rounds"], calculated_rounds)

                now = time.time()
                conn.execute("""
                    UPDATE tournaments 
                    SET status = 'RUNNING', current_round = 1, total_rounds = ?, updated_at = ?
                    WHERE id = ?
                """, (total_rounds, now, tournament_id))
                self._log_audit_conn(
                    conn, tournament_id, "START",
                    f"Turnier gestartet mit {p_count} Spielern ({total_rounds} Runden)",
                    "Turnierbeginn", now
                )
                conn.commit()
            finally:
                conn.close()

        # Generate Round 1 pairings
        self._generate_pairings(tournament_id, round_num=1)
        logger.info(f"[TOURNAMENT] Started tournament {tournament_id} with {p_count} players ({total_rounds} rounds).")
        return self.get_tournament(tournament_id)

    def _generate_pairings(self, tournament_id: str, round_num: int):
        """Generates Swiss pairings for the given round, preventing rematches."""
        with self.lock:
            conn = self._get_connection()
            try:
                players_raw = conn.execute("""
                    SELECT * FROM tournament_players 
                    WHERE tournament_id = ? AND dropped = 0
                    ORDER BY match_points DESC, game_wins DESC
                """, (tournament_id,)).fetchall()
                players = [dict(p) for p in players_raw]

                # Fetch past opponents per player
                past_matches = conn.execute("""
                    SELECT player1_id, player2_id FROM tournament_matches
                    WHERE tournament_id = ? AND winner_id IS NOT NULL
                """, (tournament_id,)).fetchall()

                played_set = set()
                for m in past_matches:
                    p1, p2 = m["player1_id"], m["player2_id"]
                    if p1 and p2 and p2 != "BYE":
                        played_set.add((p1, p2))
                        played_set.add((p2, p1))

                matches_to_insert = []
                table_idx = 1

                # If odd player count, allocate Bye
                bye_player = None
                if len(players) % 2 != 0:
                    eligible_for_bye = [p for p in reversed(players) if p["byes"] == 0]
                    if eligible_for_bye:
                        bye_player = eligible_for_bye[0]
                    else:
                        bye_player = players[-1]

                    players.remove(bye_player)
                    bye_match_id = f"m_{tournament_id}_r{round_num}_t{table_idx}_bye"
                    room_id = f"swiss_{tournament_id}_r{round_num}_t{table_idx}"
                    matches_to_insert.append({
                        "match_id": bye_match_id,
                        "tournament_id": tournament_id,
                        "round_num": round_num,
                        "table_num": table_idx,
                        "room_id": room_id,
                        "player1_id": bye_player["player_id"],
                        "player1_name": bye_player["player_name"],
                        "player2_id": "BYE",
                        "player2_name": "FREILOS (BYE)",
                        "p1_score": 2,
                        "p2_score": 0,
                        "winner_id": bye_player["player_id"],
                        "status": "COMPLETED",
                        "reported_at": time.time()
                    })

                    conn.execute("""
                        UPDATE tournament_players 
                        SET match_points = match_points + 3,
                            game_wins = game_wins + 2,
                            matches_played = matches_played + 1,
                            byes = byes + 1
                        WHERE tournament_id = ? AND player_id = ?
                    """, (tournament_id, bye_player["player_id"]))
                    table_idx += 1

                if round_num == 1:
                    random.shuffle(players)
                    for i in range(0, len(players), 2):
                        p1 = players[i]
                        p2 = players[i + 1]
                        m_id = f"m_{tournament_id}_r{round_num}_t{table_idx}"
                        r_id = f"swiss_{tournament_id}_r{round_num}_t{table_idx}"
                        matches_to_insert.append({
                            "match_id": m_id,
                            "tournament_id": tournament_id,
                            "round_num": round_num,
                            "table_num": table_idx,
                            "room_id": r_id,
                            "player1_id": p1["player_id"],
                            "player1_name": p1["player_name"],
                            "player2_id": p2["player_id"],
                            "player2_name": p2["player_name"],
                            "p1_score": 0,
                            "p2_score": 0,
                            "winner_id": None,
                            "status": "PENDING",
                            "reported_at": None
                        })
                        table_idx += 1
                else:
                    pairs = self._solve_swiss_pairings(players, played_set)
                    for p1, p2 in pairs:
                        m_id = f"m_{tournament_id}_r{round_num}_t{table_idx}"
                        r_id = f"swiss_{tournament_id}_r{round_num}_t{table_idx}"
                        matches_to_insert.append({
                            "match_id": m_id,
                            "tournament_id": tournament_id,
                            "round_num": round_num,
                            "table_num": table_idx,
                            "room_id": r_id,
                            "player1_id": p1["player_id"],
                            "player1_name": p1["player_name"],
                            "player2_id": p2["player_id"],
                            "player2_name": p2["player_name"],
                            "p1_score": 0,
                            "p2_score": 0,
                            "winner_id": None,
                            "status": "PENDING",
                            "reported_at": None
                        })
                        table_idx += 1

                for m in matches_to_insert:
                    conn.execute("""
                        INSERT INTO tournament_matches
                        (match_id, tournament_id, round_num, table_num, room_id, player1_id, player1_name, player2_id, player2_name, p1_score, p2_score, winner_id, status, reported_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        m["match_id"], m["tournament_id"], m["round_num"], m["table_num"], m["room_id"],
                        m["player1_id"], m["player1_name"], m["player2_id"], m["player2_name"],
                        m["p1_score"], m["p2_score"], m["winner_id"], m["status"], m["reported_at"]
                    ))
                conn.commit()
            finally:
                conn.close()
        logger.info(f"[TOURNAMENT] Generated {len(matches_to_insert)} pairings for {tournament_id} (Round {round_num}).")
        # Phase 88: Auto-start 45-minute round timer for newly paired round
        try:
            self.start_round_timer(tournament_id, duration_seconds=2700)
        except Exception as timer_err:
            logger.warning(f"[TOURNAMENT] Failed to auto-start round timer: {timer_err}")

    def _solve_swiss_pairings(self, players: List[Dict[str, Any]], played_set: set) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
        """Backtracking solver for Swiss pairings avoiding rematches."""
        unpaired = list(players)
        result_pairs = []

        def backtrack(candidates):
            if not candidates:
                return True
            p1 = candidates[0]
            for j in range(1, len(candidates)):
                p2 = candidates[j]
                if (p1["player_id"], p2["player_id"]) not in played_set:
                    result_pairs.append((p1, p2))
                    remaining = candidates[1:j] + candidates[j+1:]
                    if backtrack(remaining):
                        return True
                    result_pairs.pop()
            return False

        success = backtrack(unpaired)
        if not success:
            logger.warning("[TOURNAMENT] Strict rematch avoidance unsatisfiable. Falling back to adjacent pairing.")
            result_pairs.clear()
            for i in range(0, len(players), 2):
                if i + 1 < len(players):
                    result_pairs.append((players[i], players[i+1]))
        return result_pairs

    def generate_top_cut(self, tournament_id: str, top_cut_size: int = 4) -> Dict[str, Any]:
        """Generates Single Elimination Top-Cut bracket (Top 4 / Top 8 / Top 2) from Swiss standings."""
        with self.lock:
            conn = self._get_connection()
            try:
                t = conn.execute("SELECT * FROM tournaments WHERE id = ?", (tournament_id,)).fetchone()
                if not t:
                    raise ValueError(f"Tournament {tournament_id} does not exist.")

                # Fetch eligible players
                players_raw = conn.execute("""
                    SELECT * FROM tournament_players 
                    WHERE tournament_id = ? AND dropped = 0 AND (disqualified = 0 OR disqualified IS NULL)
                """, (tournament_id,)).fetchall()
                if len(players_raw) < 2:
                    raise ValueError("Mindestens 2 aktive Spieler für den Top-Cut erforderlich.")

                if len(players_raw) < top_cut_size:
                    top_cut_size = 4 if len(players_raw) >= 4 else 2

                # Compute rankings
                matches_raw = conn.execute("""
                    SELECT * FROM tournament_matches 
                    WHERE tournament_id = ? AND (stage = 'SWISS' OR stage IS NULL)
                    ORDER BY round_num ASC, table_num ASC
                """, (tournament_id,)).fetchall()
                matches = [dict(m) for m in matches_raw]
                players = [dict(p) for p in players_raw]

                player_opponents = {p["player_id"]: [] for p in players}
                for m in matches:
                    if m["status"] == "COMPLETED":
                        p1, p2 = m["player1_id"], m["player2_id"]
                        if p1 and p2 and p2 != "BYE":
                            player_opponents[p1].append(p2)
                            player_opponents[p2].append(p1)

                player_map = {p["player_id"]: p for p in players}
                for p in players:
                    matches_count = max(1, p["matches_played"])
                    mw = (p["match_points"] / (3.0 * matches_count))
                    p["mw_percent"] = max(0.33, mw)
                    total_games = p["game_wins"] + p["game_losses"]
                    gw = (p["game_wins"] / total_games) if total_games > 0 else 0.33
                    p["gw_percent"] = max(0.33, gw)

                for p in players:
                    opp_ids = player_opponents.get(p["player_id"], [])
                    omw = sum([player_map[oid]["mw_percent"] for oid in opp_ids if oid in player_map]) / len(opp_ids) if opp_ids else 0.33
                    p["omw_percent"] = round(omw * 100, 2)
                    p["gw_percent"] = round(p["gw_percent"] * 100, 2)

                standings = sorted(
                    players,
                    key=lambda x: (x["match_points"], x["omw_percent"], x["gw_percent"], (x["game_wins"] - x["game_losses"])),
                    reverse=True
                )

                seeds = standings[:top_cut_size]
                current_round = t["current_round"]
                playoff_round = current_round + 1
                now = time.time()

                pairings = []
                if top_cut_size == 4:
                    pairings = [
                        (seeds[0], seeds[3], "Halbfinale 1"),
                        (seeds[1], seeds[2], "Halbfinale 2")
                    ]
                elif top_cut_size == 8:
                    pairings = [
                        (seeds[0], seeds[7], "Viertelfinale 1"),
                        (seeds[3], seeds[4], "Viertelfinale 2"),
                        (seeds[1], seeds[6], "Viertelfinale 3"),
                        (seeds[2], seeds[5], "Viertelfinale 4")
                    ]
                else:
                    pairings = [
                        (seeds[0], seeds[1], "Finale")
                    ]

                for table_idx, (p1, p2, label) in enumerate(pairings, 1):
                    m_id = f"m_{tournament_id}_r{playoff_round}_t{table_idx}"
                    room_id = f"swiss_{tournament_id}_r{playoff_round}_t{table_idx}"
                    conn.execute("""
                        INSERT INTO tournament_matches (
                            match_id, tournament_id, round_num, table_num, room_id,
                            player1_id, player1_name, player2_id, player2_name,
                            p1_score, p2_score, status, stage, bracket_label
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 'PENDING', 'TOP_CUT', ?)
                    """, (
                        m_id, tournament_id, playoff_round, table_idx, room_id,
                        p1["player_id"], p1["player_name"], p2["player_id"], p2["player_name"],
                        label
                    ))

                conn.execute("""
                    UPDATE tournaments
                    SET current_round = ?, stage = 'TOP_CUT', has_top_cut = 1, top_cut_size = ?, status = 'RUNNING', updated_at = ?
                    WHERE id = ?
                """, (playoff_round, top_cut_size, now, tournament_id))
                self._log_audit_conn(
                    conn, tournament_id, "TOP_CUT_START",
                    f"Top-Cut Bracket generiert: {top_cut_size} Spieler, Runde {playoff_round} ({len(pairings)} Tische)",
                    "Playoffs gestartet", now
                )
                conn.commit()
            finally:
                conn.close()

        self.start_round_timer(tournament_id, duration_seconds=2700)
        logger.info(f"[TOURNAMENT] Generated Top Cut {top_cut_size} for {tournament_id} at round {playoff_round}.")
        return self.get_tournament(tournament_id)

    def report_match_result(
        self,
        tournament_id: str,
        round_num: int,
        table_num: int,
        p1_score: int,
        p2_score: int,
        winner_id: Optional[str] = None,
        replay_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Reports match result, updates player records, and auto-advances round when all tables finish."""
        with self.lock:
            conn = self._get_connection()
            try:
                m = conn.execute("""
                    SELECT * FROM tournament_matches
                    WHERE tournament_id = ? AND round_num = ? AND table_num = ?
                """, (tournament_id, round_num, table_num)).fetchone()
                if not m:
                    raise ValueError(f"Match not found at table {table_num} in round {round_num}.")

                if m["status"] == "COMPLETED":
                    logger.warning(f"[TOURNAMENT] Match {m['match_id']} already completed. Skipping redundant report.")
                    return {"status": "ALREADY_COMPLETED"}

                p1_id = m["player1_id"]
                p2_id = m["player2_id"]
                m_stage = m["stage"] if ("stage" in m.keys() and m["stage"]) else "SWISS"

                if winner_id is None:
                    if p1_score > p2_score:
                        winner_id = p1_id
                    elif p2_score > p1_score:
                        winner_id = p2_id
                    else:
                        winner_id = "DRAW"

                now = time.time()
                conn.execute("""
                    UPDATE tournament_matches
                    SET p1_score = ?, p2_score = ?, winner_id = ?, status = 'COMPLETED', reported_at = ?, replay_id = COALESCE(?, replay_id)
                    WHERE match_id = ?
                """, (p1_score, p2_score, winner_id, now, replay_id, m["match_id"]))

                # In Swiss: update match points and game record
                # In Top-Cut: record games won/lost, but preserve Swiss match points
                p1_pts = (3 if winner_id == p1_id else (1 if winner_id == "DRAW" else 0)) if m_stage == "SWISS" else 0
                conn.execute("""
                    UPDATE tournament_players
                    SET match_points = match_points + ?,
                        game_wins = game_wins + ?,
                        game_losses = game_losses + ?,
                        matches_played = matches_played + 1
                    WHERE tournament_id = ? AND player_id = ?
                """, (p1_pts, p1_score, p2_score, tournament_id, p1_id))

                if p2_id and p2_id != "BYE":
                    p2_pts = (3 if winner_id == p2_id else (1 if winner_id == "DRAW" else 0)) if m_stage == "SWISS" else 0
                    conn.execute("""
                        UPDATE tournament_players
                        SET match_points = match_points + ?,
                            game_wins = game_wins + ?,
                            game_losses = game_losses + ?,
                            matches_played = matches_played + 1
                        WHERE tournament_id = ? AND player_id = ?
                    """, (p2_pts, p2_score, p1_score, tournament_id, p2_id))

                self._log_audit_conn(
                    conn, tournament_id, "MATCH_RESULT",
                    f"Tisch {table_num} (R{round_num}) Ergebnis gemeldet: {p1_score}:{p2_score} (Sieger: {winner_id})",
                    f"Stage: {m_stage}", now
                )
                conn.commit()

                unf_matches = conn.execute("""
                    SELECT COUNT(*) as count FROM tournament_matches
                    WHERE tournament_id = ? AND round_num = ? AND status != 'COMPLETED'
                """, (tournament_id, round_num)).fetchone()["count"]

                round_advanced = False
                tournament_completed = False
                auto_trigger_top_cut = False
                t_stage = "SWISS"
                top_cut_target_size = 0

                if unf_matches == 0:
                    t = conn.execute("SELECT * FROM tournaments WHERE id = ?", (tournament_id,)).fetchone()
                    t_stage = t["stage"] if ("stage" in t.keys() and t["stage"]) else "SWISS"
                    has_top_cut = bool(t["has_top_cut"]) if "has_top_cut" in t.keys() else False
                    top_cut_target_size = t["top_cut_size"] if ("top_cut_size" in t.keys() and t["top_cut_size"]) else 0
                    total_rounds = t["total_rounds"]

                    if t_stage == "SWISS":
                        if round_num < total_rounds:
                            next_round = round_num + 1
                            conn.execute("""
                                UPDATE tournaments 
                                SET current_round = ?, updated_at = ?
                                WHERE id = ?
                            """, (next_round, now, tournament_id))
                            conn.commit()
                            round_advanced = True
                        else:
                            # Swiss rounds completed!
                            if has_top_cut and top_cut_target_size > 0:
                                auto_trigger_top_cut = True
                            else:
                                conn.execute("""
                                    UPDATE tournaments 
                                    SET status = 'COMPLETED', updated_at = ?
                                    WHERE id = ?
                                """, (now, tournament_id))
                                conn.commit()
                                tournament_completed = True
                    elif t_stage == "TOP_CUT":
                        # Playoff rounds
                        finished_playoff = conn.execute("""
                            SELECT * FROM tournament_matches
                            WHERE tournament_id = ? AND round_num = ? AND stage = 'TOP_CUT'
                            ORDER BY table_num ASC
                        """, (tournament_id, round_num)).fetchall()

                        if len(finished_playoff) == 2:
                            # Semifinals finished -> Generate FINALS!
                            sf1 = finished_playoff[0]
                            sf2 = finished_playoff[1]
                            w1_id = sf1["winner_id"]
                            w1_name = sf1["player1_name"] if w1_id == sf1["player1_id"] else sf1["player2_name"]
                            w2_id = sf2["winner_id"]
                            w2_name = sf2["player1_name"] if w2_id == sf2["player1_id"] else sf2["player2_name"]

                            next_round = round_num + 1
                            m_id = f"m_{tournament_id}_r{next_round}_t1"
                            room_id = f"swiss_{tournament_id}_r{next_round}_t1"
                            conn.execute("""
                                INSERT INTO tournament_matches (
                                    match_id, tournament_id, round_num, table_num, room_id,
                                    player1_id, player1_name, player2_id, player2_name,
                                    p1_score, p2_score, status, stage, bracket_label
                                )
                                VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, 0, 0, 'PENDING', 'TOP_CUT', 'Finale')
                            """, (m_id, tournament_id, next_round, room_id, w1_id, w1_name, w2_id, w2_name))
                            conn.execute("""
                                UPDATE tournaments
                                SET current_round = ?, updated_at = ?
                                WHERE id = ?
                            """, (next_round, now, tournament_id))
                            self._log_audit_conn(
                                conn, tournament_id, "TOP_CUT_FINALS",
                                f"Finale generiert: {w1_name} vs {w2_name}",
                                "Halbfinale abgeschlossen", now
                            )
                            conn.commit()
                            round_advanced = True
                        elif len(finished_playoff) == 1:
                            # Finals finished -> CROWN CHAMPION!
                            final_m = finished_playoff[0]
                            champ_id = final_m["winner_id"]
                            champ_name = final_m["player1_name"] if champ_id == final_m["player1_id"] else final_m["player2_name"]
                            conn.execute("""
                                UPDATE tournaments
                                SET status = 'COMPLETED', champion_id = ?, champion_name = ?, updated_at = ?
                                WHERE id = ?
                            """, (champ_id, champ_name, now, tournament_id))
                            self._log_audit_conn(
                                conn, tournament_id, "CHAMPION_CROWNED",
                                f"Turniersieger gekrönt: {champ_name} ({champ_id})",
                                "Finale gewonnen", now
                            )
                            conn.commit()
                            tournament_completed = True
            finally:
                conn.close()

        if auto_trigger_top_cut:
            logger.info(f"[TOURNAMENT] Swiss complete! Auto-generating Top-Cut ({top_cut_target_size}) for {tournament_id}...")
            self.generate_top_cut(tournament_id, top_cut_size=top_cut_target_size)
        elif round_advanced:
            if t_stage == "SWISS":
                logger.info(f"[TOURNAMENT] Round {round_num} complete! Auto-generating Round {round_num + 1} pairings...")
                self._generate_pairings(tournament_id, round_num + 1)
            elif t_stage == "TOP_CUT":
                logger.info(f"[TOURNAMENT] Playoff round complete! Auto-starting timer for next playoff round...")
                self.start_round_timer(tournament_id, duration_seconds=2700)
        elif tournament_completed:
            logger.info(f"[TOURNAMENT] All rounds complete! Tournament {tournament_id} marked as COMPLETED.")

        return {
            "status": "REPORTED",
            "round_advanced": round_advanced,
            "tournament_completed": tournament_completed,
            "stage": t_stage,
            "next_round": round_num + 1 if round_advanced else round_num
        }

    def get_tournament(self, tournament_id: str) -> Optional[Dict[str, Any]]:
        """Returns full tournament state including standings and current pairings."""
        with self.lock:
            conn = self._get_connection()
            try:
                t = conn.execute("SELECT * FROM tournaments WHERE id = ?", (tournament_id,)).fetchone()
                if not t:
                    return None

                players_raw = conn.execute("""
                    SELECT * FROM tournament_players 
                    WHERE tournament_id = ?
                """, (tournament_id,)).fetchall()
                players = [dict(p) for p in players_raw]

                matches_raw = conn.execute("""
                    SELECT * FROM tournament_matches 
                    WHERE tournament_id = ?
                    ORDER BY round_num ASC, table_num ASC
                """, (tournament_id,)).fetchall()
                matches = [dict(m) for m in matches_raw]
            finally:
                conn.close()

        player_opponents = {p["player_id"]: [] for p in players}
        for m in matches:
            if m["status"] == "COMPLETED":
                p1, p2 = m["player1_id"], m["player2_id"]
                if p1 and p2 and p2 != "BYE":
                    player_opponents[p1].append(p2)
                    player_opponents[p2].append(p1)

        player_map = {p["player_id"]: p for p in players}

        for p in players:
            matches_count = max(1, p["matches_played"])
            mw = (p["match_points"] / (3.0 * matches_count))
            p["mw_percent"] = max(0.33, mw)

            total_games = p["game_wins"] + p["game_losses"]
            gw = (p["game_wins"] / total_games) if total_games > 0 else 0.33
            p["gw_percent"] = max(0.33, gw)

        for p in players:
            opp_ids = player_opponents.get(p["player_id"], [])
            if opp_ids:
                omw = sum([player_map[oid]["mw_percent"] for oid in opp_ids if oid in player_map]) / len(opp_ids)
                ogw = sum([player_map[oid]["gw_percent"] for oid in opp_ids if oid in player_map]) / len(opp_ids)
            else:
                omw = 0.33
                ogw = 0.33
            p["omw_percent"] = round(omw * 100, 2)
            p["ogw_percent"] = round(ogw * 100, 2)
            p["gw_percent"] = round(p["gw_percent"] * 100, 2)

        standings = sorted(
            players,
            key=lambda x: (
                x["match_points"],
                x["omw_percent"],
                x["gw_percent"],
                (x["game_wins"] - x["game_losses"])
            ),
            reverse=True
        )

        for rank, p in enumerate(standings, 1):
            p["rank"] = rank

        current_round = t["current_round"]
        current_tables = [m for m in matches if m["round_num"] == current_round]

        timer = self.get_round_timer(t["id"])
        t_keys = t.keys()
        stage = t["stage"] if ("stage" in t_keys and t["stage"]) else "SWISS"
        has_top_cut = bool(t["has_top_cut"]) if "has_top_cut" in t_keys else False
        top_cut_size = t["top_cut_size"] if ("top_cut_size" in t_keys and t["top_cut_size"]) else 0
        champ_id = t["champion_id"] if ("champion_id" in t_keys) else None
        champ_name = t["champion_name"] if ("champion_name" in t_keys) else None
        ref_pin = t["referee_pin"] if ("referee_pin" in t_keys and t["referee_pin"]) else "1234"

        return {
            "id": t["id"],
            "name": t["name"],
            "format": t["format"],
            "status": t["status"],
            "current_round": current_round,
            "total_rounds": t["total_rounds"],
            "stage": stage,
            "has_top_cut": has_top_cut,
            "top_cut_size": top_cut_size,
            "champion_id": champ_id,
            "champion_name": champ_name,
            "player_count": len(players),
            "standings": standings,
            "current_tables": current_tables,
            "top_cut_matches": [m for m in matches if m.get("stage") == "TOP_CUT"],
            "all_matches": matches,
            "timer": timer,
            "referee_pin": ref_pin,
            "audit_log": self.get_audit_log(t["id"])
        }

    def list_tournaments(self) -> List[Dict[str, Any]]:
        """Returns overview of all recent tournaments."""
        with self.lock:
            conn = self._get_connection()
            try:
                rows = conn.execute("""
                    SELECT t.*, COUNT(tp.player_id) as player_count
                    FROM tournaments t
                    LEFT JOIN tournament_players tp ON t.id = tp.tournament_id
                    GROUP BY t.id
                    ORDER BY t.created_at DESC
                    LIMIT 20
                """).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    # ----------------- REFEREE ENGINE (Phase 88) -----------------
    def override_match_result(
        self,
        tournament_id: str,
        round_num: int,
        table_num: int,
        p1_score: int,
        p2_score: int,
        winner_id: Optional[str] = None,
        reason: str = "Referee Override"
    ) -> Dict[str, Any]:
        """
        Referee tool: Overrides match result, reverts previous points atomically,
        updates player records, and recalculates round progression.
        """
        with self.lock:
            conn = self._get_connection()
            try:
                m = conn.execute("""
                    SELECT * FROM tournament_matches
                    WHERE tournament_id = ? AND round_num = ? AND table_num = ?
                """, (tournament_id, round_num, table_num)).fetchone()
                if not m:
                    raise ValueError(f"Match not found at table {table_num} in round {round_num}.")

                p1_id = m["player1_id"]
                p2_id = m["player2_id"]

                # 1. Revert previous stats if already completed
                if m["status"] == "COMPLETED":
                    old_winner = m["winner_id"]
                    old_p1_pts = 3 if old_winner == p1_id else (1 if old_winner == "DRAW" else 0)
                    conn.execute("""
                        UPDATE tournament_players
                        SET match_points = MAX(0, match_points - ?),
                            game_wins = MAX(0, game_wins - ?),
                            game_losses = MAX(0, game_losses - ?),
                            matches_played = MAX(0, matches_played - 1)
                        WHERE tournament_id = ? AND player_id = ?
                    """, (old_p1_pts, m["p1_score"], m["p2_score"], tournament_id, p1_id))

                    if p2_id and p2_id != "BYE":
                        old_p2_pts = 3 if old_winner == p2_id else (1 if old_winner == "DRAW" else 0)
                        conn.execute("""
                            UPDATE tournament_players
                            SET match_points = MAX(0, match_points - ?),
                                game_wins = MAX(0, game_wins - ?),
                                game_losses = MAX(0, game_losses - ?),
                                matches_played = MAX(0, matches_played - 1)
                            WHERE tournament_id = ? AND player_id = ?
                        """, (old_p2_pts, m["p2_score"], m["p1_score"], tournament_id, p2_id))

                # 2. Determine new winner
                if winner_id is None:
                    if p1_score > p2_score:
                        winner_id = p1_id
                    elif p2_score > p1_score:
                        winner_id = p2_id
                    else:
                        winner_id = "DRAW"

                now = time.time()
                conn.execute("""
                    UPDATE tournament_matches
                    SET p1_score = ?, p2_score = ?, winner_id = ?, status = 'COMPLETED', reported_at = ?
                    WHERE match_id = ?
                """, (p1_score, p2_score, winner_id, now, m["match_id"]))

                # 3. Apply new stats
                p1_pts = 3 if winner_id == p1_id else (1 if winner_id == "DRAW" else 0)
                conn.execute("""
                    UPDATE tournament_players
                    SET match_points = match_points + ?,
                        game_wins = game_wins + ?,
                        game_losses = game_losses + ?,
                        matches_played = matches_played + 1
                    WHERE tournament_id = ? AND player_id = ?
                """, (p1_pts, p1_score, p2_score, tournament_id, p1_id))

                if p2_id and p2_id != "BYE":
                    p2_pts = 3 if winner_id == p2_id else (1 if winner_id == "DRAW" else 0)
                    conn.execute("""
                        UPDATE tournament_players
                        SET match_points = match_points + ?,
                            game_wins = game_wins + ?,
                            game_losses = game_losses + ?,
                            matches_played = matches_played + 1
                        WHERE tournament_id = ? AND player_id = ?
                    """, (p2_pts, p2_score, p1_score, tournament_id, p2_id))

                self._log_audit_conn(
                    conn, tournament_id, "OVERRIDE",
                    f"Tisch {table_num} (R{round_num}) auf {p1_score}:{p2_score} gesetzt (Sieger: {winner_id})",
                    reason, now
                )
                conn.commit()
                logger.info(f"[REFEREE OVERRIDE] Table {table_num} (R{round_num}) in {tournament_id} overridden to {p1_score}:{p2_score} (Winner: {winner_id}) | Reason: {reason}")

                # 4. Check round completion
                unf_matches = conn.execute("""
                    SELECT COUNT(*) as count FROM tournament_matches
                    WHERE tournament_id = ? AND round_num = ? AND status != 'COMPLETED'
                """, (tournament_id, round_num)).fetchone()["count"]

                round_advanced = False
                tournament_completed = False

                if unf_matches == 0:
                    t = conn.execute("SELECT * FROM tournaments WHERE id = ?", (tournament_id,)).fetchone()
                    total_rounds = t["total_rounds"]
                    if round_num < total_rounds and t["current_round"] == round_num:
                        next_round = round_num + 1
                        conn.execute("""
                            UPDATE tournaments 
                            SET current_round = ?, updated_at = ?
                            WHERE id = ?
                        """, (next_round, now, tournament_id))
                        conn.commit()
                        round_advanced = True
                    elif round_num == total_rounds and t["status"] != "COMPLETED":
                        conn.execute("""
                            UPDATE tournaments 
                            SET status = 'COMPLETED', updated_at = ?
                            WHERE id = ?
                        """, (now, tournament_id))
                        conn.commit()
                        tournament_completed = True
            finally:
                conn.close()

        if round_advanced:
            self._generate_pairings(tournament_id, round_num + 1)

        return self.get_tournament(tournament_id)

    def reset_table(
        self,
        tournament_id: str,
        round_num: int,
        table_num: int,
        reason: str = "Referee Reset"
    ) -> Dict[str, Any]:
        """Resets a match to PENDING, reverting awarded points if already completed."""
        with self.lock:
            conn = self._get_connection()
            try:
                m = conn.execute("""
                    SELECT * FROM tournament_matches
                    WHERE tournament_id = ? AND round_num = ? AND table_num = ?
                """, (tournament_id, round_num, table_num)).fetchone()
                if not m:
                    raise ValueError(f"Match not found at table {table_num} in round {round_num}.")

                p1_id = m["player1_id"]
                p2_id = m["player2_id"]

                if m["status"] == "COMPLETED":
                    old_winner = m["winner_id"]
                    old_p1_pts = 3 if old_winner == p1_id else (1 if old_winner == "DRAW" else 0)
                    conn.execute("""
                        UPDATE tournament_players
                        SET match_points = MAX(0, match_points - ?),
                            game_wins = MAX(0, game_wins - ?),
                            game_losses = MAX(0, game_losses - ?),
                            matches_played = MAX(0, matches_played - 1)
                        WHERE tournament_id = ? AND player_id = ?
                    """, (old_p1_pts, m["p1_score"], m["p2_score"], tournament_id, p1_id))

                    if p2_id and p2_id != "BYE":
                        old_p2_pts = 3 if old_winner == p2_id else (1 if old_winner == "DRAW" else 0)
                        conn.execute("""
                            UPDATE tournament_players
                            SET match_points = MAX(0, match_points - ?),
                                game_wins = MAX(0, game_wins - ?),
                                game_losses = MAX(0, game_losses - ?),
                                matches_played = MAX(0, matches_played - 1)
                            WHERE tournament_id = ? AND player_id = ?
                        """, (old_p2_pts, m["p2_score"], m["p1_score"], tournament_id, p2_id))

                now = time.time()
                conn.execute("""
                    UPDATE tournament_matches
                    SET p1_score = 0, p2_score = 0, winner_id = NULL, status = 'PENDING', reported_at = NULL
                    WHERE match_id = ?
                """, (m["match_id"],))
                self._log_audit_conn(
                    conn, tournament_id, "TABLE_RESET",
                    f"Tisch {table_num} (R{round_num}) auf PENDING zurückgesetzt",
                    reason, now
                )
                conn.commit()
                logger.info(f"[REFEREE] Table {table_num} (R{round_num}) in {tournament_id} reset to PENDING | Reason: {reason}")
            finally:
                conn.close()

        return self.get_tournament(tournament_id)

    def drop_player(self, tournament_id: str, player_id: str, reason: str = "Referee Drop") -> Dict[str, Any]:
        """Drops a player from tournament. If active in current round, awards 2-0 win to opponent."""
        return self._set_player_dropped(tournament_id, player_id, is_dq=False, reason=reason)

    def disqualify_player(self, tournament_id: str, player_id: str, reason: str = "Referee DQ") -> Dict[str, Any]:
        """Disqualifies a player. Awards 2-0 win to opponent and marks as disqualified."""
        return self._set_player_dropped(tournament_id, player_id, is_dq=True, reason=reason)

    def _set_player_dropped(self, tournament_id: str, player_id: str, is_dq: bool, reason: str) -> Dict[str, Any]:
        with self.lock:
            conn = self._get_connection()
            try:
                t = conn.execute("SELECT * FROM tournaments WHERE id = ?", (tournament_id,)).fetchone()
                if not t:
                    raise ValueError("Tournament not found")

                now = time.time()
                conn.execute("""
                    UPDATE tournament_players
                    SET dropped = 1, disqualified = ?
                    WHERE tournament_id = ? AND player_id = ?
                """, (1 if is_dq else 0, tournament_id, player_id))
                self._log_audit_conn(
                    conn, tournament_id, "PLAYER_DQ" if is_dq else "PLAYER_DROP",
                    f"Spieler {player_id} {'disqualifiziert' if is_dq else 'gedroppt'}",
                    reason, now
                )
                conn.commit()

                # If tournament is running, check active match in current round
                if t["status"] in ("RUNNING", "ACTIVE"):
                    cur_round = t["current_round"]
                    m = conn.execute("""
                        SELECT * FROM tournament_matches
                        WHERE tournament_id = ? AND round_num = ? AND (player1_id = ? OR player2_id = ?) AND status != 'COMPLETED'
                    """, (tournament_id, cur_round, player_id, player_id)).fetchone()

                    if m:
                        if m["player1_id"] == player_id:
                            opp_id = m["player2_id"]
                            p1_s, p2_s, w_id = 0, 2, opp_id
                        else:
                            opp_id = m["player1_id"]
                            p1_s, p2_s, w_id = 2, 0, opp_id

                        conn.execute("""
                            UPDATE tournament_matches
                            SET p1_score = ?, p2_score = ?, winner_id = ?, status = 'COMPLETED', reported_at = ?
                            WHERE match_id = ?
                        """, (p1_s, p2_s, w_id, now, m["match_id"]))

                        if opp_id and opp_id != "BYE":
                            conn.execute("""
                                UPDATE tournament_players
                                SET match_points = match_points + 3,
                                    game_wins = game_wins + 2,
                                    matches_played = matches_played + 1
                                WHERE tournament_id = ? AND player_id = ?
                            """, (tournament_id, opp_id))

                        conn.execute("""
                            UPDATE tournament_players
                            SET game_losses = game_losses + 2,
                                matches_played = matches_played + 1
                            WHERE tournament_id = ? AND player_id = ?
                        """, (tournament_id, player_id))
                        conn.commit()
                        logger.info(f"[REFEREE] Forfeit win awarded at Table {m['table_num']} due to {'DQ' if is_dq else 'Drop'} of {player_id}")
            finally:
                conn.close()

        return self.get_tournament(tournament_id)

    # ----------------- ROUND TIMER ENGINE (Phase 88) -----------------
    def start_round_timer(self, tournament_id: str, duration_seconds: int = 2700) -> Dict[str, Any]:
        """Starts countdown for current round."""
        now = time.time()
        with self.lock:
            conn = self._get_connection()
            try:
                conn.execute("""
                    UPDATE tournaments
                    SET round_timer_start = ?, round_timer_duration = ?, round_timer_status = 'RUNNING', round_timer_paused_remaining = ?
                    WHERE id = ?
                """, (now, duration_seconds, duration_seconds, tournament_id))
                self._log_audit_conn(
                    conn, tournament_id, "TIMER_START",
                    f"Rundentimer gestartet ({duration_seconds}s)",
                    "Timer gestartet", now
                )
                conn.commit()
            finally:
                conn.close()
        return self.get_round_timer(tournament_id)

    def pause_round_timer(self, tournament_id: str) -> Dict[str, Any]:
        """Pauses round timer, preserving remaining seconds."""
        t_info = self.get_round_timer(tournament_id)
        rem = t_info["remaining_seconds"]
        now = time.time()
        with self.lock:
            conn = self._get_connection()
            try:
                conn.execute("""
                    UPDATE tournaments
                    SET round_timer_status = 'PAUSED', round_timer_paused_remaining = ?
                    WHERE id = ?
                """, (rem, tournament_id))
                self._log_audit_conn(
                    conn, tournament_id, "TIMER_PAUSE",
                    f"Rundentimer pausiert ({rem}s verbleibend)",
                    "Timer pausiert", now
                )
                conn.commit()
            finally:
                conn.close()
        return self.get_round_timer(tournament_id)

    def resume_round_timer(self, tournament_id: str) -> Dict[str, Any]:
        """Resumes paused round timer."""
        t_info = self.get_round_timer(tournament_id)
        rem = t_info["remaining_seconds"]
        now = time.time()
        with self.lock:
            conn = self._get_connection()
            try:
                conn.execute("""
                    UPDATE tournaments
                    SET round_timer_start = ?, round_timer_duration = ?, round_timer_status = 'RUNNING'
                    WHERE id = ?
                """, (now, rem, tournament_id))
                self._log_audit_conn(
                    conn, tournament_id, "TIMER_RESUME",
                    f"Rundentimer fortgesetzt ({rem}s verbleibend)",
                    "Timer fortgesetzt", now
                )
                conn.commit()
            finally:
                conn.close()
        return self.get_round_timer(tournament_id)

    def reset_round_timer(self, tournament_id: str, duration_seconds: int = 2700) -> Dict[str, Any]:
        """Resets round timer back to initial duration and stops it."""
        now = time.time()
        with self.lock:
            conn = self._get_connection()
            try:
                conn.execute("""
                    UPDATE tournaments
                    SET round_timer_start = NULL, round_timer_duration = ?, round_timer_status = 'STOPPED', round_timer_paused_remaining = ?
                    WHERE id = ?
                """, (duration_seconds, duration_seconds, tournament_id))
                self._log_audit_conn(
                    conn, tournament_id, "TIMER_RESET",
                    f"Rundentimer zurückgesetzt auf {duration_seconds}s",
                    "Timer Reset", now
                )
                conn.commit()
            finally:
                conn.close()
        return self.get_round_timer(tournament_id)

    def trigger_time_call(self, tournament_id: str) -> Dict[str, Any]:
        """Forces Time-Call state (official Konami End-of-Round Turns 0-3)."""
        now = time.time()
        with self.lock:
            conn = self._get_connection()
            try:
                conn.execute("""
                    UPDATE tournaments
                    SET round_timer_status = 'TIME_CALL', round_timer_paused_remaining = 0
                    WHERE id = ?
                """, (tournament_id,))
                self._log_audit_conn(
                    conn, tournament_id, "TIME_CALL",
                    "Konami Time-Call ausgelöst (Turns 0-3 End-of-Round Tiebreaker aktiviert)",
                    "Time-Call", now
                )
                conn.commit()
            finally:
                conn.close()
        return self.get_round_timer(tournament_id)

    def get_round_timer(self, tournament_id: str) -> Dict[str, Any]:
        """Calculates exact remaining round time and Time-Call status."""
        with self.lock:
            conn = self._get_connection()
            try:
                t = conn.execute("SELECT * FROM tournaments WHERE id = ?", (tournament_id,)).fetchone()
                if not t:
                    return {"status": "STOPPED", "remaining_seconds": 0, "formatted": "00:00", "is_time_call": False}

                t_keys = t.keys()
                status = t["round_timer_status"] if ("round_timer_status" in t_keys and t["round_timer_status"]) else "STOPPED"
                duration = t["round_timer_duration"] if ("round_timer_duration" in t_keys and t["round_timer_duration"]) else 2700
                start_time = t["round_timer_start"] if ("round_timer_start" in t_keys) else None
                paused_rem = t["round_timer_paused_remaining"] if ("round_timer_paused_remaining" in t_keys and t["round_timer_paused_remaining"] is not None) else duration

                if status == "RUNNING" and start_time:
                    elapsed = time.time() - start_time
                    remaining = max(0, duration - elapsed)
                    if remaining <= 0:
                        status = "TIME_CALL"
                elif status == "PAUSED":
                    remaining = paused_rem
                elif status == "TIME_CALL":
                    remaining = 0
                else:
                    remaining = duration

                rem_int = int(remaining)
                mins = rem_int // 60
                secs = rem_int % 60
                return {
                    "status": status,
                    "remaining_seconds": rem_int,
                    "duration_seconds": duration,
                    "formatted": f"{mins:02d}:{secs:02d}",
                    "is_time_call": (status == "TIME_CALL" or remaining <= 0)
                }
            finally:
                conn.close()

# Global Engine Singleton
tournament_engine = TournamentEngine()
