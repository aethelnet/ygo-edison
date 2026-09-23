import os
import sys
import ctypes
import struct
import torch
import torch.nn as nn
from ygo_sqlite_bridge import YGOSqliteBridge
from ygo_byte_parser import YGOByteParser

# ANSI Color Codes
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
BLUE = "\033[94m"
BOLD = "\033[1m"
DIM = "\033[2m"
ITALIC = "\033[3m"
RESET = "\033[0m"

core_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "ygopro-core/bin/Release/libocgcore.so"))
script_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "script"))

ocgcore = ctypes.cdll.LoadLibrary(core_path)

class CardData(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_uint32),
        ("alias", ctypes.c_uint32),
        ("setcode", ctypes.c_uint16 * 16),
        ("type", ctypes.c_uint32),
        ("level", ctypes.c_uint32),
        ("attribute", ctypes.c_uint32),
        ("race", ctypes.c_uint32),
        ("attack", ctypes.c_int32),
        ("defense", ctypes.c_int32),
        ("lscale", ctypes.c_uint32),
        ("rscale", ctypes.c_uint32),
        ("link_marker", ctypes.c_uint32),
        ("rule_code", ctypes.c_uint32)
    ]

bridge = YGOSqliteBridge()
_buffers = []

def script_reader_cb(name_bytes, plen):
    if not name_bytes: plen[0] = 0; return 0
    name_str = ctypes.string_at(name_bytes).decode('utf-8', errors='ignore')
    base_name = os.path.basename(name_str)
    file_path = os.path.join(script_dir, base_name)
    if os.path.exists(file_path):
        with open(file_path, 'rb') as f: content = f.read()
        c_buf = (ctypes.c_char * len(content)).from_buffer_copy(content)
        _buffers.append(c_buf)
        plen[0] = len(content)
        return ctypes.cast(c_buf, ctypes.c_void_p).value
    plen[0] = 0; return 0

def card_reader_cb(code, pdata):
    pdata.contents.code = code
    info = bridge.card_metadata.get(code)
    if info:
        pdata.contents.type = info.get("type", 17)
        pdata.contents.attack = max(0, info.get("atk", 0))
        pdata.contents.defense = max(0, info.get("def", 0))
        pdata.contents.level = max(1, min(12, info.get("level", 4)))
        pdata.contents.race = info.get("race", 1)
        pdata.contents.attribute = info.get("attribute", 1)
    else:
        pdata.contents.type = 17; pdata.contents.attack = 1500; pdata.contents.defense = 1500; pdata.contents.level = 4
    return code

cb_card = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(CardData))(card_reader_cb)
cb_script = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int32))(script_reader_cb)
cb_msg = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32)(lambda p, m: 0)

ocgcore.set_card_reader(cb_card)
ocgcore.set_script_reader(cb_script)
ocgcore.set_message_handler(cb_msg)

ocgcore.create_duel.restype = ctypes.c_void_p
ocgcore.new_card.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint8]
ocgcore.set_player_info.argtypes = [ctypes.c_void_p, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32]
ocgcore.start_duel.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
ocgcore.process.argtypes = [ctypes.c_void_p]
ocgcore.process.restype = ctypes.c_uint32
ocgcore.get_message.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
ocgcore.get_message.restype = ctypes.c_int32
ocgcore.set_responsei.argtypes = [ctypes.c_void_p, ctypes.c_int32]
ocgcore.set_responseb.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

