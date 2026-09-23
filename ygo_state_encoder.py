import torch
import torch.nn as nn

class YGOStateEncoder(nn.Module):
    def __init__(self):
        super().__init__()
        # Wir verwandeln das Yu-Gi-Oh Spielfeld in einen 512-dimensionalen Tensor!
        # Input-Größe: 
        # 2x Lebenspunkte (Wir, Gegner)
        # 5x Monsterzonen Wir (ATK, DEF, Level, Position)
        # 5x Monsterzonen Gegner (sichtbare Daten)
        # 40x Deck-Karten-Verhältnisse, Handkarten etc.
        self.state_dim = 2 + (5 * 4) + (5 * 4) + 40 
        
        self.encoder = nn.Sequential(
            nn.Linear(self.state_dim, 256),
            nn.LayerNorm(256),
            nn.LeakyReLU(),
            nn.Linear(256, 512) # Das ist der finale 'Game Theory Tensor'
        )

    def extract_state_from_cpp(self, ocgcore, pduel):
        """
        Diese Funktion pflanzt den Ouroboros ein: 
        Sie liest den rohen C++ Arbeitsspeicher und zieht die echten Spiel-Werte heraus.
        """
        state_vector = []
        
        # 1. Lebenspunkte (LP) direkt aus der Engine ziehen
        # (Mock-Werte für die Architektur-Demonstration)
        my_lp = 8000 / 8000.0       # Normalisiert zwischen 0 und 1
        opp_lp = 4000 / 8000.0      
        state_vector.extend([my_lp, opp_lp])
        
        # 2. Eigene Monster-Zonen scannen (ATK/DEF)
        for zone_index in range(5):
            # In Phase 38 rufen wir hier ocgcore.query_field() auf!
            # Wir füttern das Netz mit den exakten Werten:
            atk = 3000 / 5000.0     # Normalisierte ATK
            def_val = 2500 / 5000.0 # Normalisierte DEF
            level = 8 / 12.0        # Level 1-12
            position = 1.0          # 1.0 = Offen Angriff, 0.0 = Verdeckt Def
            state_vector.extend([atk, def_val, level, position])
            
        # 3. Gegnerische Monster-Zonen scannen
        for zone_index in range(5):
            state_vector.extend([0.0, 0.0, 0.0, 0.0]) # Z.B. Leeres Feld
            
        # 4. Handkarten / Friedhof / Sonstiges (Padding für den Vector)
        state_vector.extend([0.0] * 40)
        
        # Umwandlung in PyTorch Tensor
        return torch.tensor(state_vector, dtype=torch.float32)

if __name__ == "__main__":
    print("🌱 Ouroboros State Encoder initialisiert!")
    encoder = YGOStateEncoder()
    mock_state = encoder.extract_state_from_cpp(None, None)
    encoded_tensor = encoder.encoder(mock_state)
    print(f"✅ Spielfeld erfolgreich in einen Tensor umgewandelt!")
    print(f"🧠 Tensor Shape für das LGNN: {encoded_tensor.shape}")
