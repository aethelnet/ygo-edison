import ctypes
import os
import struct
from ygo_byte_parser import YGOByteParser
from ygo_self_play import action_to_response_code
from ygo_sqlite_bridge import YGOSqliteBridge

db = YGOSqliteBridge()

core_path = os.path.join(os.path.dirname(__file__), "ygopro-core/bin/Release/libocgcore.so")
ocgcore = ctypes.cdll.LoadLibrary(core_path)
ocgcore.create_duel.restype = ctypes.c_void_p
ocgcore.create_duel.argtypes = [ctypes.c_uint32]
ocgcore.set_player_info.argtypes = [ctypes.c_void_p, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32]
ocgcore.new_card.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint8]
ocgcore.start_duel.argtypes = [ctypes.c_void_p, ctypes.c_int32]
ocgcore.process.restype = ctypes.c_int32
ocgcore.process.argtypes = [ctypes.c_void_p]
ocgcore.get_message.restype = ctypes.c_int32
ocgcore.get_message.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
ocgcore.set_responsei.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
ocgcore.set_responseb.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
ocgcore.set_script_reader.argtypes = [ctypes.c_void_p]
ocgcore.set_card_reader.argtypes = [ctypes.c_void_p]
ocgcore.set_message_handler.argtypes = [ctypes.c_void_p]

class CardData(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_uint32), ("alias", ctypes.c_uint32), ("setcode", ctypes.c_uint16 * 16),
        ("type", ctypes.c_uint32), ("level", ctypes.c_uint32), ("attribute", ctypes.c_uint32),
        ("race", ctypes.c_uint32), ("attack", ctypes.c_int32), ("defense", ctypes.c_int32),
        ("lscale", ctypes.c_uint32), ("rscale", ctypes.c_uint32), ("link_marker", ctypes.c_uint32),
        ("rule_code", ctypes.c_uint32)
    ]

@ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_uint32, ctypes.POINTER(CardData))
def card_reader_cb(code, pdata):
    ctypes.memset(pdata, 0, ctypes.sizeof(CardData))
    pdata.contents.code = code
    info = db.card_metadata.get(code)
    if info:
        pdata.contents.alias = info.get("alias", 0)
        pdata.contents.attack = max(0, info.get("atk", 0))
        pdata.contents.defense = max(0, info.get("def", 0))
        pdata.contents.type = info.get("type", 0)
        pdata.contents.level = info.get("level", 0)
        pdata.contents.race = info.get("race", 0)
        pdata.contents.attribute = info.get("attribute", 0)
        sc = info.get("setcode", 0)
        s_idx = 0
        while sc and s_idx < 16:
            part = sc & 0xFFFF
            if part:
                pdata.contents.setcode[s_idx] = part
                s_idx += 1
            sc >>= 16
    return 1 if info else 0

script_dirs = [
    os.path.abspath(os.path.join(os.path.dirname(__file__), "script")),
]
_buffers = []

@ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int))
def script_reader_cb(name_bytes, plen):
    if not name_bytes:
        plen[0] = 0
        return 0
    name_str = ctypes.string_at(name_bytes).decode('utf-8', errors='ignore')
    base_name = os.path.basename(name_str)
    for sdir in script_dirs:
        file_path = os.path.join(sdir, base_name)
        if os.path.exists(file_path):
            with open(file_path, 'rb') as f:
                content = f.read()
            c_buf = (ctypes.c_char * len(content)).from_buffer_copy(content)
            _buffers.append(c_buf)
            plen[0] = len(content)
            return ctypes.cast(c_buf, ctypes.c_void_p).value
    plen[0] = 0
    return 0

ocgcore.set_card_reader(card_reader_cb)
ocgcore.set_script_reader(script_reader_cb)
ocgcore.set_message_handler(ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32)(lambda p, m: 0))

