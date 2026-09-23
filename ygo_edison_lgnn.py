import torch
import torch.nn as nn
import json
import urllib.request
import time
from loguru import logger

logger.info("🃏 Kontaktiere YGOProDeck API (Filter: Edison Format)...")
url = "https://db.ygoprodeck.com/api/v7/cardinfo.php?format=Edison"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
response = urllib.request.urlopen(req)
data = json.loads(response.read().decode('utf-8'))['data']

cards = [c for c in data if "Token" not in c['type'] and "Skill" not in c['type']]
POOL_SIZE = len(cards)
logger.success(f"✅ Edison-Karten-Pool geladen! Exakt {POOL_SIZE} Karten stehen zur Verfügung.")

class EdisonDeckBuilderLGNN(nn.Module):
    def __init__(self, pool_size):
        super().__init__()
        self.card_embeddings = nn.Parameter(torch.randn(pool_size, 32))
        self.synergy_graph = nn.Parameter(torch.randn(32, 32))
        self.deck_selector = nn.Linear(32, 1) 

    def forward(self, return_logits=False):
        fluid_flow = torch.matmul(self.card_embeddings, self.synergy_graph)
        fluid_flow = torch.tanh(fluid_flow) 
        logits = self.deck_selector(fluid_flow).squeeze(-1) 
        if return_logits:
            return logits
        return torch.topk(logits, 40).indices

logger.info("🧠 Initialisiere Edison-LGNN Architektur...")
lgnn = EdisonDeckBuilderLGNN(pool_size=POOL_SIZE)

optimizer = torch.optim.Adam(lgnn.parameters(), lr=0.05)
logger.info("⚡ Lasse das Netz 5 Epochen lang nach Retro-Synergien suchen...")

for epoch in range(5):
    optimizer.zero_grad()
    logits = lgnn(return_logits=True)
    loss = logits.mean() 
    loss.backward()
    optimizer.step()
    time.sleep(0.3)

logger.info("Extrahieren des ersten generierten Edison-Decks...")
final_deck_indices = lgnn()

print("\n=============================================")
print("🏆 LGNN GENERIERTES EDISON-FORMAT DECK (Auszug)")
print("=============================================\n")

monster_count = spell_count = trap_count = 0

for idx in final_deck_indices[:15]:  
    card = cards[idx.item()]
    ctype = card['type']
    if "Monster" in ctype: monster_count += 1
    elif "Spell" in ctype: spell_count += 1
    elif "Trap" in ctype: trap_count += 1
    print(f"[{ctype}] {card['name']}")

print("\n=============================================")
print(f"Statistik (Top 15): {monster_count} Monster | {spell_count} Zauber | {trap_count} Fallen")
print("=============================================\n")
