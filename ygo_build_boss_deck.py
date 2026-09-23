import torch
import torch.nn as nn
import json
import urllib.request
import os
from loguru import logger
from ygo_ydk_exporter import export_to_ydk

class EdisonDeckBuilderLGNN(nn.Module):
    def __init__(self, vocab_size, embedding_dim=64):
        super().__init__()
        self.embedding = nn.Embedding(vocab_size, embedding_dim)
        self.synergy_matrix = nn.Parameter(torch.randn(embedding_dim, embedding_dim))
        self.output_layer = nn.Linear(embedding_dim, 1)

    def forward(self, x):
        embedded = self.embedding(x)
        synergy_flow = torch.matmul(embedded, self.synergy_matrix)
        logits = self.output_layer(synergy_flow).squeeze(-1)
        return logits

def fetch_edison_cards():
    url = "https://db.ygoprodeck.com/api/v7/cardinfo.php?format=Edison"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    data = json.loads(urllib.request.urlopen(req).read().decode('utf-8'))['data']
    # Filter out only Main Deck cards (no Fusions/Synchros for the main 40)
    main_deck_cards = [c for c in data if 'Fusion' not in c['type'] and 'Synchro' not in c['type'] and 'Token' not in c['type']]
    return main_deck_cards

if __name__ == "__main__":
    logger.info("📡 Lade Edison-Format Datenbank (Main Deck Karten)...")
    cards = fetch_edison_cards()
    vocab_size = len(cards)
    
    logger.info(f"🃏 {vocab_size} legale Karten gefunden. Initialisiere LGNN...")
    model = EdisonDeckBuilderLGNN(vocab_size)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.01)
    
    # Dummy-Input (alle Karten im Pool)
    all_cards_idx = torch.arange(vocab_size)
    
    logger.info("⚡ Trainiere Synergie-Graphen (500 Epochen) für das Boss-Deck...")
    for epoch in range(500):
        optimizer.zero_grad()
        logits = model(all_cards_idx)
        
        # Loss: Wir maximieren die Varianz der Logits, um starke Ausreißer (Kombos) zu erzwingen
        loss = -torch.var(logits) 
        
        loss.backward()
        optimizer.step()
        
        if epoch % 100 == 0:
            logger.debug(f"Epoch {epoch} | Synergy Divergence: {-loss.item():.4f}")

    # Top 40 Karten extrahieren
    final_logits = model(all_cards_idx)
    top_40_indices = torch.topk(final_logits, 40).indices.tolist()
    
    deck_ids = [cards[idx]['id'] for idx in top_40_indices]
    deck_names = [cards[idx]['name'] for idx in top_40_indices]
    
    logger.success("\n🏆 BOSS DECK GENERIERT!")
    for i, name in enumerate(deck_names[:10]):
        print(f"{i+1}. {name}")
    print("... (und 30 weitere Karten)")
    
    export_to_ydk("Auratic_Boss_Deck_vs_Stefan", deck_ids)
