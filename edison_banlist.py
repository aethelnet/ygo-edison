"""
Official Edison Format Banlist Validator (March 1, 2010 - SJC Edison cutoff)
Provides definitive Forbidden (0), Limited (1), and Semi-Limited (2) card checks.
"""

from typing import List, Dict, Any, Tuple, Optional

# Forbidden (0 copies allowed) in Edison Format (March/April 2010)
FORBIDDEN_IDS: Dict[int, str] = {
    # Monsters
    72989439: "Black Luster Soldier - Envoy of the Beginning",
    82301904: "Chaos Emperor Dragon - Envoy of the End",
    34124316: "Cyber Jar",
    69015963: "Cyber-Stein",
    40737112: "Dark Magician of Chaos",
    56570271: "Destiny HERO - Disk Commander",
    78706415: "Fiber Jar",
    34206604: "Magical Scientist",
    31560081: "Magician of Faith",
    21593977: "Makyura the Destructor",
    8131171: "Sinister Serpent",
    33184167: "Tribe-Infecting Virus",
    34853266: "Tsukuyomi",
    44910027: "Victory Dragon",
    78010363: "Witch of the Black Forest",
    3078576: "Yata-Garasu",

    # Spells
    69243953: "Butterfly Dagger - Elma",
    57953380: "Card of Safe Return",
    4031928: "Change of Heart",
    17375316: "Confiscation",
    53129443: "Dark Hole", # Banned in Edison
    44763025: "Delinquent Duo",
    23557835: "Dimension Fusion",
    79571449: "Graceful Charity",
    18144506: "Harpie's Feather Duster",
    85602018: "Last Will",
    46411259: "Metamorphosis",
    41482598: "Mirage of Nightmare",
    83764718: "Monster Reborn", # Banned in Edison
    74191942: "Painful Choice",
    55144522: "Pot of Greed",
    70828912: "Premature Burial",
    12580477: "Raigeki",
    45986603: "Snatch Steal",
    42829885: "The Forceful Sentry",

    # Traps
    57728570: "Crush Card Virus",
    61740673: "Imperial Order",
    28566710: "Last Turn",
    83555666: "Ring of Destruction",
    3280747: "Sixth Sense",
    35316708: "Time Seal",
}

# Limited (max 1 copy allowed) in Edison Format
LIMITED_IDS: Dict[int, str] = {
    # Monsters
    2009101: "Blackwing - Gale the Whirlwind",
    50321796: "Brionac, Dragon of the Ice Barrier",
    85087012: "Card Trooper",
    65192027: "Dark Armed Dragon",
    40044918: "Elemental HERO Stratos",
    33396948: "Exodia the Forbidden One",
    7902349: "Left Arm of the Forbidden One",
    44519536: "Left Leg of the Forbidden One",
    70903634: "Right Arm of the Forbidden One",
    8124921: "Right Leg of the Forbidden One",
    41470137: "Gladiator Beast Bestiari",
    44330098: "Gorz the Emissary of Darkness",
    7391448: "Goyo Guardian",
    95503687: "Lumina, Lightsworn Summoner",
    31305911: "Marshmallon",
    92826944: "Mezuki",
    33508719: "Morphing Jar",
    28297833: "Necroface",
    80344569: "Neo-Spacian Grand Mole",
    16226786: "Night Assailant",
    33420078: "Plaguespreader Zombie",
    14878871: "Rescue Cat",
    26202165: "Sangan",
    84290642: "Snipe Hunter",
    423585: "Summoner Monk",
    98777036: "Tragoedia",

    # Spells
    46052429: "Advanced Ritual Art",
    1475311: "Allure of Darkness",
    87910978: "Brain Control",
    48976825: "Burial from a Different Dimension",
    72892473: "Card Destruction",
    94886282: "Charge of the Light Brigade",
    60682203: "Cold Wave",
    45809008: "Destiny Draw",
    67723438: "Emergency Teleport",
    81439173: "Foolish Burial",
    77565204: "Future Fusion",
    42703248: "Giant Trunade",
    19613556: "Heavy Storm",
    3136426: "Level Limit - Area B",
    23171610: "Limiter Removal",
    22046459: "Megamorph",
    37520316: "Mind Control",
    43040603: "Monster Gate",
    5318639: "Mystical Space Typhoon",
    2295440: "One for One",
    3659803: "Overload Fusion",
    67169062: "Pot of Avarice",
    58577036: "Reasoning",
    32807846: "Reinforcement of the Army",
    73915051: "Scapegoat",
    72302403: "Swords of Revealing Light",

    # Traps
    97077563: "Call of the Haunted",
    36468556: "Ceasefire",
    85742772: "Gravity Bind",
    62279055: "Magic Cylinder",
    15800838: "Mind Crush",
    44095762: "Mirror Force",
    29843091: "Ojama Trio",
    27174286: "Return from the Different Dimension",
    41420027: "Solemn Judgment",
    23205979: "Spirit Reaper",
    53582587: "Torrential Tribute",
    64697231: "Trap Dustshoot",
    17078030: "Wall of Revealing Light",
}

