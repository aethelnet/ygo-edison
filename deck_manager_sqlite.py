"""
Multi-Tenant SQLite Deck Database & In-Memory Bridge (`meta_decks.db`)
Replaces unisolated flat-file filesystem saves with user-scoped SQLite records.
Includes automatic legacy migration from meta_decks/*.ydk and live Edison banlist evaluation.
"""

import os
import glob
import json
import time
import uuid
import sqlite3
import threading
from typing import List, Dict, Any, Optional, Tuple

try:
    from edison_banlist import check_edison_legality
except ImportError:
    from apps.ygo_rl.edison_banlist import check_edison_legality


def check_modern_legality(main_ids: List[int], extra_ids: List[int] = None, side_ids: List[int] = None) -> Dict[str, Any]:
    extra_ids = extra_ids or []
    side_ids = side_ids or []
    main_len = len(main_ids)
    extra_len = len(extra_ids)
    side_len = len(side_ids)
    violations = []
    if main_len < 40:
        violations.append({"type": "DECK_SIZE_TOO_SMALL", "message": f"Main Deck enthält nur {main_len} Karten (min. 40)."})
    elif main_len > 60:
        violations.append({"type": "DECK_SIZE_TOO_LARGE", "message": f"Main Deck enthält {main_len} Karten (max. 60)."})
    if extra_len > 15:
        violations.append({"type": "EXTRA_DECK_TOO_LARGE", "message": f"Extra Deck enthält {extra_len} Karten (max. 15)."})
    if side_len > 15:
        violations.append({"type": "SIDE_DECK_TOO_LARGE", "message": f"Side Deck enthält {side_len} Karten (max. 15)."})
    counts: Dict[int, int] = {}
    for cid in main_ids + extra_ids + side_ids:
        if cid > 0:
            counts[cid] = counts.get(cid, 0) + 1
    for cid, cnt in counts.items():
        if cnt > 3:
            violations.append({"type": "MAX_COPIES_EXCEEDED", "card_id": cid, "count": cnt, "max_allowed": 3, "message": f"[MAX 3] Karte {cid} darf max. 3-mal gespielt werden ({cnt} im Deck)."})
    is_legal = len(violations) == 0
    return {
        "is_legal": is_legal,
        "format": "modern",
        "violations": violations,
        "status_text": "MODERN LEGAL" if is_legal else f"ILLEGAL ({len(violations)} Regelverstösse)",
        "main_count": main_len,
        "extra_count": extra_len,
        "side_count": side_len
    }


