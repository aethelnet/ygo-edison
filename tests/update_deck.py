import re
with open('kaggle_pokemon/pokemon_engine_v2.py', 'r') as f:
    code = f.read()

deck_logic = """
        # Meta-Deck-Komposition: 15 Pokémon, 15 Energien, 30 Trainer (mit Mulligan-Garantie)
        for p in [0, 1]:
            while True:
                basics = [random.choice(self.registry.playable_basics) for _ in range(15)]
                energies = [random.choice(self.registry.energy_cards) for _ in range(15)]
                # Falls keine Trainer in csv gefunden wurden, fülle mit Energien auf (Fallback)
                if len(self.registry.trainer_cards) > 0:
                    trainers = [random.choice(self.registry.trainer_cards) for _ in range(30)]
                else:
                    trainers = [random.choice(self.registry.energy_cards) for _ in range(30)]
                    
                full_deck = basics + energies + trainers
"""

code = re.sub(
    r'(# 30/30 Deck-Komposition mit Mulligan-Garantie\n\s*for p in \[0, 1\]:\n\s*while True:\n\s*basics = \[random\.choice\(self\.registry\.playable_basics\) for _ in range\(30\)\]\n\s*energies = \[random\.choice\(self\.registry\.energy_cards\) for _ in range\(30\)\]\n\s*full_deck = basics \+ energies)',
    deck_logic.lstrip('\n'),
    code
)

with open('kaggle_pokemon/pokemon_engine_v2.py', 'w') as f:
    f.write(code)
