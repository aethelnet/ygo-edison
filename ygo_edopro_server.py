import time
import os
import sys

import socket
import struct
import ctypes
from loguru import logger
from ygo_sqlite_bridge import YGOSqliteBridge
from ygo_byte_parser import YGOByteParser
import torch
import torch.nn as nn

logger.remove()
logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")

core_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "ygopro-core/bin/Release/libocgcore.so"))
script_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "script"))

ocgcore = ctypes.cdll.LoadLibrary(core_path)

from ygo_self_play import YGOAlphaZeroBrain

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

# Network Protocol Opcodes
CTOS_RESPONSE = 0x01
CTOS_UPDATE_DECK = 0x02
CTOS_PLAYER_INFO = 0x10
CTOS_JOIN_GAME = 0x12

STOC_GAME_MSG = 0x01
STOC_JOIN_GAME = 0x12
STOC_TYPE_CHANGE = 0x13
STOC_DUEL_START = 0x15
STOC_HS_PLAYER_ENTER = 0x20
STOC_HS_PLAYER_CHANGE = 0x21

def send_packet(conn, msg_type, payload=b""):
    length = len(payload) + 1
    packet = struct.pack("<HB", length, msg_type) + payload
    conn.sendall(packet)

def encode_name(name_str):
    encoded = name_str.encode('utf-16le')
    if len(encoded) < 40:
        encoded += b'\x00' * (40 - len(encoded))
    return encoded[:40]

def get_playable_ai_deck():
    pool = [4148264, 5464695, 7359741, 7459013, 11091375, 14898066, 69247929, 69455834]
    return (pool * 5)[:40]

BLOCKING_MSGS = [10, 11, 12, 13, 14, 15, 16, 18, 19, 20]
global_brain = YGOAlphaZeroBrain()
if os.path.exists("lgnn_grandmaster.pth"):
    try:
        global_brain.load_state_dict(torch.load("lgnn_grandmaster.pth", map_location="cpu", weights_only=True))
        global_brain.eval()
        logger.success("🧠 PyTorch Gewichte erfolgreich in Server geladen!")
    except Exception as e:
        logger.warning(f"Fallback-Architektur: {e}")

