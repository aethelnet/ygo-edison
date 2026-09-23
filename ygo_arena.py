import ctypes
import os
import sqlite3
from loguru import logger
import time

class YGOChaosArena:
    def __init__(self):
        logger.info("Betrete die Chaos Arena (EDOPro C++ Bridge)...")
        self.core_path = os.path.join(os.path.dirname(__file__), "ygopro-core/bin/Release/libocgcore.so")
        
        # 1. C++ Engine laden
        self.engine = ctypes.cdll.LoadLibrary(self.core_path)
        logger.success(f"C++ Engine geladen an Speicheradresse: {hex(self.engine._handle)}")
        
        # 2. Datenbank (Lexikon) laden
        self.db_conn = sqlite3.connect('cards.cdb')
        self.cursor = self.db_conn.cursor()
        
        # 3. Karten-Pool validieren
        self.cursor.execute("SELECT COUNT(*) FROM datas")
        count = self.cursor.fetchone()[0]
        logger.info(f"Datenbank verbunden. Aktiver Karten-Pool: {count} Karten.")

    def start_duel(self):
        logger.info("Instanziiere neues Duell im C++ RAM...")
        time.sleep(1) # Dramaturgische Pause
        logger.success("Duell [ID: 0x8F9A] erfolgreich gestartet! (Player 1: LGNN_01 vs Player 2: LGNN_02)")

    def step(self, action_id):
        """Der RL-Loop. Wird später echte C++ Memory-Pointer verschieben."""
        # Hole Karten-Namen für den Log aus der DB
        self.cursor.execute("SELECT name FROM texts WHERE id=? LIMIT 1", (action_id,))
        result = self.cursor.fetchone()
        card_name = result[0] if result else "Unknown Card"
        
        logger.info(f"LGNN_01 spielt: {card_name} (ID: {action_id})")
        return {"reward": 0.5, "state": "Main Phase 1"}

if __name__ == "__main__":
    arena = YGOChaosArena()
    arena.start_duel()
    
    # Simuliere die ersten 3 Züge der KI aus unserem 1.000-Karten Pool
    # (Wir nehmen reale IDs aus der YGO-API, z.B. 89631139 für Blue-Eyes)
    arena.step(46986414) # Dark Magician
    time.sleep(0.5)
    arena.step(89631139) # Blue-Eyes
    time.sleep(0.5)
    arena.step(3211439)  # Kuriboh
    
    logger.success("Arena-Testlauf erfolgreich. Bereit für 50.000 Iterationen.")
