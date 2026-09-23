import json
import random
import urllib.request

print("🃏 Lade echte Kartendaten via YGOProDeck API (Quick & Dirty)...")
url = "https://db.ygoprodeck.com/api/v7/cardinfo.php"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
response = urllib.request.urlopen(req)
data = json.loads(response.read().decode('utf-8'))['data']

# Filter out tokens/skill cards
cards = [c for c in data if "Token" not in c['type'] and "Skill" not in c['type']]
print(f"✅ {len(cards)} legale Karten im Pool gefunden.\n")

def simulate_lgnn_deck():
    """Baut ein zufälliges 40-Karten-Deck und berechnet den echten Preis."""
    deck = random.sample(cards, 40)
    total_cost = 0.0
    deck_names = []
    
    for card in deck:
        deck_names.append(card['name'])
        # Try to get cardmarket price, default to 0.10 if missing
        try:
            price = float(card['card_prices'][0]['cardmarket_price'])
            total_cost += price
        except:
            total_cost += 0.10
            
    # Fake EDOPro Winrate (Mock)
    winrate = random.uniform(20.0, 85.0)
    fitness = (winrate ** 2) / (total_cost + 1)
    
    return deck_names, total_cost, winrate, fitness

print("🧠 [LGNN Mock] Simuliere 3 Decks in der Chaos Arena...")
best_deck = None
best_fitness = 0

for i in range(3):
    names, cost, winrate, fitness = simulate_lgnn_deck()
    if fitness > best_fitness:
        best_fitness = fitness
        best_deck = (names, cost, winrate)

names, cost, winrate = best_deck
print("\n🏆 BESTES ROGUE DECK GEFUNDEN:")
print(f"💰 Gesamtkosten (Cardmarket): {cost:.2f} €")
print(f"⚔️ Winrate vs. Snake-Eye:     {winrate:.1f} %")
print(f"🃏 Key Cards: {names[0]}, {names[1]}, {names[2]} ...\n")

# Persona Reacts
with open("active_personas.json", "r") as f:
    personas = json.load(f)
agent = personas[0] # Take Agent 7374

print(f"🎬 {agent['name']} ({agent['archetype']}) bereitet Content vor:")
print(f"[{agent['humor']} Tone] -> 'Sun Tzu sagte: Greife an, wo der Feind unvorbereitet ist. Die Meta-Sklaven geben 1.000 Euro aus, aber mein {cost:.2f}€ Deck mit {names[0]} zerstört sie. Video folgt.'")

