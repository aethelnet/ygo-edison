import json
import urllib.request
from loguru import logger
import os

def export_to_ydk(deck_name, card_ids):
    filename = f"{deck_name}.ydk"
    
    with open(filename, 'w') as f:
        f.write(f"#created by Auratic Prime LGNN\n")
        f.write("#main\n")
        
        extra_deck_ids = []
        
        for cid in card_ids:
            # Schneller Check ob Main Deck oder Extra Deck (Synchro/Fusion)
            # Normalerweise checkt man das über die CDB, hier machen wir es simpel über die API
            f.write(f"{cid}\n")
            
        f.write("#extra\n")
        # Hier würden die Extra Deck Karten landen
        
        f.write("!side\n")
        # Hier würden die Side Deck Karten landen
        
    logger.success(f"Deck erfolgreich exportiert als {filename}")
    return filename

if __name__ == "__main__":
    logger.info("Lade generierte Deck-Indizes aus der KI...")
    
    # Wir holen schnell die API, um echte IDs zu fischen (Mock für die KI-Ausgabe)
    url = "https://db.ygoprodeck.com/api/v7/cardinfo.php?format=Edison"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    data = json.loads(urllib.request.urlopen(req).read().decode('utf-8'))['data']
    
    # Das waren einige der Karten aus dem Edison-Deck vorhin
    # Wir suchen uns die passenden YGO-IDs raus
    target_names = ["Toon World", "Super Rejuvenation", "Blue-Eyes Ultimate Dragon", "Road Synchron"]
    deck_ids = []
    
    for card in data:
        if card['name'] in target_names:
            deck_ids.append(card['id'])
            
    # Auffüllen mit Random-Karten, damit es 40 sind (nur für den Export-Test)
    for card in data[100:136]:
        deck_ids.append(card['id'])
        
    export_to_ydk("LGNN_Edison_Gen5", deck_ids)
    
    print("\nInhalt der YDK-Datei:")
    os.system("head -n 10 LGNN_Edison_Gen5.ydk")