def pump_engine_until_human(pduel, conn, parser, c_buffer, msg_buffer):
    """Processes game events and streams STOC_GAME_MSG with smooth 3D animation pacing"""
    ai_summoned = False
    ai_monster_count = 0
    stefan_monster_count = 0
    
    while True:
        status = ocgcore.process(pduel)
        msg_len = ocgcore.get_message(pduel, ctypes.cast(c_buffer, ctypes.c_void_p))
        
        if msg_len > 0:
            raw_chunk = bytes(msg_buffer[:msg_len])
            mtype = raw_chunk[0]
            player = raw_chunk[1] if msg_len > 1 else 0
            
            # Debug log to see exactly what we send to EDOPro
            logger.debug(f"📤 STOC_GAME_MSG: mtype={mtype} ({hex(mtype)})")
            if mtype == 5:
                logger.error(f"💀 MSG_WIN GENERATED! Payload: {raw_chunk.hex()}")
            
            # Stream game event packet to EDOPro GUI
            # DO NOT SEND AI PROMPTS TO THE HUMAN CLIENT! EDOPro will crash!
            is_ai_prompt = False
            if mtype in [10, 11, 12, 13, 14, 15, 16, 18, 19, 20, 22, 23, 24]:
                if player == 0:
                    is_ai_prompt = True
                elif mtype == 16 and msg_len > 2 and raw_chunk[2] == 0:
                    # Do not send empty chain prompts to client
                    is_ai_prompt = True
            
            if not is_ai_prompt:
                send_packet(conn, STOC_GAME_MSG, raw_chunk)
            else:
                send_packet(conn, STOC_GAME_MSG, bytes([3])) # MSG_WAITING
            
            # Smooth 3D animation delay pacing so EDOPro client never chokes:
            if mtype in [40, 90]: time.sleep(0.15)       # New Turn / Draw animations
            elif mtype in [60, 61, 62]: time.sleep(0.2)  # Summoning animations
            elif mtype == 41: time.sleep(0.05)           # Phase transition animations
            else: time.sleep(0.02)
            
            if mtype == 1: # MSG_RETRY
                logger.warning("⚠️ Engine lehnte Input ab (MSG_RETRY). Warte auf neuen Input von Stefan...")
                return
            elif mtype == 40: # MSG_NEW_TURN
                ai_summoned = False
                logger.info(f"🔄 Turn switch in EDOPro: Player {player}")
                
            elif mtype == 16: # MSG_SELECT_CHAIN
                chain_count = raw_chunk[2] if msg_len > 2 else 0
                if chain_count == 0 or player == 0:
                    # Immer passen, wenn nichts zu ketten da ist ODER wenn KI gefragt wird
                    ocgcore.set_responsei(pduel, -1)
                    continue
                else:
                    logger.success("⏳ EDOPro GUI wartet auf Ketten-Reaktion von Stefan...")
                    return

            elif mtype in [10, 11, 12, 13, 14, 15, 18, 19, 20]:
                if player == 0:
                    # 🤖 AI'S TURN (Autonomous Response)
                    if mtype == 18:
                        import struct
                        flag = struct.unpack("<I", raw_chunk[3:7])[0]
                        seq = 0
                        valid_bits = [0,1,2,3,4,5,6, 8,9,10,11,12,13, 16,17,18,19,20,21,22, 24,25,26,27,28,29]
                        for i in valid_bits:
                            if (flag & (1 << i)) == 0:
                                seq = i
                                break
                        zone_p = player
                        if seq >= 16:
                            zone_p = 1 - player
                            s = seq - 16
                        else:
                            s = seq
                        l = 4 if s < 8 else 8
                        s = s % 8
                        resp_place = bytearray(256)
                        resp_place[0] = zone_p
                        resp_place[1] = l
                        resp_place[2] = s
                        ocgcore.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_place), 256))
                    elif mtype == 19:
                        positions = raw_chunk[6]
                        resp_pos = 1
                        for i in range(8):
                            if (positions & (1 << i)) != 0:
                                resp_pos = (1 << i)
                                break
                        ocgcore.set_responsei(pduel, resp_pos)
                    else:
                        parser.buffer = bytes(raw_chunk)
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
                                float(num_act) / 32.0, float(8000) / 8000.0, float(8000) / 8000.0,
                                float(1) / 20.0, float(mtype) / 20.0, 1.0, 0.0,
                                1.0 if mtype == 11 else 0.0, 1.0 if mtype == 10 else 0.0,
                                0.0, 0.0, 0.5, 0.5, 0.5, 0.5, 1.0
                            ], dtype=torch.float32)
                            
                            chosen_idx, _, _, _ = global_brain.act(state, num_act)
                            chosen_act = actions[chosen_idx]
                            
                            if mtype == 15:
                                resp = (ctypes.c_byte * 2)(1, chosen_idx)
                                ocgcore.set_responseb(pduel, ctypes.cast(resp, ctypes.c_void_p))
                            else:
                                resp_code = action_to_response_code(chosen_act, chosen_idx, mtype)
                                ocgcore.set_responsei(pduel, resp_code)
                        else:
                            ocgcore.set_responsei(pduel, 0)
                            
                    continue
                else:
                    # 👦 STEFAN'S TURN (Wait for EDOPro mouse click / CTOS_RESPONSE)
                    logger.success(f"⏳ EDOPro GUI wartet auf Stefans Eingabe (Opcode {mtype})...")
                    return
                    
        if status == 0:
            logger.success("🏆 Duell beendet!")
            break
            
        if status == 1 and msg_len == 0:
            logger.info("⏳ Engine wartet auf Input, pausiere Loop...")
            return

