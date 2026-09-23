import ctypes
import os
import struct
from loguru import logger

core_path = os.path.join(os.path.dirname(__file__), "ygopro-core/bin/Release/libocgcore.so")
ocgcore = ctypes.cdll.LoadLibrary(core_path)

ocgcore.create_duel.restype = ctypes.c_void_p
ocgcore.get_message.restype = ctypes.c_int32

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

ocgcore.set_card_reader(ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(CardData))(card_reader_cb))
ocgcore.set_script_reader(ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int32))(script_reader_cb))
ocgcore.set_message_handler(ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32)(lambda p, m: 0))

pduel = ocgcore.create_duel(1337)
ocgcore.set_player_info(pduel, 0, 8000, 5, 1)
ocgcore.set_player_info(pduel, 1, 8000, 5, 1)
for i in range(40): ocgcore.new_card(pduel, 89631139, 0, 0, 1, i, 8)
for i in range(40): ocgcore.new_card(pduel, 89631139, 1, 1, 1, i, 8)

ocgcore.start_duel(pduel, 0)

msg_buffer = bytearray(4096)
c_buffer = (ctypes.c_byte * 4096).from_buffer(msg_buffer)

print("Starte Endlos-Schleife der Engine...")

for step in range(3):
    status = ocgcore.process(pduel)
    print(f"Step {step}: Engine Process Status = {status}")
    
    # Lese Bytes
    msg_len = ocgcore.get_message(pduel, ctypes.cast(c_buffer, ctypes.c_void_p))
    if msg_len > 0:
        msg_type = msg_buffer[0]
        print(f"Empfangene Nachricht: Typ {msg_type}")
        
        if msg_type == 15: # MSG_SELECT_IDLECMD (Wartet auf Antwort)
            print("Engine verlangt eine Main-Phase-Entscheidung!")
            print("KI drückt Knopf 0 (erste Option im legal_actions Menü)...")
            
            # Die Antwort-Funktion in OCGCore
            # set_responsei erwartet uint32_t. In EDOPro ist '0' oft z.B. die erste Option
            ocgcore.set_responsei.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            ocgcore.set_responsei(pduel, 0)
            print("Antwort an C++ gesendet! Gehe zum nächsten Step.")
