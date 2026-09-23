import os
import sys
import time
import socket
import struct
import torch
import torch.nn as nn
from loguru import logger
from ygo_sqlite_bridge import YGOSqliteBridge
from ygo_byte_parser import YGOByteParser

logger.remove()
logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")

CTOS_RESPONSE = 0x01
CTOS_UPDATE_DECK = 0x02
CTOS_PLAYER_INFO = 0x10
CTOS_JOIN_GAME = 0x13
CTOS_HS_READY = 0x20
CTOS_HS_START = 0x21
CTOS_HS_TODUELIST = 0x23

STOC_GAME_MSG = 0x01
STOC_JOIN_GAME = 0x12
STOC_TYPE_CHANGE = 0x13
STOC_DUEL_START = 0x15
STOC_HS_PLAYER_ENTER = 0x20
STOC_HS_PLAYER_CHANGE = 0x21

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

def send_packet(sock, msg_type, payload=b""):
    length = len(payload) + 1
    packet = struct.pack("<HB", length, msg_type) + payload
    sock.sendall(packet)

def encode_name(name_str):
    encoded = name_str.encode('utf-16le')
    if len(encoded) < 40:
        encoded += b'\x00' * (40 - len(encoded))
    return encoded[:40]

def load_ai_deck():
    candidates = ["evolved_decks/Auratic_Gen8_Evolved.ydk", "evolved_decks/Auratic_Gen5_Evolved.ydk", "Auratic_Gen2_Evolved.ydk", "Auratic_Boss_Deck_vs_Stefan.ydk"]
    for c in candidates:
        if os.path.exists(c):
            with open(c, "r") as f:
                lines = f.readlines()
            main_cards = []
            is_main = False
            for line in lines:
                line = line.strip()
                if line == "#main":
                    is_main = True
                    continue
                elif line.startswith("#") or line.startswith("!"):
                    is_main = False
                    continue
                if is_main and line.isdigit():
                    main_cards.append(int(line))
            if len(main_cards) >= 40:
                logger.success(f"🎴 Deck geladen aus {c} ({len(main_cards)} Karten)")
                return main_cards[:40]
                
    pool = [4148264, 5464695, 7359741, 7459013, 11091375, 14898066, 69247929, 69455834]
    return (pool * 5)[:40]

def action_to_response_code(act, choice_idx, mtype):
    atype = act.get("type", "")
    sub_idx = act.get("sub_idx", choice_idx)
    
    if mtype == 11:
        if atype == "SUMMON": return (sub_idx << 16) | 0
        elif atype == "SPSUMMON": return (sub_idx << 16) | 1
        elif atype == "REPOS": return (sub_idx << 16) | 2
        elif atype == "MSET": return (sub_idx << 16) | 3
        elif atype == "SSET": return (sub_idx << 16) | 4
        elif atype == "ACTIVATE": return (sub_idx << 16) | 5
        elif atype == "TO_BATTLE_PHASE": return 6
        elif atype == "TO_END_PHASE": return 7
        else: return 7
    elif mtype == 10:
        if atype == "ATTACK": return (sub_idx << 16) | 1
        elif atype == "ACTIVATE": return (sub_idx << 16) | 0
        elif atype == "TO_MAIN_PHASE_2": return 2
        elif atype == "TO_END_PHASE": return 3
        else: return 3
    elif atype == "CANCEL_CHAIN":
        return -1
    elif atype == "POS_FACEUP_ATTACK":
        return 1
    elif atype == "POS_FACEUP_DEFENSE":
        return 4
    else:
        return choice_idx