# --- ALPHAZERO GRANDMASTER BRAIN ---
class YGOAlphaZeroBrain(nn.Module):
    def __init__(self, state_dim=16, action_dim=32, latent_dim=32):
        super().__init__()
        self.trunk = nn.Sequential(
            nn.Linear(state_dim, 128),
            nn.LayerNorm(128),
            nn.GELU(),
            nn.Linear(128, latent_dim),
            nn.LayerNorm(latent_dim),
            nn.GELU()
        )
        self.policy_head = nn.Sequential(
            nn.Linear(latent_dim, 64),
            nn.ReLU(),
            nn.Linear(64, action_dim)
        )
        self.value_head = nn.Sequential(
            nn.Linear(latent_dim, 64),
            nn.ReLU(),
            nn.Linear(64, 1)
        )

    def forward(self, state_tensor):
        latent = self.trunk(state_tensor)
        logits = self.policy_head(latent)
        value = self.value_head(latent)
        return logits, value, latent

    def act(self, state_tensor, num_actions):
        logits, value, latent = self.forward(state_tensor)
        mask = torch.full((32,), float('-inf'))
        mask[:num_actions] = 0
        probs = torch.softmax(logits + mask, dim=-1)
        action = torch.argmax(probs[:num_actions]).item()
        return action

def get_beatdown_deck():
    pool = [4148264, 5464695, 7359741, 7459013, 11091375, 14898066, 69247929, 69455834]
    return (pool * 5)[:40]

def card_name(cid):
    if cid == 0:
        return f"{RED}{BOLD}DIREKTER ANGRIFF{RESET}"
    info = bridge.card_metadata.get(cid, {})
    name = info.get("name", f"Card #{cid}")
    atk = info.get("atk", 0)
    return f"{BOLD}{name}{RESET} (ATK: {GREEN}{atk}{RESET})"

def format_card_detailed(cid):
    if cid == 0:
        return f"{RED}{BOLD}💥 DIREKTER ANGRIFF auf die gegnerischen Lebenspunkte!{RESET}"
    info = bridge.card_metadata.get(cid, {})
    name = info.get("name", f"Card #{cid}")
    atk = info.get("atk", 0)
    def_val = info.get("def", 0)
    level = info.get("level", 4)
    ctype = info.get("type", 17)
    desc = info.get("desc", "").replace("\r\n", " ").replace("\n", " ").strip()
    
    if ctype & 0x2:
        tag = f"{MAGENTA}[Zauberkarte]{RESET}"
        stats = ""
    elif ctype & 0x4:
        tag = f"{MAGENTA}[Fallenkarte]{RESET}"
        stats = ""
    else:
        tag = f"{YELLOW}[★{level} Monster]{RESET}"
        stats = f"| ATK: {GREEN}{atk}{RESET} / DEF: {CYAN}{def_val}{RESET}"
        
    desc_clean = f"\n        {DIM}💬 \"{desc[:85]}...\"{RESET}" if len(desc) > 85 else (f"\n        {DIM}💬 \"{desc}\"{RESET}" if desc else "")
    return f"{BOLD}{name}{RESET} {tag} {stats}{desc_clean}"

def render_lp_bar(label, lp, max_lp=8000, color=CYAN):
    ratio = max(0.0, min(1.0, float(lp) / max_lp))
    bar_len = 22
    filled = int(ratio * bar_len)
    empty = bar_len - filled
    
    if ratio > 0.5: bar_color = GREEN
    elif ratio > 0.25: bar_color = YELLOW
    else: bar_color = RED
        
    bar = f"{bar_color}{'█' * filled}{DIM}{'░' * empty}{RESET}"
    return f"{color}{label: <10}{RESET} [{bar}] {BOLD}{lp: >4}{RESET} / {max_lp} LP"

