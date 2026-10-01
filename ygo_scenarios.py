"""
ygo_scenarios.py - Taktik-Szenarien & Daily Puzzles für die Yu-Gi-Oh! Duell-Arena
Ermöglicht das direkte Starten in vordefinierte Spielsituationen (Board States, Hände, Ketten).
"""

from typing import Dict, Any, List

SCENARIOS: Dict[str, Dict[str, Any]] = {
    "quickdraw_synchro": {
        "id": "quickdraw_synchro",
        "title": "Taktik 1: Quickdraw & Dandy Synchro Climb",
        "category": "Synchro & Friedhof-Trigger",
        "difficulty": "Mittel",
        "description": "Eröffne die klassische Edison Quickdraw-Dandy Kombo: Wirf Löwenzahn-Löwe ab, nutze die Fluff-Spielmarken für Synchrobeschwörungen und überwinde Stardust Dragon!",
        "objective": "Beschwöre Bohrkrieger oder Gerümpelzerstörer via Synchrobeschwörung und zerstöre Stardust Dragon.",
        "hint": "1. Aktiviere den Handeffekt von Quickdraw Synchron und wirf Dandylion auf den Friedhof. 2. Dandylions Zwangseffekt beschwört 2 Fluff-Spielmarken. 3. Nutze Quickdraw + 1 Token für Drill Warrior (Stufe 6) oder Quickdraw + 2 Tokens + Level Eater für Junk Destroyer!",
        "p0_lp": 4000,
        "p1_lp": 4000,
        "p0_hand": [20932152, 15341821, 14943837, 57421866], # Quickdraw, Dandylion, Debris Dragon, Level Eater
        "p0_mzone": [],
        "p0_szone": [],
        "p0_grave": [],
        "p0_extra": [3429238, 74860293, 44508094, 73580471, 7391448], # Drill Warrior, Junk Destroyer, Stardust Dragon, Black Rose, Goyo Guardian
        "p0_deck": [63977008, 19613556, 14087893, 44095762], # Junk Synchron, Heavy Storm, Book of Moon, Mirror Force
        "p1_hand": [],
        "p1_mzone": [
            {"code": 44508094, "seq": 2, "pos": 1} # Stardust Dragon faceup attack
        ],
        "p1_szone": [
            {"code": 44095762, "seq": 2, "pos": 8} # Mirror Force facedown
        ],
        "p1_grave": [],
        "p1_extra": [],
        "p1_deck": [29401950, 41420027, 70342110],
    },
    "caius_chain": {
        "id": "caius_chain",
        "title": "Taktik 2: Caius vs. Bodenlose Fallgrube",
        "category": "Tribut & Kettenglieder",
        "difficulty": "Mittel",
        "description": "Opfere Cyber Drache für Caius der Schattenmonarch. Die KI reagiert mit Bodenlose Fallgrube auf die Beschwörung. Nutze Buch des Mondes in der Kette, um Caius zu verdecken und seine Verbannung durchzubringen!",
        "objective": "Opfere Cyber Drache für Caius, verbanne Stardust Dragon und rette Caius vor der Fallgrube.",
        "hint": "Caius' Effekt aktiviert sich als Kettenglied 1. Wenn der Gegner Bodenlose Fallgrube als Kettenglied 2 kettet, kette Buch des Mondes als Kettenglied 3 auf deinen eigenen Caius!",
        "p0_lp": 4000,
        "p1_lp": 2400,
        "p0_hand": [9748752, 14087893, 19613556], # Caius, Book of Moon, Heavy Storm
        "p0_mzone": [
            {"code": 70095154, "seq": 2, "pos": 1} # Cyber Dragon faceup attack
        ],
        "p0_szone": [],
        "p0_grave": [],
        "p0_extra": [7391448, 44508094],
        "p0_deck": [26202165, 15341821, 63977008],
        "p1_hand": [],
        "p1_mzone": [
            {"code": 44508094, "seq": 2, "pos": 1} # Stardust Dragon
        ],
        "p1_szone": [
            {"code": 29401950, "seq": 2, "pos": 8} # Bottomless Trap Hole facedown
        ],
        "p1_grave": [],
        "p1_extra": [],
        "p1_deck": [44095762, 70342110],
    },
    "heavy_storm_negation": {
        "id": "heavy_storm_negation",
        "title": "Taktik 3: Counter-Trap Krieg (Heavy vs. Starlight & Solemn)",
        "category": "Konterfallen & Kettenglieder",
        "difficulty": "Experte",
        "description": "Ein epischer Spell-Speed 3 Schlagabtausch: Du aktivierst Schwerer Sturm. Der Gegner kontert mit Sternenstraße, um Stardust Dragon zu beschwören. Kette dein eigenes Feierliches Urteil an!",
        "objective": "Löse die Kette (Schwerer Sturm -> Sternenstraße -> Feierliches Urteil) erfolgreich auf und räume das gegnerische Feld ab.",
        "hint": "Achte auf deine Lebenspunkte: Feierliches Urteil kostet die Hälfte deiner aktuellen LP (Spell Speed 3). Nur Konterfallen können an Konterfallen gekettet werden!",
        "p0_lp": 8000,
        "p1_lp": 4000,
        "p0_hand": [19613556, 5318639], # Heavy Storm, MST
        "p0_mzone": [],
        "p0_szone": [
            {"code": 41420027, "seq": 2, "pos": 8} # Solemn Judgment facedown
        ],
        "p0_grave": [],
        "p0_extra": [],
        "p0_deck": [63977008, 9748752, 15341821],
        "p1_hand": [],
        "p1_mzone": [],
        "p1_szone": [
            {"code": 58120309, "seq": 1, "pos": 8}, # Starlight Road facedown
            {"code": 44095762, "seq": 2, "pos": 8}, # Mirror Force facedown
            {"code": 70342110, "seq": 3, "pos": 8}, # Dimensional Prison facedown
        ],
        "p1_grave": [],
        "p1_extra": [44508094], # Stardust Dragon in Extra
        "p1_deck": [29401950, 41420027],
    },
    "pot_of_avarice_loop": {
        "id": "pot_of_avarice_loop",
        "title": "Taktik 4: Friedhofs-Recycling mit Topf der Trägheit",
        "category": "Zielwahl & Ressourcen-Management",
        "difficulty": "Einfach",
        "description": "Dein Friedhof ist voll mit wertvollen Monstern. Wähle mit Topf der Trägheit exakt 5 Monster als Ziele, mische sie ins Deck zurück und ziehe 2 neue Karten!",
        "objective": "Aktiviere Topf der Trägheit, wähle 5 Friedhofs-Ziele und nutze die nachgezogenen Karten.",
        "hint": "Topf der Trägheit verlangt exakt 5 Monster im Friedhof. Achte bei der Zielauswahl im Dialog auf die genauen Monster.",
        "p0_lp": 4000,
        "p1_lp": 4000,
        "p0_hand": [67169062, 63977008], # Pot of Avarice, Junk Synchron
        "p0_mzone": [],
        "p0_szone": [],
        "p0_grave": [15341821, 20932152, 48686504, 21502796, 26202165], # Dandylion, Quickdraw, Lonefire, Ryko, Sangan
        "p0_extra": [3429238, 74860293, 7391448],
        "p0_deck": [9748752, 14943837, 19613556, 14087893, 44095762],
        "p1_hand": [],
        "p1_mzone": [
            {"code": 44508094, "seq": 2, "pos": 1}
        ],
        "p1_szone": [],
        "p1_grave": [],
        "p1_extra": [],
        "p1_deck": [44095762, 70342110],
    },
    "brionac_bounce": {
        "id": "brionac_bounce",
        "title": "Taktik 5: Brionac Feldkontrolle & Bounce-OTK",
        "category": "Ignition-Effekte & Handkarten-Kosten",
        "difficulty": "Mittel",
        "description": "Der Gegner hat ein mächtiges Monster und zwei gefährliche Fallenkarten. Nutze den mehrfach aktivierbaren Ignition-Effekt von Brionac, um das Feld zu säubern und direkt anzugreifen!",
        "objective": "Wirf Handkarten ab, um alle 3 Karten des Gegners auf die Hand zu schicken und füge 2300 Schaden zu!",
        "hint": "Brionac hat keinen Once-Per-Turn im Edison-Format! Jeder Abwurf schickt eine gegnerische Karte auf die Hand zurück.",
        "p0_lp": 4000,
        "p1_lp": 2300,
        "p0_hand": [15341821, 33420078, 57421866], # Dandylion, Plaguespreader, Level Eater
        "p0_mzone": [
            {"code": 50321796, "seq": 2, "pos": 1} # Brionac faceup attack
        ],
        "p0_szone": [],
        "p0_grave": [],
        "p0_extra": [7391448, 44508094],
        "p0_deck": [63977008, 9748752, 19613556],
        "p1_hand": [],
        "p1_mzone": [
            {"code": 23693634, "seq": 2, "pos": 1} # Colossal Fighter
        ],
        "p1_szone": [
            {"code": 44095762, "seq": 1, "pos": 8}, # Mirror Force facedown
            {"code": 70342110, "seq": 3, "pos": 8}, # Dimensional Prison facedown
        ],
        "p1_grave": [],
        "p1_extra": [],
        "p1_deck": [29401950, 41420027],
    },
    "lonefire_spore_synchro": {
        "id": "lonefire_spore_synchro",
        "title": "Taktik 6: Plant Engine Synchro-Explosion",
        "category": "Deck-Spezialbeschwörung & Stufen-Modifikation",
        "difficulty": "Mittel",
        "description": "Nutze Einzelfeuerblüte (Lonefire Blossom), um dein Pflanzendeck zu durchsuchen, rufe Löwenzahn-Löwe oder Spore und beschwöre Schwarzer Rosendrache!",
        "objective": "Aktiviere den Effekt von Einzelfeuerblüte, beschwöre Spore aus dem Deck und führe eine Synchrobeschwörung durch.",
        "hint": "Einzelfeuerblüte kann sich selbst als Kosten als Tribut anbieten, um ein beliebiges Pflanzenmonster direkt aus dem Deck spezialzubeschwören.",
        "p0_lp": 4000,
        "p1_lp": 4000,
        "p0_hand": [48686504, 81439173, 14087893], # Lonefire Blossom, Foolish Burial, Book of Moon
        "p0_mzone": [],
        "p0_szone": [],
        "p0_grave": [],
        "p0_extra": [73580471, 7391448, 44508094, 3429238], # Black Rose, Goyo, Stardust, Drill
        "p0_deck": [11747708, 15341821, 48686504, 26202165, 63977008], # Spore, Dandylion, Lonefire, Sangan, Junk Synchron
        "p1_hand": [],
        "p1_mzone": [
            {"code": 44508094, "seq": 2, "pos": 1}
        ],
        "p1_szone": [
            {"code": 44095762, "seq": 2, "pos": 8}
        ],
        "p1_grave": [],
        "p1_extra": [],
        "p1_deck": [29401950, 41420027],
    },
    "blackwing_vayu": {
        "id": "blackwing_vayu",
        "title": "Taktik 7: Blackwing Vayu Friedhofs-Synchro",
        "category": "Friedhofs-Beschwörung & Tuner-Regeln",
        "difficulty": "Mittel",
        "description": "Vayu the Emblem of Honor kann auf dem Feld nicht als Synchromaterial genutzt werden – aber aus dem Friedhof heraus! Verbanne Vayu und Sirocco aus dem Friedhof, um Schwarzflügel-Rüstungsmeister zu rufen!",
        "objective": "Aktiviere Vayus Friedhofseffekt, verbanne Sirocco und beschwöre Schwarzflügel-Waffenflügel oder Rüstungsmeister ohne Monster auf dem Feld.",
        "hint": "Vayus Effekt ist ein Zündeffekt (Ignition) im Friedhof. Die Beschwörung zählt als Spezialbeschwörung, nicht als reguläre Synchrobeschwörung (die Effekte des gerufenen Synchromonsters werden annulliert).",
        "p0_lp": 3500,
        "p1_lp": 2800,
        "p0_hand": [22835145, 14087893], # Blizzard, Book of Moon
        "p0_mzone": [],
        "p0_szone": [],
        "p0_grave": [72714392, 75498415], # Vayu (Lv1), Sirocco (Lv5)
        "p0_extra": [76913983, 69031175], # Armed Wing (Lv6), Armor Master (Lv7)
        "p0_deck": [58820853, 49003716, 44095762], # Shura, Bora, Mirror Force
        "p1_hand": [],
        "p1_mzone": [
            {"code": 44508094, "seq": 2, "pos": 1} # Stardust Dragon
        ],
        "p1_szone": [
            {"code": 70342110, "seq": 2, "pos": 8} # Dimensional Prison facedown
        ],
        "p1_grave": [],
        "p1_extra": [],
        "p1_deck": [29401950, 41420027],
    },
    "machina_fortress": {
        "id": "machina_fortress",
        "title": "Taktik 8: Machina Fortress Loop & Beatdown",
        "category": "Kosten-Kombinatorik & Selbst-Wiederbelebung",
        "difficulty": "Einfach",
        "description": "Machina Fortress kann von der Hand oder dem Friedhof spezialbeschworen werden, indem Maschinen-Monster abgeworfen werden, deren Gesamtstufe 8 oder mehr beträgt.",
        "objective": "Beschwöre Machina Fortress aus der Hand oder dem Friedhof und überrenne das gegnerische Monster!",
        "hint": "Machina Fortress (Stufe 7) kann sich selbst zusammen mit einem anderen Maschinenmonster (z.B. Machina Gearframe Stufe 4 = Stufe 11) abwerfen, um sich sofort selbst zu beschwören!",
        "p0_lp": 4000,
        "p1_lp": 2500,
        "p0_hand": [5556499, 42940404, 78349103], # Fortress (Lv7), Gearframe (Lv4), Peacekeeper (Lv2)
        "p0_mzone": [],
        "p0_szone": [],
        "p0_grave": [5556499], # Fortress in Grave
        "p0_extra": [],
        "p0_deck": [42940404, 78349103, 14087893],
        "p1_hand": [],
        "p1_mzone": [
            {"code": 7391448, "seq": 2, "pos": 1} # Goyo Guardian (2800 ATK)
        ],
        "p1_szone": [],
        "p1_grave": [],
        "p1_extra": [],
        "p1_deck": [44095762, 70342110],
    },
    "diva_hero": {
        "id": "diva_hero",
        "title": "Taktik 9: Diva HERO Miracle Fusion & Absolute Zero",
        "category": "Deck-Beschwörung & Fusions-Climb",
        "difficulty": "Experte",
        "description": "Kombiniere die Tiefsee-Taucherin mit der HERO-Engine: Beschwöre Taucherin -> rufe Stachelkiemen-Kämpfer -> führe eine Synchrobeschwörung aus -> aktiviere Wunderfusion aus dem Friedhof für Elementar-HELD Absoluter Nullpunkt!",
        "objective": "Beschwöre Catastor via Synchro und aktiviere Wunderfusion für Absoluter Nullpunkt, um das Feld zu dominieren!",
        "hint": "Tiefsee-Taucherin (Stufe 2 Empfänger) holt Stachelkiemen-Kämpfer (Stufe 3) aus dem Deck = Stufe 5 Synchro (Catastor). Wunderfusion nutzt dann Taucherin (WASSER) + Stratos (HERO) im Friedhof!",
        "p0_lp": 4000,
        "p1_lp": 3000,
        "p0_hand": [78868119, 45906428], # Deep Sea Diva, Miracle Fusion
        "p0_mzone": [],
        "p0_szone": [],
        "p0_grave": [40044918], # Elemental HERO Stratos
        "p0_extra": [26593852, 40854197, 7391448, 50321796], # Catastor (Lv5), Absolute Zero (Fusion), Goyo, Brionac
        "p0_deck": [42463414, 40044918, 14087893], # Spined Gillman, Stratos, Book of Moon
        "p1_hand": [],
        "p1_mzone": [
            {"code": 23693634, "seq": 2, "pos": 1} # Colossal Fighter (2800 ATK)
        ],
        "p1_szone": [
            {"code": 44095762, "seq": 2, "pos": 8} # Mirror Force facedown
        ],
        "p1_grave": [],
        "p1_extra": [],
        "p1_deck": [29401950, 41420027],
    }
}