def start_bot_client(host="127.0.0.1", port=7911):
    logger.info("===================================================================")
    logger.info("🤖 AURATIC PRIME // SOVEREIGN PYTORCH BOT (EDOPRO CLIENT MODE) 🤖")
    logger.info("===================================================================")
    
    brain = YGOAlphaZeroBrain()
    if os.path.exists("lgnn_grandmaster.pth"):
        try:
            brain.load_state_dict(torch.load("lgnn_grandmaster.pth", map_location="cpu", weights_only=True))
            brain.eval()
            logger.success("🧠 PyTorch AlphaZero Gehirn erfolgreich geladen!")
        except Exception as e:
            logger.warning(f"Fallback-Architektur: {e}")
            
    deck = load_ai_deck()
    parser = YGOByteParser()

    while True:
        try:
            logger.info(f"🔌 Verbinde mit Stefans EDOPro Host auf {host}:{port}...")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((host, port))
            logger.success("⚡ ERFOLGREICH MIT EDOPRO TCP-PORT VERBUNDEN!")
            
            # Step 1: Send Player Info
            ai_name = encode_name("Auratic_LGNN")
            p1_len = len(ai_name) + 1
            p1 = struct.pack("<HB", p1_len, CTOS_PLAYER_INFO) + ai_name
            
            time.sleep(0.1)
            sock.sendall(p1)
            time.sleep(0.1)
            
            # 2. CTOS_JOIN_GAME
            join_payload = bytes.fromhex("541300000000000000000000")
            p2_len = len(join_payload) + 1
            p2 = struct.pack("<HB", p2_len, CTOS_JOIN_GAME) + join_payload
            
            sock.sendall(p2)
            logger.info("📤 Join-Paket gesendet, warte auf Server Handshake-Antwort...")

            deck_sent = False
            ready_sent = False
            ai_monster_count = 0
            lp = {0: 8000, 1: 8000}
            turn_num = 1

            while True:
                header = sock.recv(2)
                if not header or len(header) < 2:
                    logger.warning("Verbindung vom Host beendet.")
                    break
                length = struct.unpack("<H", header)[0]
                
                payload = bytearray()
                while len(payload) < length:
                    chunk = sock.recv(length - len(payload))
                    if not chunk: break
                    payload.extend(chunk)
                    
                if len(payload) == 0: break
                
                msg_type = payload[0]
                data = payload[1:]
                
                logger.info(f"📥 Server Paket empfangen: opcode={hex(msg_type)} (len={len(payload)})")

                # State Machine Transitions
                if msg_type in [STOC_JOIN_GAME, STOC_TYPE_CHANGE, 0x12, 0x13]:
                    if not deck_sent:
                        logger.success("🎴 Server akzeptiert Beitritt! Sende Deck...")
                        deck_payload = struct.pack("<II", len(deck), 0)
                        for cid in deck:
                            deck_payload += struct.pack("<I", cid)
                        send_packet(sock, CTOS_UPDATE_DECK, deck_payload)
                        deck_sent = True
                        
                        # Set to Duelist & Ready
                        send_packet(sock, CTOS_HS_TODUELIST)
                        send_packet(sock, CTOS_HS_READY)
                        logger.success("✅ Bot steht in der Lobby auf BEREIT!")
                        ready_sent = True

                elif msg_type in [STOC_HS_PLAYER_ENTER, STOC_HS_PLAYER_CHANGE, 0x20, 0x21]:
                    if not ready_sent and deck_sent:
                        send_packet(sock, CTOS_HS_READY)
                        logger.success("✅ Bot bestätigt BEREIT-Status!")
                        ready_sent = True

                elif msg_type == STOC_GAME_MSG and len(data) > 0:
                    mtype = data[0]
                    
                    if mtype == 40: # MSG_NEW_TURN
                        turn_num += 1
                        logger.info(f"🔄 Runde {turn_num} beginnt!")
                        
                    elif mtype == 18: # MSG_SELECT_PLACE
                        p = data[1] if len(data) > 1 else 0
                        zone_seq = max(0, min(4, ai_monster_count))
                        resp_bytes = bytes([p, 4, zone_seq])
                        send_packet(sock, CTOS_RESPONSE, resp_bytes)
                        logger.info(f"🤖 [Bot]: Platziert Monster in 3D-Zone {zone_seq}")
                        
                    elif mtype == 16: # MSG_SELECT_CHAIN
                        send_packet(sock, CTOS_RESPONSE, struct.pack("<i", -1))
                        
                    elif mtype == 19: # MSG_SELECT_POSITION
                        send_packet(sock, CTOS_RESPONSE, struct.pack("<i", 1))
                        logger.info("🤖 [Bot]: Wählt offene Angriffsposition (ATK)")
                        
                    elif mtype in [10, 11, 12, 13, 14, 15, 20]:
                        parser.buffer = bytes(data)
                        parser.offset = 1
                        ev = None
                        if mtype == 11: ev = parser.parse_idlecmd()
                        elif mtype == 10: ev = parser.parse_battlecmd()
                        elif mtype == 14: ev = parser.parse_select_option()
                        elif mtype == 15: ev = parser.parse_select_card()
                        elif mtype in [12, 13]: ev = parser.parse_select_yesno()
                        
                        if ev and "legal_actions" in ev and len(ev["legal_actions"]) > 0:
                            actions = ev["legal_actions"]
                            num_act = min(len(actions), 32)
                            
                            state = torch.tensor([
                                float(num_act) / 32.0, float(lp[0]) / 8000.0, float(lp[1]) / 8000.0,
                                float(turn_num) / 20.0, float(mtype) / 20.0, 1.0, 0.0,
                                1.0 if mtype == 11 else 0.0, 1.0 if mtype == 10 else 0.0,
                                0.0, 0.0, 0.5, 0.5, 0.5, 0.5, 1.0
                            ], dtype=torch.float32)
                            
                            chosen_idx = brain.act(state, num_act)
                            chosen_act = actions[chosen_idx]
                            
                            if chosen_act.get("type") == "SUMMON":
                                ai_monster_count += 1
                                logger.info(f"🤖 [Bot Action]: Beschwört Monster {chosen_act.get('card_id')}")
                            elif chosen_act.get("type") == "ATTACK":
                                logger.info(f"🤖 [Bot Battle]: Greift mit Monster an!")
                            elif chosen_act.get("type") == "TO_END_PHASE":
                                logger.info("🤖 [Bot]: Beendet Zug (End Phase).")
                                
                            if mtype == 15:
                                resp_b = bytes([1, chosen_idx])
                                send_packet(sock, CTOS_RESPONSE, resp_b)
                            else:
                                resp_code = action_to_response_code(chosen_act, chosen_idx, mtype)
                                send_packet(sock, CTOS_RESPONSE, struct.pack("<I", resp_code))
                                
        except ConnectionRefusedError:
            logger.info("⏳ Warte auf Stefan... (Starte 'Host Game' in EDOPro auf Port 7911)")
            time.sleep(2.0)
        except KeyboardInterrupt:
            logger.info("🛑 Bot beendet.")
            break
        except Exception as e:
            logger.error(f"Bot Loop Event: {e}")
            time.sleep(2.0)

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 7911
    start_bot_client(port=port)
