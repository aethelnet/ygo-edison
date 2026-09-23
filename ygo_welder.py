import ctypes
import os
import struct

core_path = os.path.join(os.path.dirname(__file__), "ygopro-core/bin/Release/libocgcore.so")
ocgcore = ctypes.cdll.LoadLibrary(core_path)

ocgcore.create_duel.restype = ctypes.c_void_p
ocgcore.get_message.restype = ctypes.c_int32
ocgcore.set_responsei.argtypes = [ctypes.c_void_p, ctypes.c_uint32]

class CardData(ctypes.Structure):
    _fields_ = [("code", ctypes.c_uint32), ("alias", ctypes.c_uint32), ("setcode", ctypes.c_uint16 * 16),
                ("type", ctypes.c_uint32), ("level", ctypes.c_uint32), ("attribute", ctypes.c_uint32),
                ("race", ctypes.c_uint32), ("attack", ctypes.c_int32), ("defense", ctypes.c_int32),
                ("lscale", ctypes.c_uint32), ("rscale", ctypes.c_uint32), ("link_marker", ctypes.c_uint32),
                ("rule_code", ctypes.c_uint32)]

_kept_alive_scripts = []
def script_reader_cb(name, plen):
    path = os.path.join(os.path.dirname(__file__), "script", name.decode('utf-8').replace('./script/', '').replace('script/', ''))
    if os.path.exists(path):
        with open(path, 'rb') as f: content = f.read()
        plen[0] = len(content)
        buffer = (ctypes.c_byte * len(content)).from_buffer_copy(content)
        _kept_alive_scripts.append(buffer)
        return ctypes.cast(buffer, ctypes.c_void_p).value
    plen[0] = 0; return 0

def card_reader_cb(code, pdata):
    data = pdata.contents; data.code = code; data.type = 17; data.attack = 3000
    return code

cb_card = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(CardData))(card_reader_cb)
cb_script = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int32))(script_reader_cb)
cb_msg = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32)(lambda p, m: 0)

ocgcore.set_card_reader(cb_card)
ocgcore.set_script_reader(cb_script)
ocgcore.set_message_handler(cb_msg)

pduel = ocgcore.create_duel(1337)
ocgcore.set_player_info(pduel, 0, 8000, 5, 1)
ocgcore.set_player_info(pduel, 1, 8000, 5, 1)
for i in range(40): ocgcore.new_card(pduel, 89631139, 0, 0, 1, i, 8)
for i in range(40): ocgcore.new_card(pduel, 89631139, 1, 1, 1, i, 8)

ocgcore.start_duel(pduel, 0)

msg_buffer = bytearray(4096)
c_buffer = (ctypes.c_byte * 4096).from_buffer(msg_buffer)

print("=== STARTE AUTONOMEN YGO-RL LOOP ===")

for step in range(15):
    status = ocgcore.process(pduel)
    msg_len = ocgcore.get_message(pduel, ctypes.cast(c_buffer, ctypes.c_void_p))
    
    if msg_len > 0:
        msg_type = msg_buffer[0]
        
        if msg_type == 11:
            print(f"[Schritt {step}] Engine meldet: MSG_DRAW (Karte gezogen)")
        elif msg_type == 20:
            print(f"[Schritt {step}] Engine meldet: MSG_NEW_TURN (Neuer Zug beginnt)")
        elif msg_type == 21:
            print(f"[Schritt {step}] Engine meldet: MSG_NEW_PHASE")
        elif msg_type == 15:
            print(f"[Schritt {step}] Engine blockiert! (Status {status}) -> Erwarte Entscheidung in der Main Phase.")
            print(f"             -> KI wählt Option 0.")
            ocgcore.set_responsei(pduel, 0)
            print(f"             -> Antwort an C++ Memory gesendet!")
        elif msg_type in [10, 12, 13, 14, 16, 17, 18]:
            print(f"[Schritt {step}] Engine blockiert! (Message {msg_type}). KI wählt blind Option 0.")
            ocgcore.set_responsei(pduel, 0)
        else:
            print(f"[Schritt {step}] Engine sendet Message Code: {msg_type}")

print("=== LOOP ERFOLGREICH BEENDET ===")
