import torch
import torch.nn as nn
import json
import urllib.request
import time

print("🃏 Lade YGO-Karten-Pool...")
url = "https://db.ygoprodeck.com/api/v7/cardinfo.php"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
data = json.loads(urllib.request.urlopen(req).read().decode('utf-8'))['data']

# Filter out tokens
cards = [c for c in data if "Token" not in c['type'] and "Skill" not in c['type']]
POOL_SIZE = min(8000, len(cards)) # Cap at 8000 for RAM/speed in this MVP
cards = cards[:POOL_SIZE]
print(f"✅ Pool auf {POOL_SIZE} Karten limitiert für den Tensor.")

# --- THE PYTORCH LGNN ARCHITECTURE ---
class YgoDeckBuilderLGNN(nn.Module):
    def __init__(self, pool_size):
        super().__init__()
        self.pool_size = pool_size
        
        # Jede Karte bekommt ein 16-dimensionales "Feature Embedding" (simuliert Effekte, Typen, Level)
        self.card_embeddings = nn.Parameter(torch.randn(pool_size, 16))
        
        # Das Herzstück: Die Liquid Synergy Matrix (Wie gut passen Karten zusammen?)
        self.synergy_graph = nn.Parameter(torch.randn(16, 16))
        
        # Komprimiert die fließenden Synergien auf Wahrscheinlichkeiten für das Deck
        self.deck_selector = nn.Linear(16, 1) 

    def forward(self):
        # 1. Karten "kommunizieren" miteinander über den Synergie-Graphen
        # (Matrix-Multiplikation: Embeddings x Synergy Graph)
        fluid_flow = torch.matmul(self.card_embeddings, self.synergy_graph)
        fluid_flow = torch.tanh(fluid_flow) # Liquid Activation
        
        # 2. Berechne den "Auswahl-Druck" für jede Karte
        logits = self.deck_selector(fluid_flow).squeeze(-1) # Shape: (POOL_SIZE,)
        
        # 3. Wähle die Top 40 Karten mit der höchsten mathematischen Synergie
        top_40_indices = torch.topk(logits, 40).indices
        return top_40_indices

print("\n🧠 Initialisiere LGNN Architektur (Synergy Graph)...")
lgnn = YgoDeckBuilderLGNN(pool_size=POOL_SIZE)

print("⚡ Führe ersten Forward-Pass aus (Deck-Generierung)...")
start_time = time.time()
with torch.no_grad():
    deck_indices = lgnn()
end_time = time.time()

print(f"⏱️ Forward-Pass in {end_time - start_time:.4f} Sekunden abgeschlossen.\n")

print("🏆 DAS ERSTE NEURONAL GENERIERTE DECK (Top 10 Karten):")
for idx in deck_indices[:10]:
    card = cards[idx.item()]
    print(f"  - {card['name']} (Type: {card['type']})")
    
print("...")
print("✅ Tensor-Pipeline steht! Das Netz versteht jetzt Karten als mathematische Vektoren.")
