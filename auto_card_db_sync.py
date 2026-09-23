import os
import sys
import time
import json
import sqlite3
import shutil
import urllib.request
import subprocess
from loguru import logger

BABEL_CDB_URL = "https://raw.githubusercontent.com/ProjectIgnis/BabelCDB/master/cards.cdb"
CARDSCRIPTS_RAW_URL = "https://raw.githubusercontent.com/ProjectIgnis/CardScripts/master/official"
CARDSCRIPTS_GIT_URL = "https://github.com/ProjectIgnis/CardScripts.git"
YGOPRODECK_API_URL = "https://db.ygoprodeck.com/api/v7/cardinfo.php"

class AutoCardDBSync:
    def __init__(self, cdb_path=None, script_dir=None):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.cdb_path = cdb_path or os.path.join(base_dir, "cards.cdb")
        self.script_dir = script_dir or os.path.join(base_dir, "script")
        
        os.makedirs(self.script_dir, exist_ok=True)
        self._ensure_db_schema()

    def _ensure_db_schema(self):
        """Ensures SQLite tables exist in cards.cdb"""
        with sqlite3.connect(self.cdb_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS datas (
                    id integer primary key,
                    ot integer,
                    alias integer,
                    setcode integer,
                    type integer,
                    atk integer,
                    def integer,
                    level integer,
                    race integer,
                    attribute integer,
                    category integer
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS texts (
                    id integer primary key,
                    name text,
                    desc text,
                    str1 text, str2 text, str3 text, str4 text,
                    str5 text, str6 text, str7 text, str8 text,
                    str9 text, str10 text, str11 text, str12 text,
                    str13 text, str14 text, str15 text, str16 text
                )
            """)
            conn.commit()

    def get_stats(self):
        """Returns count of cards and scripts"""
        total_datas = 0
        total_texts = 0
        if os.path.exists(self.cdb_path):
            try:
                with sqlite3.connect(self.cdb_path) as conn:
                    c = conn.cursor()
                    total_datas = c.execute("SELECT count(*) FROM datas").fetchone()[0]
                    total_texts = c.execute("SELECT count(*) FROM texts").fetchone()[0]
            except Exception as e:
                logger.error(f"Error querying cards.cdb: {e}")

        total_scripts = 0
        if os.path.exists(self.script_dir):
            total_scripts = len([f for f in os.listdir(self.script_dir) if f.startswith("c") and f.endswith(".lua")])

        return {
            "cdb_path": self.cdb_path,
            "script_dir": self.script_dir,
            "total_cards_datas": total_datas,
            "total_cards_texts": total_texts,
            "total_lua_scripts": total_scripts
        }

    def has_card(self, card_id: int) -> bool:
        """Checks if card exists in local CDB"""
        try:
            with sqlite3.connect(self.cdb_path) as conn:
                c = conn.cursor()
                row = c.execute("SELECT id FROM datas WHERE id = ?", (card_id,)).fetchone()
                return row is not None
        except Exception:
            return False

    def has_script(self, card_id: int) -> bool:
        """Checks if c{card_id}.lua exists in script_dir"""
        path = os.path.join(self.script_dir, f"c{card_id}.lua")
        return os.path.exists(path) and os.path.getsize(path) > 0

    def fetch_script_jit(self, card_id: int) -> bool:
        """Downloads c{card_id}.lua directly from ProjectIgnis CardScripts"""
        script_file = os.path.join(self.script_dir, f"c{card_id}.lua")
        if os.path.exists(script_file) and os.path.getsize(script_file) > 0:
            return True

        subpaths = [
            f"official/c{card_id}.lua",
            f"pre-release/c{card_id}.lua",
            f"rush/c{card_id}.lua"
        ]
        
        for sp in subpaths:
            url = f"https://raw.githubusercontent.com/ProjectIgnis/CardScripts/master/{sp}"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Auratic-YGO-Ingestor/1.0"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status == 200:
                        content = resp.read()
                        with open(script_file, "wb") as f:
                            f.write(content)
                        logger.info(f"⚡ JIT Script fetched: c{card_id}.lua ({len(content)} bytes)")
                        return True
            except Exception:
                continue

        logger.warning(f"Could not find Lua script for card {card_id} online.")
        return False

    def fetch_metadata_jit(self, card_id: int) -> bool:
        """Fetches card info from YGOProDeck API and inserts into SQLite CDB"""
        if self.has_card(card_id):
            return True

        url = f"{YGOPRODECK_API_URL}?id={card_id}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Auratic-YGO-Ingestor/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                cards = data.get("data", [])
                if not cards:
                    return False
                c = cards[0]
                
                # Compute approximate type mask
                raw_type = c.get("type", "")
                type_code = 0
                if "Monster" in raw_type: type_code |= 0x1
                if "Spell" in raw_type: type_code |= 0x2
                if "Trap" in raw_type: type_code |= 0x4
                if "Normal" in raw_type: type_code |= 0x10
                if "Effect" in raw_type: type_code |= 0x20
                if "Fusion" in raw_type: type_code |= 0x40
                if "Ritual" in raw_type: type_code |= 0x80
                if "Synchro" in raw_type: type_code |= 0x2000
                if "XYZ" in raw_type: type_code |= 0x800000
                if "Tuner" in raw_type: type_code |= 0x1000
                if "Link" in raw_type: type_code |= 0x4000000

                attrib_map = {"EARTH": 1, "WATER": 2, "FIRE": 4, "WIND": 8, "LIGHT": 16, "DARK": 32, "DIVINE": 64}
                attribute = attrib_map.get(c.get("attribute", ""), 0)

                race_map = {
                    "Warrior": 0x1, "Spellcaster": 0x2, "Fairy": 0x4, "Fiend": 0x8,
                    "Zombie": 0x10, "Machine": 0x20, "Aqua": 0x40, "Pyro": 0x80,
                    "Rock": 0x100, "Winged Beast": 0x200, "Plant": 0x400, "Insect": 0x800,
                    "Thunder": 0x1000, "Dragon": 0x2000, "Beast": 0x4000, "Beast-Warrior": 0x8000,
                    "Dinosaur": 0x10000, "Fish": 0x20000, "Sea Serpent": 0x40000, "Reptile": 0x80000,
                    "Psychic": 0x100000, "Divine-Beast": 0x200000, "Creator-God": 0x400000,
                    "Wyrm": 0x800000, "Cyberse": 0x1000000, "Illusion": 0x2000000
                }
                race = race_map.get(c.get("race", ""), 0)

                with sqlite3.connect(self.cdb_path) as conn:
                    cur = conn.cursor()
                    cur.execute("""
                        INSERT OR REPLACE INTO datas (id, ot, alias, setcode, type, atk, def, level, race, attribute, category)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (card_id, 3, 0, 0, type_code, c.get("atk", 0), c.get("def", 0), c.get("level", 0), race, attribute, 0))
                    
                    cur.execute("""
                        INSERT OR REPLACE INTO texts (id, name, desc, str1, str2, str3, str4, str5, str6, str7, str8, str9, str10, str11, str12, str13, str14, str15, str16)
                        VALUES (?, ?, ?, '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '')
                    """, (card_id, c.get("name", f"Card {card_id}"), c.get("desc", "")))
                    conn.commit()
                    
                logger.info(f"⚡ JIT Metadata injected into cards.cdb for: {c.get('name')} ({card_id})")
                return True
        except Exception as e:
            logger.error(f"Error fetching JIT metadata for {card_id}: {e}")
            return False

    def validate_and_sync_deck(self, card_ids):
        """Ensures every single card in the deck has DB entry and Lua script"""
        missing_meta = 0
        missing_scripts = 0
        for cid in set(card_ids):
            if cid <= 0: continue
            if not self.has_card(cid):
                if self.fetch_metadata_jit(cid):
                    missing_meta += 1
            if not self.has_script(cid):
                if self.fetch_script_jit(cid):
                    missing_scripts += 1
        return {"missing_meta_fixed": missing_meta, "missing_scripts_fixed": missing_scripts}

    def sync_full_database(self):
        """Downloads latest BabelCDB and CardScripts repo, merging everything"""
        logger.info("🔄 Starte vollständigen Sync mit ProjectIgnis (BabelCDB & CardScripts)...")
        temp_cdb = "/tmp/babel_cards.cdb"
        
        # 1. Pull latest official cards.cdb
        try:
            logger.info(f"Lade offizielle BabelCDB herunter: {BABEL_CDB_URL}")
            req = urllib.request.Request(BABEL_CDB_URL, headers={"User-Agent": "Auratic-YGO-Ingestor/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp, open(temp_cdb, "wb") as out:
                shutil.copyfileobj(resp, out)
            
            with sqlite3.connect(self.cdb_path) as conn:
                conn.execute(f"ATTACH DATABASE '{temp_cdb}' AS babel")
                conn.execute("INSERT OR REPLACE INTO datas SELECT * FROM babel.datas")
                conn.execute("INSERT OR REPLACE INTO texts SELECT * FROM babel.texts")
                conn.commit()
                conn.execute("DETACH DATABASE babel")
            logger.info("✅ BabelCDB erfolgreich in lokale cards.cdb gemerged!")
        except Exception as e:
            logger.error(f"Fehler beim BabelCDB-Sync: {e}")
        finally:
            if os.path.exists(temp_cdb):
                os.remove(temp_cdb)

        # 2. Pull CardScripts repo via shallow git clone
        temp_git_dir = "/tmp/ProjectIgnis_CardScripts"
        try:
            if os.path.exists(temp_git_dir):
                shutil.rmtree(temp_git_dir, ignore_errors=True)
            logger.info(f"Klone CardScripts (shallow)...")
            subprocess.run(["git", "clone", "--depth", "1", CARDSCRIPTS_GIT_URL, temp_git_dir], check=True, capture_output=True)
            
            # Copy all official scripts directly into script_dir
            official_dir = os.path.join(temp_git_dir, "official")
            copied = 0
            if os.path.exists(official_dir):
                for f in os.listdir(official_dir):
                    if f.endswith(".lua"):
                        src = os.path.join(official_dir, f)
                        dst = os.path.join(self.script_dir, f)
                        shutil.copy2(src, dst)
                        copied += 1
            
            # Copy core Lua files (constant.lua, utility.lua, proc_*.lua)
            for f in os.listdir(temp_git_dir):
                if f.endswith(".lua"):
                    src = os.path.join(temp_git_dir, f)
                    dst = os.path.join(self.script_dir, f)
                    shutil.copy2(src, dst)

            logger.info(f"✅ {copied} offizielle Lua-Kartenskripte und Core-Dateien aktualisiert!")
        except Exception as e:
            logger.error(f"Fehler beim CardScripts-Sync: {e}")
        finally:
            if os.path.exists(temp_git_dir):
                shutil.rmtree(temp_git_dir, ignore_errors=True)

        stats = self.get_stats()
        logger.info(f"📊 Neuer Datenbank-Stand: {stats['total_cards_datas']} Karten in DB, {stats['total_lua_scripts']} Lua-Skripte aktiv.")
        return stats

if __name__ == "__main__":
    ingestor = AutoCardDBSync()
    print("Aktueller Status:", ingestor.get_stats())
    if len(sys.argv) > 1 and sys.argv[1] == "--full":
        ingestor.sync_full_database()
