import os
import ctypes
from ygo_sqlite_bridge import YGOSqliteBridge
from ygo_byte_parser import YGOByteParser

db = YGOSqliteBridge()
core_path = os.path.join(os.path.dirname(__file__), "ygopro-core/bin/Release/libocgcore.so")
ocg = ctypes.cdll.LoadLibrary(core_path)

ocg.create_duel.restype = ctypes.c_void_p
ocg.create_duel.argtypes = [ctypes.c_uint32]
ocg.set_player_info.argtypes = [ctypes.c_void_p, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32]
ocg.new_card.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint8]
ocg.start_duel.argtypes = [ctypes.c_void_p, ctypes.c_int32]
ocg.process.restype = ctypes.c_int32
ocg.process.argtypes = [ctypes.c_void_p]
ocg.get_message.restype = ctypes.c_int32
ocg.get_message.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

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
    os.path.abspath(os.path.join(os.path.dirname(__file__), "ygopro-core/script")),
]

@ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int32))
def script_reader_cb(name, plen):
    sname = name.decode("utf-8")
    for sdir in script_dirs:
        p = os.path.join(sdir, sname)
        if os.path.exists(p):
            with open(p, "rb") as f:
                content = f.read()
                plen[0] = len(content)
                buf = (ctypes.c_char * len(content)).from_buffer_copy(content)
                return ctypes.cast(buf, ctypes.c_void_p)
    plen[0] = 0
    return 0

msg_handler_cb = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32)(lambda p, m: 0)

ocg.set_card_reader(card_reader_cb)
ocg.set_script_reader(script_reader_cb)
ocg.set_message_handler(msg_handler_cb)

def test_scenario():
    pduel = ocg.create_duel(777)
    # Set LP: 4000 for both, draw 3 cards for P0, 0 for P1
    ocg.set_player_info(pduel, 0, 4000, 3, 0)
    ocg.set_player_info(pduel, 1, 4000, 0, 0)

    # Deck for player 0: Quickdraw (20932152), Dandylion (15341821), Heavy Storm (19613556)
    ocg.new_card(pduel, 20932152, 0, 0, 1, 0, 8)
    ocg.new_card(pduel, 15341821, 0, 0, 1, 1, 8)
    ocg.new_card(pduel, 19613556, 0, 0, 1, 2, 8)

    # Extra Deck for player 0: Drill Warrior (3429238), Junk Destroyer (74860293)
    ocg.new_card(pduel, 3429238, 0, 0, 64, 0, 8)
    ocg.new_card(pduel, 74860293, 0, 0, 64, 1, 8)

    # Opponent field: Stardust Dragon faceup attack (loc 4, seq 2, pos 1)
    ocg.new_card(pduel, 44508094, 1, 1, 4, 2, 1)
    # Opponent backrow: Mirror Force facedown (loc 8, seq 2, pos 8)
    ocg.new_card(pduel, 44095762, 1, 1, 8, 2, 8)

    ocg.start_duel(pduel, 0x08)

    buf = ctypes.create_string_buffer(65536)
    parser = YGOByteParser(db.card_metadata)

    for step in range(50):
        status = ocg.process(pduel)
        msg_len = ocg.get_message(pduel, ctypes.cast(buf, ctypes.c_void_p))
        if msg_len > 0:
            mtype = buf.raw[0]
            parser.buffer = buf.raw[:msg_len]
            parser.offset = 1
            if mtype == 11:
                res = parser.parse_idlecmd()
                print("=== SUCCESS! IDLECMD Actions ===")
                for act in res["legal_actions"]:
                    cid = act.get("card_id")
                    name = db.card_metadata.get(cid, {}).get("name") if cid else act.get("type")
                    print(f"  [{act['type']}] {name} (id={cid}, sub_idx={act.get('sub_idx')})")
                return True
    return False

if __name__ == "__main__":
    test_scenario()