def render_battlefield(lp, turn_num, current_player, ai_field, human_field):
    pname = f"{MAGENTA}🤖 KI (Player 0){RESET}" if current_player == 0 else f"{CYAN}👦 STEFAN (Player 1){RESET}"
    
    print("\n" + "╔" + "═" * 74 + "╗")
    print(f"║ {BOLD}⚡ AURATIC PRIME // CYBER-ARENA HUD ⚡{RESET}       Runde {turn_num: <3} | Zug: {pname} ║")
    print("╠" + "═" * 74 + "╣")
    print(f"║ {render_lp_bar('🤖 KI', lp[0], color=MAGENTA)}                               ║")
    print(f"║ {render_lp_bar('👦 STEFAN', lp[1], color=CYAN)}                               ║")
    print("╠" + "═" * 74 + "╣")
    
    ai_monsters = [card_name(c) for c in ai_field.get("monsters", [])]
    ai_str = " | ".join(ai_monsters) if ai_monsters else f"{DIM}(Keine Monster auf dem Feld){RESET}"
    print(f"║ {MAGENTA}🤖 KI FELD:{RESET} {ai_str}")
    print("║" + "─" * 74 + "║")
    
    human_monsters = [card_name(c) for c in human_field.get("monsters", [])]
    human_str = " | ".join(human_monsters) if human_monsters else f"{DIM}(Keine Monster auf dem Feld){RESET}"
    print(f"║ {CYAN}👦 STEFAN FELD:{RESET} {human_str}")
    print("╚" + "═" * 74 + "╝\n")

def format_action_desc(act, detailed=False):
    atype = act.get("type", "UNKNOWN")
    cid = act.get("card_id", 0)
    card_str = format_card_detailed(cid) if detailed and cid else card_name(cid)
    
    if atype == "SUMMON":
        return f"{GREEN}🃏 Monster BESCHWÖREN:{RESET} {card_str}"
    elif atype == "SPSUMMON":
        return f"{YELLOW}✨ SPEZIALBESCHWÖREN:{RESET} {card_str}"
    elif atype == "MSET":
        return f"{DIM}🛡️ Monster VERDECKT SETZEN:{RESET} {card_str}"
    elif atype == "SSET":
        return f"{CYAN}📜 Zauber/Falle SETZEN:{RESET} {card_str}"
    elif atype == "REPOS":
        return f"{CYAN}🔄 Position ÄNDERN (ATK <-> DEF):{RESET} {card_str}"
    elif atype == "ACTIVATE":
        return f"{YELLOW}⚡ EFFEKT AKTIVIEREN:{RESET} {card_str}"
    elif atype == "TO_BATTLE_PHASE":
        return f"{RED}⚔️ In die BATTLE PHASE wechseln (Angreifen){RESET}"
    elif atype == "TO_END_PHASE":
        return f"{DIM}🛑 ZUG BEENDEN (End Phase){RESET}"
    elif atype == "ATTACK":
        return f"{RED}💥 Mit Monster ANGREIFEN:{RESET} {card_str}"
    elif atype == "TARGET_CARD":
        return f"{YELLOW}🎯 Ziel-Monster WÄHLEN:{RESET} {card_str}"
    elif atype == "DIRECT_ATTACK":
        return f"{RED}{BOLD}💥 DIREKTER ANGRIFF auf die Lebenspunkte des Gegners!{RESET}"
    elif atype == "TO_MAIN_PHASE_2":
        return f"{CYAN}🔄 Zu Main Phase 2 wechseln{RESET}"
    elif atype == "CANCEL_CHAIN":
        return f"{DIM}🚫 PASSEN / KETTE ÜBERSPRINGEN (Keine Reaktion){RESET}"
    elif atype == "POS_FACEUP_ATTACK":
        return f"{GREEN}⚔️ Offene Angriffsposition (ATK Mode){RESET}"
    elif atype == "POS_FACEUP_DEFENSE":
        return f"{CYAN}🛡️ Offene Verteidigungsposition (DEF Mode){RESET}"
    else:
        return f"👉 {atype} {card_str}"

