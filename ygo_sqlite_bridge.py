import sqlite3
import os
from loguru import logger

class YGOSqliteBridge:
    def __init__(self, db_path=None):
        candidates = [
            db_path if db_path else "",
            os.getenv("CARDS_CDB_PATH", ""),
            os.path.abspath(os.path.join(os.path.dirname(__file__), "cards.cdb")),
            os.path.abspath(os.path.join(os.path.dirname(__file__), "data", "cards.cdb")),
        ]
        resolved_path = None
        for c in candidates:
            if c and os.path.exists(c):
                resolved_path = c
                break
        
        if not resolved_path:
            raise FileNotFoundError(f"Konnte keine cards.cdb finden! Gesucht in: {candidates}")
            
        self.db_path = resolved_path
        self.cards = []
        self.idx_to_id = {}
        self.id_to_idx = {}
        self.card_metadata = {}
        self._load_cards()

    def _load_cards(self):
        logger.info(f"📂 Verbinde mit SQLite Datenbank: {self.db_path}")
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        # Alle Karten laden (Main, Extra & Tokens für In-Game Spielmarken wie Dandylion Fluff Tokens)
        c.execute("""
            SELECT d.id, t.name, d.type, d.atk, d.def, d.level, d.race, d.attribute, t.desc, d.setcode, d.alias,
                   t.str1, t.str2, t.str3, t.str4, t.str5, t.str6, t.str7, t.str8,
                   t.str9, t.str10, t.str11, t.str12, t.str13, t.str14, t.str15, t.str16
            FROM datas d JOIN texts t ON d.id = t.id
            ORDER BY d.id ASC
        """)
        
        rows = c.fetchall()
        conn.close()
        
        for idx, row in enumerate(rows):
            card_id = row[0]
            name = row[1]
            card_type = row[2]
            atk = row[3]
            def_val = row[4]
            level = row[5]
            race = row[6]
            attribute = row[7]
            desc = row[8]
            setcode = row[9]
            alias = row[10]
            opt_strings = [s.strip() for s in row[11:27] if s and isinstance(s, str) and s.strip()]
            
            self.cards.append({
                "idx": idx,
                "id": card_id,
                "name": name,
                "type": card_type,
                "atk": atk,
                "def": def_val,
                "level": level,
                "race": race,
                "attribute": attribute,
                "desc": desc,
                "setcode": setcode if setcode else 0,
                "alias": alias if alias else 0,
                "strings": opt_strings
            })
            self.idx_to_id[idx] = card_id
            self.id_to_idx[card_id] = idx
            self.card_metadata[card_id] = self.cards[-1]

        logger.success(f"🎴 {len(self.cards)} Main Deck Karten erfolgreich geladen und indiziert.")
        
        # Load system strings if strings.conf exists
        self.system_strings = {}
        str_candidates = [
            os.path.join(os.path.dirname(self.db_path), "strings.conf"),
            os.path.abspath(os.path.join(os.path.dirname(__file__), "strings.conf")),
            os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data/db_temp/locales/de-DE/strings.conf")),
            os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data/db_temp/locales/en-US/strings.conf")),
        ]
        for sc in str_candidates:
            if os.path.exists(sc):
                try:
                    with open(sc, "r", encoding="utf-8", errors="ignore") as f:
                        for line in f:
                            line = line.strip()
                            if line.startswith("!system"):
                                parts = line.split(maxsplit=2)
                                if len(parts) >= 3:
                                    try:
                                        self.system_strings[int(parts[1])] = parts[2]
                                    except:
                                        pass
                    logger.info(f"Loaded {len(self.system_strings)} system strings from {sc}")
                    break
                except Exception as e:
                    logger.warning(f"Error reading {sc}: {e}")

    def reload(self):
        """Reloads all cards and metadata from disk"""
        self.cards = []
        self.idx_to_id = {}
        self.id_to_idx = {}
        self.card_metadata = {}
        self._load_cards()

    def get_description(self, desc):
        if not desc or desc == 0:
            return ""
        if isinstance(desc, str):
            try:
                desc = int(desc)
            except ValueError:
                return desc
        if desc < 10000:
            return self.system_strings.get(desc, f"Option {desc}")
        card_code = desc >> 4
        str_idx = desc & 0xF
        meta = self.card_metadata.get(card_code)
        if meta and "strings" in meta and str_idx < len(meta["strings"]):
            s = meta["strings"][str_idx]
            if s and s.strip():
                return s.strip()
        if meta:
            return f"{meta['name']} (Option {str_idx + 1})"
        return f"Option {desc}"


    def get_card_id(self, tensor_idx):
        return self.idx_to_id.get(tensor_idx)

    def get_card_info(self, tensor_idx):
        return self.cards[tensor_idx]

    def export_to_ydk(self, filename, top_indices):
        if not filename.endswith(".ydk"):
            filename += ".ydk"
            
        deck_ids = [self.get_card_id(idx) for idx in top_indices if self.get_card_id(idx) is not None]
        
        lines = [
            f"#created by Auratic Evolutionary Engine (SQLite Bridge)",
            "#main"
        ]
        for cid in deck_ids:
            lines.append(str(cid))
        lines.append("#extra")
        lines.append("#side")
        lines.append("")
        
        with open(filename, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
            
        logger.success(f"💾 Echtes YDK-Deck gespeichert: {filename} ({len(deck_ids)} Karten)")
        return filename

if __name__ == "__main__":
    bridge = YGOSqliteBridge()
    print("Test Sample 0:", bridge.get_card_info(0))
    print("Total indexed cards:", len(bridge.cards))

