import torch
import torch.nn as nn
import random
import os
from loguru import logger
from ygo_sqlite_bridge import YGOSqliteBridge

class EvolutionaryDeckBuilder(nn.Module):
    def __init__(self, total_cards, initial_weights=None):
        super().__init__()
        if initial_weights is not None:
            self.card_fitness = nn.Parameter(initial_weights)
        else:
            self.card_fitness = nn.Parameter(torch.ones(total_cards))
            
        self.optimizer = torch.optim.Adam([self.card_fitness], lr=0.05)

    def generate_deck(self, total_cards, deck_size=40):
        """Wählt die Top-Karten mit dem höchsten Fitness-Score + Mutation Noise"""
        noise = torch.randn(total_cards) * 0.15
        noisy_fitness = self.card_fitness + noise
        
        top_indices = torch.topk(noisy_fitness, deck_size).indices
        return top_indices.tolist()

    def apply_decay(self, deck, won_match):
        """Belohnt Gewinner-Decks und decayed Verlierer-Kombos hart"""
        self.optimizer.zero_grad()
        loss = 0
        
        if won_match:
            # Reward: Fitness der Gewinner-Karten steigt
            for idx in deck:
                loss -= self.card_fitness[idx]
        else:
            # DECAY: Fitness der Verlierer-Karten wird bestraft
            for idx in deck:
                loss += self.card_fitness[idx] * 2.5
                
        loss.backward()
        self.optimizer.step()
        
        with torch.no_grad():
            self.card_fitness.clamp_(min=0.01)

def run_evolution(generations=10000, deck_size=40):
    logger.info("🧬 Starte echten Gen 2 Evolutionary Deckbuilder mit SQLite-Anbindung...")
    bridge = YGOSqliteBridge()
    total_cards = len(bridge.cards)
    
    # Intelligenter Prior basierend auf Kartentext-Synergien (ATK, Effekte, Handtraps)
    initial_priors = torch.ones(total_cards)
    for idx, card in enumerate(bridge.cards):
        desc = (card.get("desc") or "").lower()
        atk = card.get("atk", 0)
        
        # Bonus für spielstarke Mechaniken
        if "negate" in desc: initial_priors[idx] += 0.5
        if "destroy" in desc: initial_priors[idx] += 0.3
        if "special summon" in desc: initial_priors[idx] += 0.4
        if "draw" in desc: initial_priors[idx] += 0.4
        if "add" in desc and "deck" in desc: initial_priors[idx] += 0.6
        if atk >= 2400: initial_priors[idx] += 0.3
        
    evolution = EvolutionaryDeckBuilder(total_cards, initial_weights=initial_priors)
    
    logger.info(f"⚡ Simuliere {generations} Ouroboros-Matches mit Decay-Faktor über {total_cards} reale Karten...")
    
    for generation in range(1, generations + 1):
        current_deck = evolution.generate_deck(total_cards, deck_size=deck_size)
        
        # Win-Probability wächst mit der durchschnittlichen Fitness der gewählten Karten
        current_avg_fitness = evolution.card_fitness[current_deck].mean().item()
        win_chance = min(0.85, 0.15 + (current_avg_fitness / 10.0))
        won = random.random() < win_chance
        
        evolution.apply_decay(current_deck, won)
        
        if generation % 2500 == 0:
            top_fit = torch.max(evolution.card_fitness).item()
            min_fit = torch.min(evolution.card_fitness).item()
            logger.info(f"🔄 Generation {generation}/{generations} | Winrate: {win_chance*100:.1f}% | Max Fitness: {top_fit:.2f} | Min (Decayed): {min_fit:.2f}")

    # Top 40 Karten extrahieren
    top_indices = torch.topk(evolution.card_fitness, deck_size).indices.tolist()
    
    logger.success("\n🏆 EVOLVED DECK AUS RECHNER-GENETIK GENERIERT:")
    for i, idx in enumerate(top_indices[:15]):
        info = bridge.get_card_info(idx)
        print(f"  {i+1:2d}. [ID: {info['id']}] {info['name']} (ATK: {info['atk']} | DEF: {info['def']})")
    print(f"  ... und {deck_size - 15} weitere reale Karten.")
    
    output_ydk = "Auratic_Gen2_Evolved.ydk"
    bridge.export_to_ydk(output_ydk, top_indices)
    return output_ydk

if __name__ == "__main__":
    run_evolution(generations=10000, deck_size=40)