class DeckDatabase:
    def __init__(self, db_path: str = None, legacy_dir: str = None):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        if db_path is None:
            db_path = os.path.join(base_dir, "meta_decks.db")
        if legacy_dir is None:
            legacy_dir = os.path.join(base_dir, "meta_decks")
            
        self.db_path = db_path
        self.legacy_dir = legacy_dir
        self.lock = threading.Lock()
        
        self._init_db()
        self._migrate_legacy_ydk()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self):
        with self.lock:
            conn = self._get_connection()
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS decks (
                        deck_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        user_name TEXT NOT NULL,
                        deck_name TEXT NOT NULL,
                        main_ids TEXT NOT NULL,
                        extra_ids TEXT NOT NULL,
                        side_ids TEXT NOT NULL,
                        is_public INTEGER DEFAULT 1,
                        upvotes INTEGER DEFAULT 0,
                        views INTEGER DEFAULT 0,
                        is_edison_legal INTEGER DEFAULT 0,
                        edison_status TEXT DEFAULT '',
                        created_at REAL,
                        updated_at REAL,
                        format TEXT DEFAULT 'edison'
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_decks_user ON decks(user_id);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_decks_name ON decks(deck_name);")
                try:
                    conn.execute("ALTER TABLE decks ADD COLUMN format TEXT DEFAULT 'edison';")
                except Exception:
                    pass
                # Self-healing migration: Any deck marked illegal in Edison with format 'edison' is auto-promoted to 'modern'
                conn.execute("UPDATE decks SET format = 'modern' WHERE is_edison_legal = 0 AND (format = 'edison' OR format IS NULL);")
                conn.commit()
            finally:
                conn.close()

    def _migrate_legacy_ydk(self):
        """Import legacy .ydk files from meta_decks/ into meta_decks.db as system community decks."""
        if not os.path.exists(self.legacy_dir):
            return
            
        with self.lock:
            conn = self._get_connection()
            c = conn.cursor()
            try:
                c.execute("SELECT count(*) FROM decks")
                count = c.fetchone()[0]
                if count > 0:
                    return # Already populated

                print(f"[DECK_DB]: Migrating legacy .ydk files from {self.legacy_dir} to SQLite...")
                for path in glob.glob(os.path.join(self.legacy_dir, "*.ydk")):
                    deck_name = os.path.basename(path).replace(".ydk", "")
                    main, extra, side = self._parse_ydk_file(path)
                    if not main and not extra and not side:
                        continue
                    
                    val = check_edison_legality(main, extra, side)
                    deck_id = f"sys_{uuid.uuid4().hex[:8]}"
                    now = time.time()
                    
                    c.execute("""
                        INSERT OR REPLACE INTO decks 
                        (deck_id, user_id, user_name, deck_name, main_ids, extra_ids, side_ids, is_public, upvotes, views, is_edison_legal, edison_status, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        deck_id, "system", "System", deck_name,
                        json.dumps(main), json.dumps(extra), json.dumps(side),
                        1, 5, 0, 1 if val["is_legal"] else 0, val["status_text"],
                        now, now
                    ))
                conn.commit()
                print("[DECK_DB]: Legacy migration complete.")
            except Exception as e:
                print(f"[DECK_DB_ERROR]: Migration error: {e}")
            finally:
                conn.close()

    def _parse_ydk_file(self, path: str) -> Tuple[List[int], List[int], List[int]]:
        main, extra, side = [], [], []
        curr = None
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#created"): continue
                    if line == "#main": curr = main; continue
                    if line == "#extra": curr = extra; continue
                    if line == "!side": curr = side; continue
                    if line.isdigit() and curr is not None:
                        curr.append(int(line))
        except Exception as e:
            print(f"[DECK_DB_ERROR]: Failed to read {path}: {e}")
        return main, extra, side

    def save_deck(
        self,
        user_id: str,
        user_name: str,
        deck_name: str,
        main: List[int],
        extra: List[int],
        side: List[int],
        is_public: bool = True,
        deck_id: Optional[str] = None,
        format: Optional[str] = None
    ) -> Dict[str, Any]:
        """Saves or updates a user-scoped deck and performs Edison/Modern legality validation."""
        clean_user_id = (user_id or "usr_anonymous").strip()
        clean_user_name = (user_name or "Duellant").strip()
        clean_deck_name = deck_name.strip() or "Custom_Deck"
        
        # Validations
        val_edison = check_edison_legality(main, extra, side)
        val_modern = check_modern_legality(main, extra, side)
        
        clean_format = (format or "").strip().lower()
        if not clean_format:
            clean_format = "edison" if val_edison["is_legal"] else "modern"
            
        is_legal = 1 if val_edison["is_legal"] else 0
        edison_status = val_edison["status_text"]
        
        now = time.time()
        
        with self.lock:
            conn = self._get_connection()
            c = conn.cursor()
            try:
                # Check if updating an existing deck owned by this user
                existing_id = None
                if deck_id:
                    c.execute("SELECT deck_id FROM decks WHERE deck_id = ? AND user_id = ?", (deck_id, clean_user_id))
                    row = c.fetchone()
                    if row: existing_id = row[0]
                
                if not existing_id:
                    # Check by name + user_id
                    c.execute("SELECT deck_id FROM decks WHERE deck_name = ? AND user_id = ?", (clean_deck_name, clean_user_id))
                    row = c.fetchone()
                    if row: existing_id = row[0]
                    
                target_id = existing_id or f"dk_{uuid.uuid4().hex[:10]}"
                
                c.execute("""
                    INSERT OR REPLACE INTO decks
                    (deck_id, user_id, user_name, deck_name, main_ids, extra_ids, side_ids, is_public, upvotes, views, is_edison_legal, edison_status, created_at, updated_at, format)
                    VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, 
                        COALESCE((SELECT upvotes FROM decks WHERE deck_id = ?), 0),
                        COALESCE((SELECT views FROM decks WHERE deck_id = ?), 0),
                        ?, ?, 
                        COALESCE((SELECT created_at FROM decks WHERE deck_id = ?), ?),
                        ?, ?
                    )
                """, (
                    target_id, clean_user_id, clean_user_name, clean_deck_name,
                    json.dumps(main), json.dumps(extra), json.dumps(side),
                    1 if is_public else 0,
                    target_id, target_id,
                    is_legal, edison_status,
                    target_id, now, now,
                    clean_format
                ))
                conn.commit()
            finally:
                conn.close()

        # Mirror write to disk for backward-compatible tools
        try:
            os.makedirs(self.legacy_dir, exist_ok=True)
            safe_filename = "".join([c for c in clean_deck_name if c.isalnum() or c in ['_', '-']])
            with open(os.path.join(self.legacy_dir, f"{safe_filename}.ydk"), "w", encoding="utf-8") as f:
                f.write(f"#created by Auratic Prime Deck DB (user: {clean_user_id})\n#main\n")
                for cid in main: f.write(f"{cid}\n")
                f.write("#extra\n")
                for cid in extra: f.write(f"{cid}\n")
                f.write("!side\n")
                for cid in side: f.write(f"{cid}\n")
        except Exception as e:
            print(f"[DECK_DB_WARN]: Legacy file mirror write failed: {e}")

        active_val = val_modern if clean_format == "modern" else val_edison
        return {
            "status": "ok",
            "deck_id": target_id,
            "deck_name": clean_deck_name,
            "user_id": clean_user_id,
            "format": clean_format,
            "is_legal": bool(active_val["is_legal"]),
            "is_edison_legal": bool(is_legal),
            "is_modern_legal": bool(val_modern["is_legal"]),
            "edison_validation": val_edison,
            "modern_validation": val_modern
        }

    def get_deck(self, identifier: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Retrieves a deck by deck_id or deck_name. Prioritizes user's own deck if duplicate names exist."""
        clean_user = (user_id or "").strip()
        conn = self._get_connection()
        c = conn.cursor()
        row = None
        try:
            # 1. Direct deck_id lookup
            c.execute("""
                SELECT deck_id, user_id, user_name, deck_name, main_ids, extra_ids, side_ids, is_public, upvotes, views, is_edison_legal, edison_status, created_at, updated_at, format
                FROM decks WHERE deck_id = ?
            """, (identifier,))
            row = c.fetchone()
            
            # 2. Deck name lookup (user first, then public/system)
            if not row:
                if clean_user:
                    c.execute("""
                        SELECT deck_id, user_id, user_name, deck_name, main_ids, extra_ids, side_ids, is_public, upvotes, views, is_edison_legal, edison_status, created_at, updated_at, format
                        FROM decks WHERE deck_name = ? AND user_id = ?
                    """, (identifier, clean_user))
                    row = c.fetchone()
                if not row:
                    c.execute("""
                        SELECT deck_id, user_id, user_name, deck_name, main_ids, extra_ids, side_ids, is_public, upvotes, views, is_edison_legal, edison_status, created_at, updated_at, format
                        FROM decks WHERE deck_name = ? AND is_public = 1
                        ORDER BY (user_id = 'system') DESC, upvotes DESC LIMIT 1
                    """, (identifier,))
                    row = c.fetchone()

            if not row:
                return None
                
            # Increment views
            c.execute("UPDATE decks SET views = views + 1 WHERE deck_id = ?", (row[0],))
            conn.commit()

            deck_fmt = row[14] if len(row) > 14 and row[14] else ("edison" if row[10] else "modern")
            main = json.loads(row[4])
            extra = json.loads(row[5])
            side = json.loads(row[6])
            val_edison = check_edison_legality(main, extra, side)
            val_modern = check_modern_legality(main, extra, side)

            return {
                "deck_id": row[0],
                "user_id": row[1],
                "user_name": row[2],
                "deck_name": row[3],
                "main": main,
                "extra": extra,
                "side": side,
                "format": deck_fmt,
                "is_public": bool(row[7]),
                "upvotes": row[8],
                "views": row[9] + 1,
                "is_edison_legal": bool(row[10]),
                "is_modern_legal": bool(val_modern["is_legal"]),
                "edison_status": row[11],
                "edison_validation": val_edison,
                "modern_validation": val_modern,
                "created_at": row[12],
                "updated_at": row[13]
            }
        finally:
            conn.close()

    def list_decks(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Lists decks grouped into 'my_decks' and 'community_decks'."""
        clean_user = (user_id or "").strip()
        conn = self._get_connection()
        c = conn.cursor()
        try:
            c.execute("""
                SELECT deck_id, user_id, user_name, deck_name, 
                       length(main_ids) as m_len, is_public, upvotes, views, 
                       is_edison_legal, edison_status, updated_at, format
                FROM decks
                ORDER BY updated_at DESC
            """)
            rows = c.fetchall()
            
            my_decks = []
            community_decks = []
            all_names = []
            
            for r in rows:
                is_edison = bool(r[8])
                raw_fmt = (r[11] if len(r) > 11 and r[11] else "").strip().lower()
                if raw_fmt == "edison" and not is_edison:
                    deck_fmt = "modern"
                elif raw_fmt:
                    deck_fmt = raw_fmt
                else:
                    deck_fmt = "edison" if is_edison else "modern"

                d_obj = {
                    "deck_id": r[0],
                    "user_id": r[1],
                    "user_name": r[2],
                    "deck_name": r[3],
                    "format": deck_fmt,
                    "format_tag": " [EDISON]" if deck_fmt == "edison" else " [MODERN]",
                    "is_public": bool(r[5]),
                    "upvotes": r[6],
                    "views": r[7],
                    "is_edison_legal": is_edison,
                    "is_modern_legal": True if (deck_fmt == "modern" or not is_edison) else True,
                    "edison_status": r[9],
                    "updated_at": r[10]
                }
                if clean_user and r[1] == clean_user:
                    my_decks.append(d_obj)
                else:
                    if r[5] == 1 or r[1] == "system":
                        community_decks.append(d_obj)
                
                if r[3] not in all_names:
                    all_names.append(r[3])
                    
            return {
                "my_decks": my_decks,
                "community_decks": community_decks,
                "decks": all_names # Backward compatibility for legacy select dropdowns
            }
        finally:
            conn.close()

    def delete_deck(self, deck_id: str, user_id: str) -> bool:
        """Deletes a deck if owned by the user or system."""
        clean_user = (user_id or "").strip()
        with self.lock:
            conn = self._get_connection()
            c = conn.cursor()
            try:
                c.execute("DELETE FROM decks WHERE deck_id = ? AND (user_id = ? OR user_id = 'system')", (deck_id, clean_user))
                deleted = c.rowcount > 0
                conn.commit()
                return deleted
            finally:
                conn.close()