def action_to_response_code(act, choice_idx, mtype):
    atype = act.get("type", "")
    sub_idx = act.get("sub_idx", choice_idx)
    
    if mtype == 11: # MSG_SELECT_IDLECMD
        if atype == "SUMMON":
            return (sub_idx << 16) | 0
        elif atype == "SPSUMMON":
            return (sub_idx << 16) | 1
        elif atype == "REPOS":
            return (sub_idx << 16) | 2
        elif atype == "MSET":
            return (sub_idx << 16) | 3
        elif atype == "SSET":
            return (sub_idx << 16) | 4
        elif atype == "ACTIVATE":
            return (sub_idx << 16) | 5
        elif atype == "TO_BATTLE_PHASE":
            return 6
        elif atype == "TO_END_PHASE":
            return 7
        else:
            return 7
    elif mtype == 10: # MSG_SELECT_BATTLECMD
        if atype == "ATTACK":
            return (sub_idx << 16) | 1
        elif atype == "ACTIVATE":
            return (sub_idx << 16) | 0
        elif atype == "TO_MAIN_PHASE_2":
            return 2
        elif atype == "TO_END_PHASE":
            return 3
        else:
            return 3
    elif atype == "CANCEL_CHAIN":
        return -1
    elif atype == "POS_FACEUP_ATTACK":
        return 1
    elif atype == "POS_FACEUP_DEFENSE":
        return 4
    else:
        return choice_idx

