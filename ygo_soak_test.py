import ctypes
import os
import random
import torch
import torch.nn as nn
from torch.distributions import Categorical
from loguru import logger
from ygo_byte_parser import YGOByteParser

logger.remove()
logger.add("soak_test.log", format="{time:HH:mm:ss} | {level} | {message}")

class YGOOuroborosBrain(nn.Module):
    def __init__(self):
        super().__init__()
        self.network = nn.Sequential(
            nn.Linear(1, 64),
            nn.ReLU(),
            nn.Linear(64, 64),
            nn.ReLU(),
            nn.Linear(64, 20)
        )
        self.optimizer = torch.optim.Adam(self.parameters(), lr=0.005)
        
    def act(self, legal_actions):
        num_options = len(legal_actions)
        if num_options == 0: return 0, None
        state = torch.tensor([float(num_options)])
        logits = self.network(state)
        mask = torch.full((20,), float('-inf'))
        mask[:num_options] = 0
        probs = torch.softmax(logits + mask, dim=-1)
        m = Categorical(probs)
        action_idx = m.sample()
        return action_idx.item(), m.log_prob(action_idx)

# --- C++ BindINGS ---
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

def script_reader_cb(name, plen): plen[0] = 0; return 0
def card_reader_cb(code, pdata): data = pdata.contents; data.code = code; data.type = 17; data.attack = 3000; return code

cb_card = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(CardData))(card_reader_cb)
cb_script = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int32))(script_reader_cb)
cb_msg = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32)(lambda p, m: 0)

ocgcore.set_card_reader(cb_card)
ocgcore.set_script_reader(cb_script)
ocgcore.set_message_handler(cb_msg)

brain = YGOOuroborosBrain()
parser = YGOByteParser()

logger.info("🔥 MASSIVE SOAK TEST STARTED (5000 EPISODES) 🔥")

for episode in range(1, 5001):
    pduel = ocgcore.create_duel(random.randint(1000, 99999))
    ocgcore.set_player_info(pduel, 0, 8000, 5, 1)
    ocgcore.set_player_info(pduel, 1, 8000, 5, 1)
    
    # Beide laden das Alien-Deck
    for i in range(40): ocgcore.new_card(pduel, 89631139, 0, 0, 1, i, 8)
    for i in range(40): ocgcore.new_card(pduel, 89631139, 1, 1, 1, i, 8)
    ocgcore.start_duel(pduel, 0)
    
    msg_buffer = bytearray(4096)
    c_buffer = (ctypes.c_byte * 4096).from_buffer(msg_buffer)
    
    log_probs = []
    
    for step in range(100): # Längere Duelle
        status = ocgcore.process(pduel)
        if status == 0: break # Duell vorbei
            
        msg_len = ocgcore.get_message(pduel, ctypes.cast(c_buffer, ctypes.c_void_p))
        if msg_len > 0:
            events = parser.parse_buffer(msg_buffer, msg_len)
            action_sent = False
            for event in events:
                if "legal_actions" in event and len(event["legal_actions"]) > 0:
                    player_turn = event.get("player", 0)
                    
                    if player_turn == 0:
                        # PLAYER 0 = LGNN (Das lernende Gehirn)
                        action_idx, log_prob = brain.act(event["legal_actions"])
                        if log_prob is not None: log_probs.append(log_prob)
                    else:
                        # PLAYER 1 = CHAOS AGENT (Der Random-Gegner)
                        # Um zu verhindern, dass die KI nur Mirror-Matches lernt,
                        # macht Player 1 einfach zufällige Züge. Das härtet das LGNN ab!
                        action_idx = random.randint(0, len(event["legal_actions"]) - 1)
                        
                    ocgcore.set_responsei(pduel, action_idx)
                    action_sent = True
                    break
                    
            msg_type = msg_buffer[0]
            if not action_sent and msg_type in [10, 13, 14, 15, 16, 17, 18, 19]:
                ocgcore.set_responsei(pduel, 0)
                
    # Reward (Dummy für den Soak Test: Überleben/Länge des Duells belohnen)
    reward = torch.tensor(1.0 if step > 30 else -1.0)
    loss = 0
    for lp in log_probs: loss -= lp * reward
    if log_probs:
        brain.optimizer.zero_grad()
        loss.backward()
        brain.optimizer.step()
        
    if episode % 100 == 0:
        logger.info(f"🔄 Episode {episode}/5000 | Loss: {loss.item():.4f} | Weights saved.")
        torch.save(brain.state_dict(), "lgnn_pilot_weights.pth")

logger.info("✅ 5000 EPISODES COMPLETE. SOAK TEST DONE.")