def handle_client(conn, addr, parser):
    logger.success(f"⚡ VERBINDUNG HERGESTELLT VON STEFAN ({addr})!")
    player_name = "Stefan"
    duel_started = False
    pduel = None
    msg_buffer = bytearray(8192)
    c_buffer = (ctypes.c_byte * 8192).from_buffer(msg_buffer)
    
    resp_buf = bytearray(256)
    c_resp = (ctypes.c_byte * 256).from_buffer(resp_buf)
    
    client_deck = [89631139] * 40
    ai_deck = get_playable_ai_deck()
    
    try:
        while True:
            header = conn.recv(2)
            if not header or len(header) < 2:
                break
            length = struct.unpack("<H", header)[0]
            
            payload = bytearray()
            while len(payload) < length:
                chunk = conn.recv(length - len(payload))
                if not chunk:
                    break
                payload.extend(chunk)
                
            if len(payload) == 0:
                break
                
            msg_type = payload[0]
            data = payload[1:]
            
            logger.info(f"📥 CTOS Packet: opcode={hex(msg_type)} (len={len(payload)})")
            
            if msg_type == CTOS_PLAYER_INFO:
                logger.info(f"MAGICAL PLAYER INFO: {data.hex()}")
                try:
                    player_name = data[:40].decode('utf-16le', errors='ignore').strip('\x00')
                except:
                    player_name = "Stefan"
                logger.info(f"👤 Player Info: {player_name}")
                
            elif msg_type == CTOS_JOIN_GAME:
                logger.info(f"🚪 Stefan betritt die Duel-Lobby...")
                logger.info(f"MAGICAL BYTES: {data.hex()}")
                join_info = struct.pack("<BBBBBBIBBH", 0, 5, 0, 0, 0, 0, 8000, 5, 1, 180)
                send_packet(conn, STOC_JOIN_GAME, join_info)
                send_packet(conn, STOC_TYPE_CHANGE, bytes([0x11]))
                
                ai_name = encode_name("Auratic_LGNN")
                send_packet(conn, STOC_HS_PLAYER_ENTER, ai_name + bytes([0x0]))
                
                stefan_encoded = encode_name(player_name)
                send_packet(conn, STOC_HS_PLAYER_ENTER, stefan_encoded + bytes([0x1]))
                
                send_packet(conn, STOC_HS_PLAYER_CHANGE, bytes([(0 << 4) | 0x1]))
                logger.success("✅ Lobby Handshake abgeschlossen. KI ist BEREIT! START-BUTTON AKTIV!")

            elif msg_type == 0x02 and len(data) >= 8:
                try:
                    main_count, extra_count = struct.unpack("<II", data[:8])
                    parsed_cards = [struct.unpack("<I", data[8+i*4:12+i*4])[0] for i in range(main_count)]
                    if parsed_cards:
                        client_deck = parsed_cards
                        logger.success(f"🎴 Stefans Deck erfolgreich mit Server synchronisiert! ({len(client_deck)} Karten)")
                except Exception as e:
                    logger.warning(f"Deck-Sync fallback: {e}")
                    
                send_packet(conn, STOC_HS_PLAYER_CHANGE, bytes([(1 << 4) | 0x1]))

            elif msg_type in [0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26] or (msg_type == 0x02 and len(data) < 8):
                logger.warning(f"⚔️ DUEL START SIGNAL ERHALTEN (Opcode {hex(msg_type)})! Starte 3D-Duell...")
                send_packet(conn, STOC_DUEL_START)
                
                pduel = ocgcore.create_duel(777777)
                ocgcore.set_player_info(pduel, 0, 8000, 5, 1)
                ocgcore.set_player_info(pduel, 1, 8000, 5, 1)
                
                for i, cid in enumerate(ai_deck[:40]):
                    ocgcore.new_card(pduel, cid, 0, 0, 1, i, 8)
                for i, cid in enumerate(client_deck[:len(client_deck)]):
                    ocgcore.new_card(pduel, cid, 1, 1, 1, i, 8)
                    
                ocgcore.start_duel(pduel, 5 << 16)
                duel_started = True
                logger.success("🚀 DUELL GESTARTET! 3D-Spielfeld (Master Rule 5) wird in EDOPro geladen...")
                
                # Craft MSG_START (opcode 4)
                # Format: msg(1), playertype(1), rule(4), lp1(4), lp2(4), deck1(2), deck2(2), extra1(2), extra2(2)
                # Stefan is Player 1 (Guest), so playertype = 1
                msg_start_payload = struct.pack("<BBiiihhhh", 4, 1, 5, 8000, 8000, len(ai_deck[:40]), len(client_deck), 15, 15)
                send_packet(conn, STOC_GAME_MSG, msg_start_payload)
                
                
                time.sleep(0.5) # Give EDOPro time to initialize the field
                
                pump_engine_until_human(pduel, conn, parser, c_buffer, msg_buffer)

            elif msg_type == CTOS_RESPONSE:
                if duel_started and pduel:
                    logger.debug(f"🔍 CTOS_RESPONSE Data: {data.hex()}")
                    resp_buf[:] = b'\x00' * 256
                    resp_buf[:len(data)] = data
                    ocgcore.set_responseb(pduel, ctypes.cast(c_resp, ctypes.c_void_p))
                    pump_engine_until_human(pduel, conn, parser, c_buffer, msg_buffer)

    except Exception as e:
        logger.error(f"⚠️ Netzwerk / Engine Event: {e}")
    finally:
        conn.close()
        logger.info("🛑 Verbindung geschlossen. Server bleibt aktiv für nächsten Connect...")

def run_server_forever(host="0.0.0.0", port=7911):
    logger.info(f"🌐 [PHASE 39] Starting Persistent EDOPro TCP Server on {host}:{port}...")
    
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((host, port))
    server_sock.listen(5)
    
    logger.success(f"🚀 SERVER DAEMON ONLINE auf Port {port}! Wartet auf Verbindungen...")
    parser = YGOByteParser()
    
    while True:
        try:
            conn, addr = server_sock.accept()
            handle_client(conn, addr, parser)
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"Server loop error: {e}")

if __name__ == "__main__":
    port = 7911
    if len(sys.argv) > 1:
        port = int(sys.argv[1])
    run_server_forever("0.0.0.0", port)
