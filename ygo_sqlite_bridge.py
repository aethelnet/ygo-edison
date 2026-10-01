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
            os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data/db_temp/locales/en-US/cards.cdb")),
            os.path.join(os.path.expanduser("~"), "auratic-systems-prime", "data", "db_temp", "locales", "en-US", "cards.cdb")
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

    def get_card_string(self, card_code, str_idx):
        if not card_code:
            return None
            
        EXPLICIT_STRINGS = {
            55920742: { # Noh-P.U.N.K. Foxy Tune
                0: "[HAND-SPECIAL] 1 P.U.N.K. tributieren -> Foxy Tune beschwören",
                1: "[DECK-SPECIAL] Handkarte abwerfen -> P.U.N.K. aus Deck holen",
                2: "[BATTLE] LP in Höhe der ATK erhalten"
            },
            75046994: { # Noh-P.U.N.K. Rising Scale
                0: "[HAND-SPECIAL] 1 P.U.N.K. verbannen -> Rising Scale beschwören",
                1: "[SEARCH/SPECIAL] 600 LP zahlen -> P.U.N.K. suchen oder beschwören",
                2: "[QUICK] Monster mit >=2500 ATK in verdeckte DEF setzen"
            },
            30271097: { # The Fallen & The Virtuous
                0: "[ACTIVATE] The Fallen & The Virtuous aktivieren",
                1: "[OPTION 1] Extra Deck Albaz senden -> 1 offene Karte zerstören",
                2: "[OPTION 2] (Ecclesia aktiv) Monster aus beliebigem GY beschwören"
            },
            28403802: { # P.U.N.K. JAM Dragon Drive
                0: "[SUMMON] Stufe-3 Psi suchen oder spezialbeschwören",
                1: "[REBORN] P.U.N.K. aus Friedhof spezialbeschwören"
            },
            18313046: { # Ukiyoe-P.U.N.K. Rising Carp
                0: "[TRIBUTE] 2 P.U.N.K.-Monster aus Hand/Deck spezialbeschwören",
                1: "[SYNCHRO-MATERIAL] Synchromonster kann 2x angreifen"
            },
            44708154: { # Ukiyoe-P.U.N.K. Amazing Dragon
                0: "[BOUNCE] Gegnerische Feldkarten auf die Hand geben",
                1: "[REBORN] 1 P.U.N.K. aus dem Friedhof spezialbeschwören"
            },
            6609736: { # Noh-P.U.N.K. Deer Note
                0: "[HAND-REVEAL] Deer Note + P.U.N.K. beschwören & 1 senden",
                1: "[GRAVE-TRIGGER] Nicht-Stufe-5 P.U.N.K. aus Friedhof beschwören"
            },
            13258285: { # Ukiyoe-P.U.N.K. Sharakusai
                0: "[FUSION] 600 LP zahlen -> P.U.N.K. Fusionsbeschwörung",
                1: "[SYNCHRO-QUICK] (Gegnerzug) 600 LP zahlen -> Synchrobeschwörung"
            },
            19535693: { # Noh-P.U.N.K. Ze Amin
                0: "[SEARCH] 600 LP zahlen -> 1 P.U.N.K.-Monster suchen",
                1: "[ATK-BUFF] Wenn auf Friedhof gelegt: 1 P.U.N.K. erhält 600 ATK"
            },
            67723438: { # Emergency Teleport
                0: "[SPECIAL] Stufe 3 oder niedriger Psi aus Hand/Deck spezialbeschwören",
                1: "Durch Notfallteleport beschworen"
            },
            14558127: { # Ash Blossom & Joyous Spring
                0: "[DISCARD] Von Hand abwerfen -> Deck/GY-Effekt annullieren"
            }
        }
        if card_code in EXPLICIT_STRINGS and str_idx in EXPLICIT_STRINGS[card_code]:
            return EXPLICIT_STRINGS[card_code][str_idx]
            
        meta = self.card_metadata.get(card_code)
        if meta and "strings" in meta and str_idx < len(meta["strings"]):
            s = meta["strings"][str_idx]
            if s and s.strip():
                return s.strip()
        if meta:
            return f"{meta['name']} (Option {str_idx + 1})"
        return None

    def get_description(self, desc, card_id=None, active_cards=None):
        if not desc or desc == 0:
            return ""
        if isinstance(desc, str):
            try:
                desc = int(desc)
            except ValueError:
                return desc
        
        # Ensure unsigned 32-bit integer representation
        desc_u = int(desc) & 0xFFFFFFFF
            
        # 1. Direct card_id matching
        if card_id:
            c_u = int(card_id) & 0xFFFFFFFF
            # Modern EDOPro Stringid: (str_idx & 0xfffff) | ((code << 20) & 0xffffffff)
            if (desc_u & ~0xFFFFF) == ((c_u << 20) & 0xFFFFFFFF):
                str_idx = desc_u & 0xFFFFF
                res = self.get_card_string(card_id, str_idx)
                if res:
                    return res
            # Legacy Stringid: (code << 4) | (str_idx & 0xf)
            elif (desc_u & ~0xF) == ((c_u << 4) & 0xFFFFFFFF):
                str_idx = desc_u & 0xF
                res = self.get_card_string(card_id, str_idx)
                if res:
                    return res
            # Direct small option index
            elif desc_u < 16:
                res = self.get_card_string(card_id, desc_u)
                if res:
                    return res

        # 2. Small system strings fallback (< 10000)
        if desc_u < 10000 and desc_u in self.system_strings:
            return self.system_strings[desc_u]

        # 2. Legacy direct decoding if card_code exists in DB
        legacy_code = desc_u >> 4
        legacy_idx = desc_u & 0xF
        if legacy_code in self.card_metadata:
            res = self.get_card_string(legacy_code, legacy_idx)
            if res:
                return res

        # 3. Modern Stringid matching across active_cards and known archetypes
        high_20 = desc_u & ~0xFFFFF
        str_idx = desc_u & 0xFFFFF
        
        candidates = []
        if active_cards:
            for cid in active_cards:
                if ((int(cid) << 20) & 0xFFFFFFFF) == high_20:
                    candidates.append(cid)
                    
        # Check explicit archetypes if still no candidate
        KNOWN_ARCHETYPES = [55920742, 75046994, 30271097, 28403802, 18313046, 44708154, 6609736, 13258285, 19535693, 67723438, 14558127]
        for cid in KNOWN_ARCHETYPES:
            if ((cid << 20) & 0xFFFFFFFF) == high_20:
                if cid not in candidates:
                    candidates.append(cid)

        if candidates:
            res = self.get_card_string(candidates[0], str_idx)
            if res:
                return res

        # 4. Fallback system string or generic option
        if desc_u in self.system_strings:
            return self.system_strings[desc_u]
        return f"Option {desc_u}"


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

