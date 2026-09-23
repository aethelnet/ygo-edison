import ctypes
import os
import struct
import torch
import torch.nn as nn
from loguru import logger

# --- 1. DAS GEHIRN (LGNN) ---
class YGOPlayerLGNN(nn.Module):
    def __init__(self, vocab_size=100000000, embedding_dim=32):
        super().__init__()
        # Wir betten die 8-stelligen Karten-IDs (Passcodes) ein
        self.embedding = nn.EmbeddingBag(vocab_size, embedding_dim, mode='mean')
        self.decision_layer = nn.Sequential(
            nn.Linear(embedding_dim, 16),
            nn.ReLU(),
            nn.Linear(16, 1) # Gibt einen "Q-Value" (Siegeswahrscheinlichkeit) für die Aktion aus
        )
        
    def choose_action(self, legal_actions):
        """Bewertet alle legalen Aktionen und wählt den Index mit dem höchsten Score."""
        scores = []
        for action in legal_actions:
            # Wir extrahieren die ID der Karte. Bei Phasenwechsel nehmen wir ID 0.
            card_id = action.get("card_id", 0)
            
            # Tensor-Mathematik: Werte die Aktion aus
            tensor_id = torch.tensor([[card_id % 100000000]]) # Modulo für Sicherheit
            emb = self.embedding(tensor_id)
            score = self.decision_layer(emb).item()
            scores.append(score)
            
            logger.debug(f"LGNN bewertet {action['type']} (Card {card_id}) -> Score: {score:.4f}")
            
        # Nimm den Index mit dem höchsten Score (Argmax)
        best_index = scores.index(max(scores))
        logger.success(f"🧠 LGNN wählt Option {best_index}: {legal_actions[best_index]['type']}!")
        return best_index

# --- 2. DIE ENGINE (Muskeln) ---
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

# Initialisiere KI
ai_player = YGOPlayerLGNN()

print("\n=== THE AWAKENING: LGNN ÜBERNIMMT DAS LENKRAD ===")

for step in range(10):
    status = ocgcore.process(pduel)
    msg_len = ocgcore.get_message(pduel, ctypes.cast(c_buffer, ctypes.c_void_p))
    
    if msg_len > 0:
        msg_type = msg_buffer[0]
        
        # 15 = MSG_SELECT_IDLECMD (Main Phase)
        if msg_type == 15:
            print(f"\n[Zug {step}] ⚔️ MAIN PHASE BLOCKIERT: KI muss eine Aktion wählen!")
            
            # Wir entpacken das C++ Byte-Paket in ein Python Array
            offset = 2
            legal_actions = []
            
            # Normal Summons
            count = struct.unpack_from("B", msg_buffer, offset)[0]; offset += 1
            for _ in range(count):
                code = struct.unpack_from("I", msg_buffer, offset)[0]; offset += 5
                legal_actions.append({"type": "SUMMON", "card_id": code})
                
            # SpSummon, Repos, MSet, SSet, Activate skippen wir hier im Mock für die Übersicht
            # Wir springen direkt ans Ende des Pakets (To BP, To EP)
            # Normalerweise macht unser Byte-Parser das dynamisch. 
            legal_actions.append({"type": "PHASE_CHANGE", "target": "BATTLE_PHASE"})
            legal_actions.append({"type": "PHASE_CHANGE", "target": "END_PHASE"})
            
            print(f"Gefundene Optionen im C++ Memory:")
            for idx, act in enumerate(legal_actions):
                print(f"  Option {idx}: {act['type']} (Card: {act.get('card_id', 'N/A')})")
            
            # HIER KOMMT DAS GEHIRN ZUM EINSATZ
            best_idx = ai_player.choose_action(legal_actions)
            
            # Zurück in die C++ Engine
            ocgcore.set_responsei(pduel, best_idx)
            
        elif msg_type in [10, 12, 13, 14, 16, 17, 18]:
            # Fallback für andere blockierende Nachrichten
            ocgcore.set_responsei(pduel, 0)

print("=== LOOP BEENDET ===")
