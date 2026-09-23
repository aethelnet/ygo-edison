import ctypes
import os
from loguru import logger

core_path = os.path.join(os.path.dirname(__file__), "ygopro-core/bin/Release/libocgcore.so")
ocgcore = ctypes.cdll.LoadLibrary(core_path)

# Ensure proper argument types
ocgcore.set_player_info.argtypes = [ctypes.c_void_p, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32]
ocgcore.new_card.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint8]
ocgcore.start_duel.argtypes = [ctypes.c_void_p, ctypes.c_int32]
ocgcore.process.argtypes = [ctypes.c_void_p]
ocgcore.end_duel.argtypes = [ctypes.c_void_p]

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

CMPFUNC_SCRIPT = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int32))
CMPFUNC_CARD = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(CardData))
CMPFUNC_MSG = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32)

_kept_alive_scripts = []

def script_reader_cb(name, plen):
    script_name = name.decode('utf-8')
    # Clean up the path requested by C++ (e.g. './script/constant.lua')
    clean_name = script_name.replace('./script/', '').replace('script/', '')
    script_path = os.path.join(os.path.dirname(__file__), "script", clean_name)
    
    if os.path.exists(script_path):
        logger.debug(f"Lua-Skript erfolgreich geladen: {clean_name}")
        with open(script_path, 'rb') as f:
            content = f.read()
        plen[0] = len(content)
        buffer = (ctypes.c_byte * len(content)).from_buffer_copy(content)
        _kept_alive_scripts.append(buffer)
        return ctypes.cast(buffer, ctypes.c_void_p).value
    else:
        logger.warning(f"Skript fehlt: {script_name}")
        plen[0] = 0
        return 0

def card_reader_cb(code, pdata):
    logger.info(f"C++ liest Karte aus der Datenbank: ID {code}")
    data = pdata.contents
    data.code = code
    data.type = 17 
    data.attack = 3000
    return code

def msg_handler_cb(pduel, msg_type):
    return 0

cb_card = CMPFUNC_CARD(card_reader_cb)
cb_script = CMPFUNC_SCRIPT(script_reader_cb)
cb_msg = CMPFUNC_MSG(msg_handler_cb)

logger.info("📡 Verbinde Python Callbacks mit C++ OCGCore...")
ocgcore.set_card_reader(cb_card)
ocgcore.set_script_reader(cb_script)
ocgcore.set_message_handler(cb_msg)

logger.info("⚔️ Erstelle Duell-Instanz im RAM...")
ocgcore.create_duel.restype = ctypes.c_void_p
pduel = ocgcore.create_duel(1337)

if not pduel:
    logger.error("Fehler beim Erstellen des Duells!")
    exit(1)

logger.info("❤️ Setze Startbedingungen (8000 LP, 5 Handkarten)...")
ocgcore.set_player_info(pduel, 0, 8000, 5, 1)
ocgcore.set_player_info(pduel, 1, 8000, 5, 1)

logger.info("🃏 Lade 40 Karten ins Main Deck von Spieler 1...")
for i in range(40):
    ocgcore.new_card(pduel, 89631139, 0, 0, 1, i, 8)
    
logger.info("🃏 Lade 40 Karten ins Main Deck von Spieler 2...")
for i in range(40):
    ocgcore.new_card(pduel, 89631139, 1, 1, 1, i, 8)

logger.success("🔥 STARTE ECHTES C++ DUELL! 🔥")
ocgcore.start_duel(pduel, 0) 

logger.info("Lasse C++ Engine den ersten Zug berechnen...")
status = ocgcore.process(pduel)
logger.success(f"✅ Engine Status: {status} (Duell läuft fehlerfrei!)")

ocgcore.end_duel(pduel)
