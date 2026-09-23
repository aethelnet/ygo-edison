import torch
from loguru import logger

class YGOObservationSpace:
    def __init__(self):
        # Das Auge der KI: Wie viele Features sehen wir?
        self.feature_dim = 120 # Ein YGO-Board hat ca. 120 relevante Datenpunkte
        
    def extract_state(self, game_state):
        """Übersetzt das C++ Spielbrett in die Matrix"""
        logger.info("Scanne aktuelles Spielbrett...")
        
        # Leerer Tensor
        state_tensor = torch.zeros(self.feature_dim)
        
        # 1. Globale Stats (Normalisiert zwischen 0 und 1)
        state_tensor[0] = game_state['my_lp'] / 8000.0
        state_tensor[1] = game_state['opp_lp'] / 8000.0
        state_tensor[2] = game_state['my_hand_size'] / 10.0
        state_tensor[3] = game_state['opp_hand_size'] / 10.0
        
        # 2. Feld-Analyse (z.B. Monster-Zone 1)
        if game_state['opp_monster_1']:
            state_tensor[10] = 1.0 # Zone besetzt
            state_tensor[11] = game_state['opp_monster_1']['atk'] / 5000.0
            state_tensor[12] = 1.0 if game_state['opp_monster_1']['is_face_down'] else 0.0
            
        return state_tensor

if __name__ == "__main__":
    observer = YGOObservationSpace()
    
    # Simulierter Spielstand aus der Engine (Stefan führt!)
    current_game_state = {
        "my_lp": 4000,
        "opp_lp": 8000,
        "my_hand_size": 3,
        "opp_hand_size": 5,
        "opp_monster_1": {"atk": 2500, "is_face_down": False} # Z.B. Dunkler Magier
    }
    
    matrix_vision = observer.extract_state(current_game_state)
    logger.success(f"Spielstand erfolgreich in {matrix_vision.shape[0]}-dimensionalen Tensor übersetzt.")
    
    print("\nSo sieht das LGNN das aktuelle Duell (Auszug):")
    print(f"Index [0] - Meine LP (norm.):    {matrix_vision[0].item():.2f}")
    print(f"Index [1] - Stefans LP (norm.):  {matrix_vision[1].item():.2f}")
    print(f"Index [3] - Stefans Handgröße:   {matrix_vision[3].item():.2f}")
    print(f"Index [11]- Gegner Monster 1 ATK:{matrix_vision[11].item():.2f}")