def run_interactive_arena():
    print(f"\n{BOLD}==================================================================={RESET}")
    print(f"{CYAN}⚔️  AURATIC PRIME: MENSCH VS. NEURONALES NETZ (CYBER ARENA) ⚔️{RESET}")
    print(f"{BOLD}==================================================================={RESET}")
    
    brain = YGOAlphaZeroBrain()
    if os.path.exists("lgnn_grandmaster.pth"):
        try:
            brain.load_state_dict(torch.load("lgnn_grandmaster.pth", map_location="cpu", weights_only=True))
            brain.eval()
            print(f"{GREEN}🧠 PyTorch Grandmaster KI aktiv! (AlphaZero Actor-Critic geladen){RESET}")
        except Exception as e:
            print(f"{YELLOW}⚠️ KI startet mit Standard-Architektur ({e}){RESET}")
            
    print(f"🤖 Player 0: {MAGENTA}Auratic PyTorch LGNN (Grandmaster Model){RESET}")
    print(f"👦 Player 1: {CYAN}Stefan (Human Sovereign){RESET}")
    print(f"{BOLD}==================================================================={RESET}\n")

    parser = YGOByteParser()
    pduel = ocgcore.create_duel(777777)
    ocgcore.set_player_info(pduel, 0, 8000, 5, 1)
    ocgcore.set_player_info(pduel, 1, 8000, 5, 1)

    deck = get_beatdown_deck()
    for i, cid in enumerate(deck):
        ocgcore.new_card(pduel, cid, 0, 0, 1, i, 8)
        ocgcore.new_card(pduel, cid, 1, 1, 1, i, 8)

    ocgcore.start_duel(pduel, 0)

    buf = bytearray(8192)
    c_buf = (ctypes.c_byte * 8192).from_buffer(buf)
    
    lp = {0: 8000, 1: 8000}
    turn_num = 1
    current_player = 0
    ai_summoned_in_turn = False
    
    ai_field = {"monsters": []}
    human_field = {"monsters": []}

    while True:
        status = ocgcore.process(pduel)
        mlen = ocgcore.get_message(pduel, ctypes.cast(c_buf, ctypes.c_void_p))
        
        if mlen > 0:
            chunk = bytes(buf[:mlen])
            mtype = chunk[0]
            parser.buffer = chunk
            parser.offset = 1
            
            if mtype == 40: # MSG_NEW_TURN
                current_player = chunk[1]
                ai_summoned_in_turn = False
                render_battlefield(lp, turn_num, current_player, ai_field, human_field)
                turn_num += 1
                
            elif mtype == 90: # MSG_DRAW
                p = chunk[1]
                cnt = chunk[2]
                offset = 3
                cards = []
                for _ in range(cnt):
                    if offset + 4 <= len(chunk):
                        cards.append(struct.unpack("<I", chunk[offset:offset+4])[0])
                        offset += 4
                pname = f"{MAGENTA}KI{RESET}" if p == 0 else f"{CYAN}Stefan{RESET}"
                card_names = ", ".join([card_name(c) for c in cards]) if cards else f"{cnt} Karte(n)"
                print(f"🃏 {pname} zieht: {card_names}")
                
            elif mtype == 91: # MSG_DAMAGE
                p = chunk[1]
                dmg = struct.unpack("<I", chunk[2:6])[0]
                lp[p] = max(0, lp[p] - dmg)
                pname = f"{MAGENTA}KI{RESET}" if p == 0 else f"{CYAN}Stefan{RESET}"
                print(f"💥 {RED}{pname} erleidet {dmg} Schadenspunkte! (LP: {lp[p]}){RESET}")
                if lp[p] == 0:
                    winner = f"{CYAN}STEFAN{RESET}" if p == 0 else f"{MAGENTA}DIE PYTORCH KI{RESET}"
                    print(f"\n{BOLD}🏆🏆🏆 DUELL BEENDET! SIEGER: {winner}! 🏆🏆🏆{RESET}")
                    return

            elif mtype == 18: # MSG_SELECT_PLACE
                p = chunk[1]
                zone_seq = len(ai_field["monsters"]) - 1 if p == 0 else len(human_field["monsters"]) - 1
                zone_seq = max(0, min(4, zone_seq))
                resp_place = (ctypes.c_byte * 3)(p, 4, zone_seq)
                ocgcore.set_responseb(pduel, ctypes.cast(resp_place, ctypes.c_void_p))

            elif mtype == 16: # MSG_SELECT_CHAIN
                ocgcore.set_responsei(pduel, -1)

            elif mtype == 19: # MSG_SELECT_POSITION
                ocgcore.set_responsei(pduel, 1)

            elif mtype in [10, 11, 12, 13, 14, 15, 20]:
                ev = None
                phase_title = "MAIN PHASE"
                if mtype == 11: 
                    ev = parser.parse_idlecmd()
                    phase_title = "MAIN PHASE"
                elif mtype == 10: 
                    ev = parser.parse_battlecmd()
                    phase_title = "BATTLE PHASE (ANGRIFF)"
                elif mtype == 14: 
                    ev = parser.parse_select_option()
                    phase_title = "EFFEKT-OPTION WÄHLEN"
                elif mtype == 15: 
                    ev = parser.parse_select_card()
                    phase_title = "ANGRIFFSZIEL / KARTE WÄHLEN"
                elif mtype in [12, 13]: 
                    ev = parser.parse_select_yesno()
                    phase_title = "JA / NEIN ABFRAGE"
                
                if ev and "legal_actions" in ev and len(ev["legal_actions"]) > 0:
                    p = ev.get("player", 0)
                    actions = ev["legal_actions"]
                    num_act = min(len(actions), 32)
                    
                    if p == 0:
                        # 🤖 KI WÄHLT MIT NEURONALEM NETZ
                        if mtype == 11 and ai_summoned_in_turn:
                            chosen_act = [a for a in actions if a.get("type") == "TO_BATTLE_PHASE"][0] if any(a.get("type") == "TO_BATTLE_PHASE" for a in actions) else actions[-1]
                            chosen_idx = actions.index(chosen_act)
                            print(f"{MAGENTA}🤖 [KI]:{RESET} {format_action_desc(chosen_act)}")
                        else:
                            state = torch.tensor([
                                float(num_act) / 32.0, float(lp[0]) / 8000.0, float(lp[1]) / 8000.0,
                                float(turn_num) / 20.0, float(mtype) / 20.0, 1.0, 0.0,
                                1.0 if mtype == 11 else 0.0, 1.0 if mtype == 10 else 0.0,
                                0.0, 0.0, 0.5, 0.5, 0.5, 0.5, 1.0
                            ], dtype=torch.float32)
                            chosen_idx = brain.act(state, num_act)
                            chosen_act = actions[chosen_idx]
                            
                            if chosen_act.get("type") == "SUMMON":
                                ai_field["monsters"].append(chosen_act.get("card_id", 0))
                                ai_summoned_in_turn = True
                                print(f"{MAGENTA}🤖 [KI]:{RESET} {format_action_desc(chosen_act)}")
                            elif chosen_act.get("type") == "ATTACK":
                                print(f"{MAGENTA}🤖 [KI Battle]:{RESET} {format_action_desc(chosen_act)}")
                            elif chosen_act.get("type") == "TO_END_PHASE":
                                print(f"{MAGENTA}🤖 [KI]:{RESET} Beendet Zug (To EP).")
                                
                        if mtype == 15:
                            resp = (ctypes.c_byte * 2)(1, chosen_idx)
                            ocgcore.set_responseb(pduel, ctypes.cast(resp, ctypes.c_void_p))
                        else:
                            resp = action_to_response_code(chosen_act, chosen_idx, mtype)
                            ocgcore.set_responsei(pduel, resp)
                    else:
                        # 👦 STEFAN WÄHLT INTERAKTIV IM TERMINAL
                        print("\n" + "┌" + "─" * 74 + "┐")
                        print(f"│ {CYAN}{BOLD}👦 STEFAN, DU BIST AM ZUG! [📍 {phase_title}]{RESET}")
                        print(f"│ {DIM}Hier sind alle deine legalen Optionen im aktuellen Moment:{RESET}")
                        print("├" + "─" * 74 + "┤")
                        
                        shortcut_map = {}
                        for a_idx, act in enumerate(actions):
                            atype = act.get("type", "")
                            if atype == "TO_BATTLE_PHASE": shortcut_map['b'] = a_idx
                            elif atype == "TO_END_PHASE": shortcut_map['e'] = a_idx
                            elif atype == "CANCEL_CHAIN": shortcut_map['s'] = a_idx
                            
                            print(f"│  [{BOLD}{CYAN}{a_idx}{RESET}] -> {format_action_desc(act, detailed=True)}")
                        print("└" + "─" * 74 + "┘")
                        
                        hint = f"👉 Deine Wahl ({BOLD}0-{len(actions)-1}{RESET}"
                        if 'b' in shortcut_map: hint += f" | {BOLD}b{RESET}=Battle"
                        if 'e' in shortcut_map: hint += f" | {BOLD}e{RESET}=End"
                        if 's' in shortcut_map: hint += f" | {BOLD}s{RESET}=Skip"
                        hint += f") [Default: 0]: "
                        
                        while True:
                            try:
                                sys.stdout.flush()
                                val = input(hint).strip().lower()
                                if val in shortcut_map:
                                    choice = shortcut_map[val]
                                    break
                                elif val.isdigit() and int(val) < len(actions):
                                    choice = int(val)
                                    break
                                else:
                                    choice = 0
                                    break
                            except (EOFError, KeyboardInterrupt):
                                choice = 0
                                break
                                
                        chosen_act = actions[choice]
                        if chosen_act.get("type") == "SUMMON":
                            human_field["monsters"].append(chosen_act.get("card_id", 0))
                            
                        print(f"👉 Stefan wählt: {format_action_desc(chosen_act)}")
                        if mtype == 15: # MSG_SELECT_CARD
                            resp = (ctypes.c_byte * 2)(1, choice)
                            ocgcore.set_responseb(pduel, ctypes.cast(resp, ctypes.c_void_p))
                        else:
                            resp = action_to_response_code(chosen_act, choice, mtype)
                            ocgcore.set_responsei(pduel, resp)
                else:
                    if mtype == 11: ocgcore.set_responsei(pduel, 7)
                    elif mtype == 16: ocgcore.set_responsei(pduel, -1)
                    else: ocgcore.set_responsei(pduel, 0)
                    
        if status == 0:
            print(f"\n{BOLD}🏆🏆🏆 DUELL BEENDET! 🏆🏆🏆{RESET}")
            break

if __name__ == "__main__":
    run_interactive_arena()