import os
import json
import uuid

SCENARIOS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "user_scenarios.json")

def load_persisted_scenarios():
    """Lädt persistierte Benutzer-Szenarien aus JSON-Datei."""
    if os.path.exists(SCENARIOS_FILE):
        try:
            with open(SCENARIOS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    SCENARIOS.update(data)
                    print(f"[SCENARIOS]: {len(data)} persistierte Szenarien erfolgreich geladen.")
        except Exception as e:
            print(f"[SCENARIOS_ERROR]: Fehler beim Laden von user_scenarios.json: {e}")

def save_persisted_scenarios():
    """Speichert dynamisch erstellte Szenarien persistent auf Festplatte."""
    try:
        os.makedirs(os.path.dirname(SCENARIOS_FILE), exist_ok=True)
        # Speichere alle Szenarien (oder Custom Checkpoints)
        custom_scenarios = {k: v for k, v in SCENARIOS.items() if k not in [
            "quickdraw_synchro", "caius_chain", "heavy_storm_negation",
            "pot_of_avarice_loop", "brionac_bounce", "lonefire_spore_synchro",
            "blackwing_vayu", "machina_fortress", "diva_hero"
        ]}
        with open(SCENARIOS_FILE, "w", encoding="utf-8") as f:
            json.dump(custom_scenarios, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[SCENARIOS_ERROR]: Fehler beim Speichern von user_scenarios.json: {e}")

# Initialisiere Persistenz beim Laden des Moduls
load_persisted_scenarios()

def board_to_scenario(board: Dict[str, Any], title: str = "Replay Checkpoint", description: str = "") -> Dict[str, Any]:
    """Konvertiert einen Board-State (z.B. aus einem Replay-Frame oder Live-Spiel) in ein spielbares Szenario."""
    p0 = board.get("0") or board.get(0) or {}
    p1 = board.get("1") or board.get(1) or {}
    
    def extract_codes(cards):
        if not cards: return []
        res = []
        for c in cards:
            if isinstance(c, dict) and c.get("code") and c["code"] > 0:
                res.append(c["code"])
            elif isinstance(c, (int, str)):
                try: res.append(int(c))
                except: pass
        return res
    
    def extract_zones(cards):
        if not cards: return []
        res = []
        for seq, c in enumerate(cards):
            if isinstance(c, dict) and c.get("code") and c["code"] > 0:
                pos = c.get("position", 1)
                res.append({"code": c["code"], "seq": seq, "pos": pos})
        return res

    sc_id = f"fork_{uuid.uuid4().hex[:8]}"
    scenario = {
        "id": sc_id,
        "title": title or f"Checkpoint: {sc_id}",
        "category": "Replay Checkpoint",
        "difficulty": "Mittel",
        "description": description or "Aus einem Replay-Frame oder Duell-Zustand erzeugter Checkpoint.",
        "objective": "Gewinne das Duell ab diesem Zug oder finde eine bessere Spiellinie!",
        "hint": "Du übernimmst genau diesen Zustand mit allen Handkarten, Monstern, Fallen und Friedhöfen.",
        "p0_lp": int(p0.get("lp", 8000)),
        "p1_lp": int(p1.get("lp", 8000)),
        "p0_hand": extract_codes(p0.get("hand", [])),
        "p0_mzone": extract_zones(p0.get("mzone", [])),
        "p0_szone": extract_zones(p0.get("szone", [])),
        "p0_grave": extract_codes(p0.get("grave", [])),
        "p0_extra": extract_codes(p0.get("extra", [])),
        "p0_deck": extract_codes(p0.get("deck", [])) or [63977008, 14087893, 44095762, 19613556, 29401950, 41420027, 9748752, 70095154, 26202165, 53582587, 97077563, 70342110, 97919147, 34773082, 94145021],
        "p1_hand": extract_codes(p1.get("hand", [])),
        "p1_mzone": extract_zones(p1.get("mzone", [])),
        "p1_szone": extract_zones(p1.get("szone", [])),
        "p1_grave": extract_codes(p1.get("grave", [])),
        "p1_extra": extract_codes(p1.get("extra", [])),
        "p1_deck": extract_codes(p1.get("deck", [])) or [29401950, 41420027, 14087893, 63977008, 44095762, 53582587, 9748752, 70095154, 26202165, 70342110, 97919147, 34773082, 94145021, 19613556, 97077563],
    }
    SCENARIOS[sc_id] = scenario
    save_persisted_scenarios()
    return scenario

def delete_scenario(scenario_id: str) -> bool:
    """Löscht ein benutzerdefiniertes Szenario."""
    if scenario_id in SCENARIOS:
        del SCENARIOS[scenario_id]
        save_persisted_scenarios()
        return True
    return False


def get_scenario_metadata_list() -> List[Dict[str, Any]]:
    """Gibt eine Liste aller Szenarien für die Benutzeroberfläche zurück."""
    out = []
    for sc_id, sc in SCENARIOS.items():
        out.append({
            "id": sc["id"],
            "title": sc["title"],
            "category": sc.get("category", "Taktik"),
            "difficulty": sc.get("difficulty", "Mittel"),
            "description": sc.get("description", ""),
            "objective": sc.get("objective", ""),
            "hint": sc.get("hint", ""),
            "p0_lp": sc.get("p0_lp", 8000),
            "p1_lp": sc.get("p1_lp", 8000),
        })
    return out


def setup_scenario_board(ocgcore, pduel, scenario: Dict[str, Any]):
    """
    Richtet das libocgcore Spielfeld für ein bestimmtes Szenario ein.
    Setzt LP, platziert Karten direkt in Hand, MZONE, SZONE, Graveyard, Extra Deck und Deck.
    """
    p0_lp = int(scenario.get("p0_lp", 8000))
    p1_lp = int(scenario.get("p1_lp", 8000))

    # Set player info: start_hand = 0 so libocgcore does not overwrite custom hand by auto-drawing from deck
    ocgcore.set_player_info(pduel, 0, p0_lp, 0, 1)
    ocgcore.set_player_info(pduel, 1, p1_lp, 0, 1)

    # 1. Deck Cards (LOCATION_DECK = 0x01)
    for i, cid in enumerate(scenario.get("p0_deck", [])):
        ocgcore.new_card(pduel, int(cid), 0, 0, 1, i, 8)
    for i, cid in enumerate(scenario.get("p1_deck", [])):
        ocgcore.new_card(pduel, int(cid), 1, 1, 1, i, 8)

    # 2. Extra Deck Cards (LOCATION_EXTRA = 0x40)
    for i, cid in enumerate(scenario.get("p0_extra", [])):
        ocgcore.new_card(pduel, int(cid), 0, 0, 64, i, 8)
    for i, cid in enumerate(scenario.get("p1_extra", [])):
        ocgcore.new_card(pduel, int(cid), 1, 1, 64, i, 8)

    # 3. Graveyard Cards (LOCATION_GRAVE = 0x10)
    for i, cid in enumerate(scenario.get("p0_grave", [])):
        ocgcore.new_card(pduel, int(cid), 0, 0, 16, i, 1)
    for i, cid in enumerate(scenario.get("p1_grave", [])):
        ocgcore.new_card(pduel, int(cid), 1, 1, 16, i, 1)

    # 4. Hand Cards (LOCATION_HAND = 0x02)
    for i, cid in enumerate(scenario.get("p0_hand", [])):
        ocgcore.new_card(pduel, int(cid), 0, 0, 2, i, 8)
    for i, cid in enumerate(scenario.get("p1_hand", [])):
        ocgcore.new_card(pduel, int(cid), 1, 1, 2, i, 8)

    # 5. Monster Zone (LOCATION_MZONE = 0x04)
    for item in scenario.get("p0_mzone", []):
        cid = item["code"]
        seq = item.get("seq", 2)
        pos = item.get("pos", 1)  # 1 = face-up ATK, 4 = face-up DEF, 8 = face-down DEF
        ocgcore.new_card(pduel, int(cid), 0, 0, 4, seq, pos)
    for item in scenario.get("p1_mzone", []):
        cid = item["code"]
        seq = item.get("seq", 2)
        pos = item.get("pos", 1)
        ocgcore.new_card(pduel, int(cid), 1, 1, 4, seq, pos)

    # 6. Spell/Trap Zone (LOCATION_SZONE = 0x08)
    for item in scenario.get("p0_szone", []):
        cid = item["code"]
        seq = item.get("seq", 2)
        pos = item.get("pos", 8)  # 1 = face-up, 8 = face-down
        ocgcore.new_card(pduel, int(cid), 0, 0, 8, seq, pos)
    for item in scenario.get("p1_szone", []):
        cid = item["code"]
        seq = item.get("seq", 2)
        pos = item.get("pos", 8)
        ocgcore.new_card(pduel, int(cid), 1, 1, 8, seq, pos)