def test_duel():
    pduel = ocgcore.create_duel(12345)
    ocgcore.set_player_info(pduel, 0, 8000, 5, 1)
    ocgcore.set_player_info(pduel, 1, 8000, 5, 1)

    deck0 = [48686504, 15341821, 20932152, 48686504, 15341821] + [89631139]*35
    deck1 = [89631139]*40

    for i, c in enumerate(deck0):
        ocgcore.new_card(pduel, c, 0, 0, 1, i, 8)
    for i, c in enumerate(deck1):
        ocgcore.new_card(pduel, c, 1, 1, 1, i, 8)

    ocgcore.start_duel(pduel, 0)

    parser = YGOByteParser()
    msg_buf = ctypes.create_string_buffer(65536)

    for step in range(50):
        status = ocgcore.process(pduel)
        length = ocgcore.get_message(pduel, ctypes.byref(msg_buf))
        if length <= 0:
            continue
        data = msg_buf.raw[:length]
        mtype = data[0]
        print(f"[Step {step}] Status={status}, msg_len={length}, mtype={mtype} (0x{mtype:02x})")

        if mtype == 11: # MSG_SELECT_IDLECMD
            parser.buffer = data
            parser.offset = 1
            parsed = parser.parse_idlecmd()
            print(f"  MSG_SELECT_IDLECMD: Player={parsed['player']}, Actions={len(parsed['legal_actions'])}")
            for idx, act in enumerate(parsed['legal_actions']):
                info = db.card_metadata.get(act.get('card_id', 0), {})
                cname = info.get('name', act.get('card_id'))
                print(f"    [{idx}] Type: {act['type']}, SubIdx: {act.get('sub_idx')}, Card: {cname} (Loc: {act.get('loc')}, Seq: {act.get('seq')})")

            summon_acts = [a for a in parsed['legal_actions'] if a['type'] == 'SUMMON' and a['card_id'] == 48686504]
            activate_acts = [a for a in parsed['legal_actions'] if a['type'] == 'ACTIVATE']

            if summon_acts:
                act = summon_acts[0]
                resp = action_to_response_code(act, act['sub_idx'], 11)
                print(f"  -> Setting responsei for SUMMON: {resp}")
                ocgcore.set_responsei(pduel, resp)
            elif activate_acts:
                act = activate_acts[0]
                info = db.card_metadata.get(act.get('card_id', 0), {})
                resp = action_to_response_code(act, act['sub_idx'], 11)
                print(f"  -> Setting responsei for ACTIVATE {info.get('name')}: {resp} (hex: {hex(resp)})")
                ocgcore.set_responsei(pduel, resp)
            else:
                print("No more summon or activate acts.")
                break
        elif mtype == 18: # MSG_SELECT_PLACE
            parser.buffer = data
            parser.offset = 1
            parsed = parser.parse_select_place()
            print(f"  MSG_SELECT_PLACE: choosing zone")
            resp_place = bytearray(256)
            resp_place[0] = 0 # player 0
            resp_place[1] = 4 # mzone
            resp_place[2] = 0 # seq 0
            ocgcore.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_place), 256))
        elif mtype in [15, 20]: # SELECT_CARD / SELECT_TRIBUTE
            parser.buffer = data
            parser.offset = 1
            parsed = parser.parse_select_card() if mtype == 15 else parser.parse_select_tribute()
            print(f"  SELECT_CARD/TRIBUTE (mtype {mtype})")
            for idx, act in enumerate(parsed['legal_actions']):
                info = db.card_metadata.get(act.get('card_id', 0), {})
                print(f"    [{idx}] Select {info.get('name')}")
            # select first card
            resp_arr = bytearray(64)
            resp_arr[0] = 1
            resp_arr[1] = 0
            ocgcore.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_arr), 64))
        elif mtype == 16: # MSG_SELECT_CHAIN
            parser.buffer = data
            parser.offset = 1
            parsed = parser.parse_chain()
            print(f"  MSG_SELECT_CHAIN: {len(parsed['legal_actions'])} actions")
            for idx, act in enumerate(parsed['legal_actions']):
                info = db.card_metadata.get(act.get('card_id', 0), {})
                print(f"    [{idx}] Type: {act['type']}, Card: {info.get('name')}")
            # Pass
            ocgcore.set_responsei(pduel, 0xFFFFFFFF)
        elif mtype == 1:
            print(f"  -> MSG_RETRY received!")
            break

if __name__ == "__main__":
    test_duel()
