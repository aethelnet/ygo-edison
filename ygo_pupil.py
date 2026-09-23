import ctypes
import os
import struct
from loguru import logger

# --- C++ DLL LADEN ---
core_path = os.path.join(os.path.dirname(__file__), "ygopro-core/bin/Release/libocgcore.so")
ocgcore = ctypes.cdll.LoadLibrary(core_path)

ocgcore.create_duel.restype = ctypes.c_void_p
# int32 query_card(void* pduel, uint8 playerid, uint8 location, uint8 sequence, uint32 query_flag, byte* pbuf, int32 use_cache)
ocgcore.query_card.argtypes = [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int32]
ocgcore.query_card.restype = ctypes.c_int32

# Yu-Gi-Oh Konstanten
LOCATION_MZONE = 0x04
QUERY_CODE = 0x1
QUERY_ATTACK = 0x10
QUERY_DEFENSE = 0x20
# Wir fragen Code (1), ATK (16) und DEF (32) ab -> Flag = 1 + 16 + 32 = 49 (0x31)
QUERY_FLAG = 0x31 

class CardData(ctypes.Structure):
    _fields_ = [("code", ctypes.c_uint32), ("alias", ctypes.c_uint32), ("setcode", ctypes.c_uint16 * 16),
                ("type", ctypes.c_uint32), ("level", ctypes.c_uint32), ("attribute", ctypes.c_uint32),
                ("race", ctypes.c_uint32), ("attack", ctypes.c_int32), ("defense", ctypes.c_int32)]

def card_reader_cb(code, pdata):
    data = pdata.contents
    data.code = code
    data.attack = 3000
    data.defense = 2500
    return code

cb_card = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(CardData))(card_reader_cb)
ocgcore.set_card_reader(cb_card)
ocgcore.set_script_reader(ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int32))(lambda n, p: 0))

logger.info("👁️ Erschaffe die Pupille...")

# 1. Duell starten und ein Monster aufs Feld legen
pduel = ocgcore.create_duel(1234)
# new_card(pduel, code, owner, playerid, location, sequence, position)
# Wir legen den Blauäugigen W. Drachen (89631139) auf Player 0, Monsterzone 0
ocgcore.new_card(pduel, 89631139, 0, 0, LOCATION_MZONE, 0, 1) # 1 = Face-Up Attack

# 2. Die Pupille scannt den Arbeitsspeicher
buffer = bytearray(4096)
c_buffer = (ctypes.c_byte * 4096).from_buffer(buffer)

logger.info("🔍 Scanne C++ Memory: Spieler 0, Monsterzone 0...")
length = ocgcore.query_card(pduel, 0, LOCATION_MZONE, 0, QUERY_FLAG, ctypes.cast(c_buffer, ctypes.c_void_p), 0)

if length > 0:
    # C++ schreibt die Daten sequentiell in den Puffer, basierend auf den Flags.
    # Länge: 4 Byte (Immer am Anfang)
    # Code: 4 Byte
    # Position/Data: ... Das C++ Struct schreibt Code, ATK und DEF.
    
    # Vereinfachtes Entpacken für unser Beispiel:
    # 0-4: Struct Length, 4-8: Card ID, 8-12: ATK, 12-16: DEF
    data_len = struct.unpack_from("I", buffer, 0)[0]
    card_id = struct.unpack_from("I", buffer, 4)[0]
    
    # Da die Engine je nach Version dynamisch packt, zeigen wir den rohen Memory an:
    logger.success(f"👁️ PUPILLE HAT ETWAS GESEHEN! Bytes gelesen: {length}")
    logger.success(f"🎴 Erkannte Card ID: {card_id}")
    
    # ATK/DEF aus den restlichen Bytes (Grob-Indexierung)
    atk = struct.unpack_from("I", buffer, 8)[0]
    def_val = struct.unpack_from("I", buffer, 12)[0]
    logger.success(f"⚔️ Erkannte Werte im RAM -> ATK: {atk}, DEF: {def_val}")
else:
    logger.error("Keine Karte gefunden!")
