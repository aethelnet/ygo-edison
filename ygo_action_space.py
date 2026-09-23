import random
import time
from loguru import logger

class YGOActionSpace:
    def __init__(self):
        # Alle möglichen Basis-Aktionen in Yu-Gi-Oh
        self.ACTIONS = {
            0: "PHASE_WECHSELN",
            1: "NORMALBESCHWÖRUNG",
            2: "SPEZIALBESCHWÖRUNG",
            3: "KARTE_SETZEN",
            4: "EFFEKT_AKTIVIEREN",
            5: "ANGREIFEN"
        }
        
    def get_legal_moves(self, state):
        """Fragt die C++ Engine: Was darf ich jetzt gerade tun?"""
        # Mock: Je nach State gibt es andere legale Züge
        if state == "MAIN_PHASE_1":
            return [0, 1, 3, 4] # Darf Phase wechseln, beschwören, setzen, aktivieren
        elif state == "BATTLE_PHASE":
            return [0, 5]       # Darf angreifen oder Phase beenden
        return [0]

    def translate_nn_output(self, nn_action_id, legal_moves):
        """Zwingt die KI, nur legale Züge auszuführen (Masking)"""
        if nn_action_id not in legal_moves:
            logger.warning(f"KI wollte illegalen Zug {nn_action_id} machen. Überschreibe mit sicherem Zug (0).")
            return 0
        return nn_action_id

    def execute_combo(self):
        logger.info("Starte Action-Space Interface...")
        
        # Zug 1: Main Phase
        current_state = "MAIN_PHASE_1"
        legal_moves = self.get_legal_moves(current_state)
        
        logger.info(f"[State: {current_state}] Legale Optionen: {legal_moves}")
        
        # Die KI "entscheidet" sich für Aktion 1 (Normalbeschwörung)
        ki_entscheidung = 1
        gefilterte_aktion = self.translate_nn_output(ki_entscheidung, legal_moves)
        logger.success(f"🤖 KI feuert Aktion: {self.ACTIONS[gefilterte_aktion]} (Karte: Rescue Rabbit)")
        time.sleep(0.5)

        # Zug 2: Effekt aktivieren
        ki_entscheidung = 4
        gefilterte_aktion = self.translate_nn_output(ki_entscheidung, legal_moves)
        logger.success(f"🤖 KI feuert Aktion: {self.ACTIONS[gefilterte_aktion]} (Effekt von Rescue Rabbit)")
        time.sleep(0.5)
        
        # Die C++ Engine reagiert auf den Effekt
        logger.info("🔄 C++ Engine updated den State...")
        time.sleep(0.5)
        logger.success("🤖 KI feuert Aktion: SPEZIALBESCHWÖRUNG (2x Normales Monster aus dem Deck!)")
        
if __name__ == "__main__":
    action_space = YGOActionSpace()
    action_space.execute_combo()