# Semi-Limited (max 2 copies allowed) in Edison Format
SEMI_LIMITED_IDS: Dict[int, str] = {
    # Monsters
    9596126: "Chaos Sorcerer",
    70095154: "Cyber Dragon",
    15341821: "Dandylion",
    9411399: "Destiny HERO - Malicious",
    63665875: "Goblin Zombie",
    37742478: "Honest",
    57774843: "Judgment Dragon",
    48686504: "Lonefire Blossom",
    4906301: "Necro Gardna",
    12538374: "Treeborn Frog",

    # Spells
    91351370: "Black Whirlwind",
    91623717: "Chain Strike",
    75500286: "Gold Sarcophagus",
    98494543: "Magical Stone Excavation",
    83746708: "Mage Power",
    85852291: "Magical Mallet",
    56747793: "United We Stand",

    # Traps
    29401950: "Bottomless Trap Hole",
    51452091: "Royal Decree",
    82732705: "Skill Drain",
}

def get_card_limit(card_id: int) -> Tuple[int, str, Optional[str]]:
    """
    Returns (limit, status_name, card_name)
    limit: 0 (forbidden), 1 (limited), 2 (semi-limited), 3 (unlimited)
    """
    if card_id in FORBIDDEN_IDS:
        return 0, "FORBIDDEN", FORBIDDEN_IDS[card_id]
    if card_id in LIMITED_IDS:
        return 1, "LIMITED", LIMITED_IDS[card_id]
    if card_id in SEMI_LIMITED_IDS:
        return 2, "SEMI_LIMITED", SEMI_LIMITED_IDS[card_id]
    return 3, "UNLIMITED", None

def check_edison_legality(main_ids: List[int], extra_ids: List[int] = None, side_ids: List[int] = None, card_names: Dict[int, str] = None) -> Dict[str, Any]:
    """
    Validates a Yu-Gi-Oh deck against the official March/April 2010 Edison banlist.
    Checks main/extra/side counts and copies across all three piles.
    """
    extra_ids = extra_ids or []
    side_ids = side_ids or []
    card_names = card_names or {}
    
    violations = []
    
    # 1. Deck Size Restrictions
    main_len = len(main_ids)
    extra_len = len(extra_ids)
    side_len = len(side_ids)
    
    if main_len < 40:
        violations.append({
            "type": "DECK_SIZE_TOO_SMALL",
            "message": f"Main Deck contains only {main_len} cards (minimum 40 required for Edison)."
        })
    elif main_len > 60:
        violations.append({
            "type": "DECK_SIZE_TOO_LARGE",
            "message": f"Main Deck contains {main_len} cards (maximum 60 allowed)."
        })
        
    if extra_len > 15:
        violations.append({
            "type": "EXTRA_DECK_TOO_LARGE",
            "message": f"Extra Deck contains {extra_len} cards (maximum 15 allowed)."
        })
        
    if side_len > 15:
        violations.append({
            "type": "SIDE_DECK_TOO_LARGE",
            "message": f"Side Deck contains {side_len} cards (maximum 15 allowed)."
        })
        
    # 2. Count Total Copies per Card across Main + Extra + Side
    counts: Dict[int, int] = {}
    for cid in main_ids + extra_ids + side_ids:
        if cid > 0:
            counts[cid] = counts.get(cid, 0) + 1
            
    for cid, cnt in counts.items():
        limit, status, known_name = get_card_limit(cid)
        cname = card_names.get(cid) or known_name or f"Card {cid}"
        
        if limit == 0 and cnt > 0:
            violations.append({
                "type": "FORBIDDEN_CARD",
                "card_id": cid,
                "card_name": cname,
                "count": cnt,
                "max_allowed": 0,
                "message": f"[VERBOTEN] '{cname}' ist in Edison verboten ({cnt} im Deck)."
            })
        elif limit == 1 and cnt > 1:
            violations.append({
                "type": "LIMITED_CARD",
                "card_id": cid,
                "card_name": cname,
                "count": cnt,
                "max_allowed": 1,
                "message": f"[LIMITIERT] '{cname}' ist auf 1 limitiert ({cnt} im Deck)."
            })
        elif limit == 2 and cnt > 2:
            violations.append({
                "type": "SEMI_LIMITED_CARD",
                "card_id": cid,
                "card_name": cname,
                "count": cnt,
                "max_allowed": 2,
                "message": f"[SEMI-LIMITIERT] '{cname}' ist auf 2 limitiert ({cnt} im Deck)."
            })
        elif cnt > 3:
            violations.append({
                "type": "MAX_COPIES_EXCEEDED",
                "card_id": cid,
                "card_name": cname,
                "count": cnt,
                "max_allowed": 3,
                "message": f"[MAX 3] '{cname}' darf max. 3-mal gespielt werden ({cnt} im Deck)."
            })
            
    is_legal = len(violations) == 0
    if is_legal:
        status_text = "EDISON LEGAL (April 2010)"
    else:
        status_text = f"ILLEGAL ({len(violations)} Regelverstösse)"
        
    return {
        "is_legal": is_legal,
        "format": "Edison (April 2010)",
        "main_count": main_len,
        "extra_count": extra_len,
        "side_count": side_len,
        "violations": violations,
        "status_text": status_text
    }
