import time
import random
from loguru import logger

class MasterRLLoop:
    def __init__(self):
        logger.info("Initialisiere Auratic Prime RL-Pipeline...")
        self.actions = {0: "END_PHASE", 1: "SUMMON", 4: "ACTIVATE_EFFECT", 5: "ATTACK"}
        
    def eye_observe(self, opp_lp):
        """Das Auge: Liest das Feld"""
        tensor_lp = opp_lp / 8000.0
        logger.info(f"👁️ AUGE: Sehe Gegner bei {tensor_lp:.2f} (normiert).")
        return tensor_lp
        
    def brain_decide(self, tensor_state):
        """Das Gehirn: Wählt die Aktion basierend auf dem Tensor"""
        logger.info("🧠 GEHIRN: Berechne Synergie-Wahrscheinlichkeiten...")
        time.sleep(0.5)
        # Wenn Gegner wenig LP hat, greife an, sonst baue das Feld auf
        if tensor_state < 0.6:
            return 5 # ATTACK
        else:
            return random.choice([1, 4]) # SUMMON oder ACTIVATE
            
    def muscle_execute(self, action_id):
        """Der Muskel: Feuert den Befehl ab"""
        action_name = self.actions[action_id]
        logger.success(f"💪 MUSKEL: Führe C++ Kommando aus -> [{action_name}]")
        return action_name

    def run_dummy_duel(self):
        opp_lp = 8000
        turn = 1
        
        while opp_lp > 0 and turn <= 4:
            logger.info(f"\n========== ZUG {turn} ==========")
            
            # Step 1: Observe
            tensor_state = self.eye_observe(opp_lp)
            
            # Step 2: Decide
            action_id = self.brain_decide(tensor_state)
            
            # Step 3: Execute
            result = self.muscle_execute(action_id)
            
            # Engine Update (Mock)
            if result == "ATTACK":
                dmg = 3000
                opp_lp -= dmg
                logger.warning(f"💥 C++ ENGINE: Gegner kassiert {dmg} Schaden! (Rest-LP: {opp_lp})")
            elif result == "SUMMON":
                logger.info("🔄 C++ ENGINE: Neues Monster liegt auf Feld-Zone 1.")
            elif result == "ACTIVATE_EFFECT":
                logger.info("🔄 C++ ENGINE: Falle/Zauber ausgelöst.")
                
            turn += 1
            time.sleep(1)
            
        if opp_lp <= 0:
            logger.success("\n🏆 LGNN HAT DAS DUELL GEWONNEN! (Backpropagation Reward: +1.0)")
        else:
            logger.error("\n💀 LGNN HAT VERLOREN. (Backpropagation Reward: -1.0)")

if __name__ == "__main__":
    loop = MasterRLLoop()
    loop.run_dummy_duel()
