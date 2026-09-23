import os
import sys
import time
import random
import ctypes
import struct
import torch
import torch.nn as nn
from torch.distributions import Categorical
from loguru import logger
from ygo_sqlite_bridge import YGOSqliteBridge
from ygo_byte_parser import YGOByteParser

logger.remove()
logger.add("self_play_training.log", rotation="20 MB", format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}")
logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")

core_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "ygopro-core/bin/Release/libocgcore.so"))
script_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "script"))

ocgcore = ctypes.cdll.LoadLibrary(core_path)

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
    if not name_bytes:
        plen[0] = 0
        return 0
    name_str = ctypes.string_at(name_bytes).decode('utf-8', errors='ignore')
    base_name = os.path.basename(name_str)
    file_path = os.path.join(script_dir, base_name)
    if os.path.exists(file_path):
        with open(file_path, 'rb') as f:
            content = f.read()
        c_buf = (ctypes.c_char * len(content)).from_buffer_copy(content)
        _buffers.append(c_buf)
        plen[0] = len(content)
        return ctypes.cast(c_buf, ctypes.c_void_p).value
    plen[0] = 0
    return 0

def card_reader_cb(code, pdata):
    ctypes.memset(pdata, 0, 72) # sizeof CardData
    pdata.contents.code = code
    info = bridge.card_metadata.get(code)
    if info:
        pdata.contents.type = info.get("type", 17)
        pdata.contents.attack = max(0, info.get("atk", 0))
        pdata.contents.defense = max(0, info.get("def", 0))
        pdata.contents.level = max(1, min(12, info.get("level", 4)))
        pdata.contents.race = info.get("race", 1)
        pdata.contents.attribute = info.get("attribute", 0)
        return 1
    else:
        pdata.contents.type = 17
        pdata.contents.attack = 1500
        pdata.contents.defense = 1500
        pdata.contents.level = 4
        pdata.contents.race = 1
        pdata.contents.attribute = 1
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
ocgcore.set_responsei.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
ocgcore.set_script_reader.argtypes = [ctypes.c_void_p]
ocgcore.set_card_reader.argtypes = [ctypes.c_void_p]
ocgcore.set_message_handler.argtypes = [ctypes.c_void_p]

# --- EVOLUTIONARY GENETIC DECKBUILDER WITH CURVE BALANCE ---
class EvolutionaryDeckEngine(nn.Module):
    def __init__(self, total_cards):
        super().__init__()
        initial_priors = torch.ones(total_cards)
        
        self.lvl4_indices = []
        self.boss_indices = []
        self.spell_indices = []
        self.extra_indices = []
        
        for idx, card in enumerate(bridge.cards):
            desc = (card.get("desc") or "").lower()
            atk = card.get("atk", 0)
            lvl = card.get("level", 4)
            ctype = card.get("type", 17)
            
            if ctype & (0x40 | 0x2000 | 0x800000 | 0x4000000): # Fusion, Synchro, Xyz, Link
                self.extra_indices.append(idx)
                if atk >= 2500: initial_priors[idx] += 1.0
            elif ctype & 0x1: # Monster
                if lvl <= 4:
                    self.lvl4_indices.append(idx)
                    if atk >= 1800: initial_priors[idx] += 1.0
                else:
                    self.boss_indices.append(idx)
                    if atk >= 2800: initial_priors[idx] += 1.0
            else:
                self.spell_indices.append(idx)
                if "destroy" in desc or "negate" in desc: initial_priors[idx] += 0.8
            
        self.card_fitness = nn.Parameter(initial_priors)
        self.optimizer = torch.optim.Adam([self.card_fitness], lr=0.03)

    def generate_deck_indices(self, total_cards, deck_size=40, mutation_noise=0.15):
        noise = torch.randn(total_cards) * mutation_noise
        noisy_fitness = self.card_fitness + noise
        
        # 1. 24x Level 1-4 Monsters
        lvl4_fit = noisy_fitness[self.lvl4_indices]
        top_lvl4 = [self.lvl4_indices[i] for i in torch.topk(lvl4_fit, min(24, len(self.lvl4_indices))).indices.tolist()]
        
        # 2. 6x Level 5-8 Boss Finishers
        boss_fit = noisy_fitness[self.boss_indices]
        top_boss = [self.boss_indices[i] for i in torch.topk(boss_fit, min(6, len(self.boss_indices))).indices.tolist()]
        
        # 3. 10x Spells / Power Traps
        spell_fit = noisy_fitness[self.spell_indices]
        top_spell = [self.spell_indices[i] for i in torch.topk(spell_fit, min(10, len(self.spell_indices))).indices.tolist()]
        
        main_deck = top_lvl4 + top_boss + top_spell
        random.shuffle(main_deck)
        
        # 4. 15x Extra Deck cards
        extra_fit = noisy_fitness[self.extra_indices]
        extra_deck = [self.extra_indices[i] for i in torch.topk(extra_fit, min(15, len(self.extra_indices))).indices.tolist()]
        
        return main_deck, extra_deck

    def apply_match_feedback(self, deck_indices, won):
        self.optimizer.zero_grad()
        loss = 0
        if won:
            for idx in deck_indices:
                loss -= self.card_fitness[idx]
        else:
            for idx in deck_indices:
                loss += self.card_fitness[idx] * 1.5
        loss.backward()
        self.optimizer.step()
        with torch.no_grad():
            self.card_fitness.clamp_(min=0.01)

    def export_champion_deck(self, filepath, deck_size=40):
        main_deck, extra_deck = self.generate_deck_indices(len(bridge.cards), deck_size, mutation_noise=0.0)
        
        with open(filepath, 'w') as f:
            f.write('#created by Auratic Prime Evolution\n#main\n')
            for idx in main_deck:
                f.write(f'{bridge.get_card_id(idx)}\n')
            f.write('#extra\n')
            for idx in extra_deck:
                f.write(f'{bridge.get_card_id(idx)}\n')
            f.write('!side\n')
            
        top_cards = []
        for idx in main_deck[:5]:
            info = bridge.get_card_info(idx)
            top_cards.append(f'{info["name"]} (ATK: {info["atk"]})')
        return main_deck + extra_deck, top_cards

# --- ALPHAZERO ACTOR-CRITIC COMBAT ENGINE ---
import math
class LGNNDynamics(nn.Module):
    def __init__(self, latent_dim, action_dim):
        super().__init__()
        self.transition = nn.Sequential(
            nn.Linear(latent_dim + action_dim, latent_dim),
            nn.LayerNorm(latent_dim),
            nn.GELU(),
            nn.Linear(latent_dim, latent_dim)
        )
    def forward(self, latent_state, action_onehot):
        x = torch.cat([latent_state, action_onehot], dim=-1)
        return latent_state + self.transition(x)

class YGOAlphaZeroBrain(nn.Module):
    def __init__(self, state_dim=16, action_dim=32, latent_dim=32):
        super().__init__()
        self.action_dim = action_dim
        self.trunk = nn.Sequential(
            nn.Linear(state_dim, 128),
            nn.LayerNorm(128),
            nn.GELU(),
            nn.Linear(128, latent_dim),
            nn.LayerNorm(latent_dim),
            nn.GELU()
        )
        self.dynamics = LGNNDynamics(latent_dim, action_dim)
        
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
        self.optimizer = torch.optim.Adam(self.parameters(), lr=0.0003, weight_decay=1e-5)

    def extract_latent(self, state_tensor):
        with torch.no_grad():
            return self.trunk(state_tensor)

    def forward(self, state_tensor):
        latent = self.trunk(state_tensor)
        logits = self.policy_head(latent)
        value = torch.tanh(self.value_head(latent))
        return logits, value, latent
        
    def latent_mcts(self, initial_latent, num_actions, num_simulations=15):
        Q_values = torch.zeros(num_actions)
        N_visits = torch.zeros(num_actions)
        
        prior_logits = self.policy_head(initial_latent).squeeze()
        prior_probs = torch.softmax(prior_logits[:num_actions], dim=-1)
        
        for _ in range(num_simulations):
            # Selection (PUCT)
            U = 1.0 * prior_probs * (math.sqrt(max(1, N_visits.sum().item())) / (1 + N_visits))
            scores = Q_values + U
            action = torch.argmax(scores).item()
            
            # Expansion & Simulation (in Latent Space)
            action_onehot = torch.zeros(self.action_dim)
            action_onehot[action] = 1.0
            next_latent = self.dynamics(initial_latent, action_onehot)
            
            predicted_value = torch.tanh(self.value_head(next_latent))
            v = predicted_value.item()
            
            # Backpropagation
            Q_values[action] = (Q_values[action] * N_visits[action] + v) / (N_visits[action] + 1)
            N_visits[action] += 1
            
        return N_visits / num_simulations # Improved policy based on visit counts

    def act(self, state_tensor, num_actions, use_mcts=True):
        logits, value, latent = self.forward(state_tensor)
        
        if use_mcts and num_actions > 1:
            improved_probs = self.latent_mcts(latent, num_actions)
            probs = improved_probs
        else:
            mask = torch.full((32,), float('-inf'))
            mask[:num_actions] = 0
            probs = torch.softmax(logits + mask, dim=-1)
            
        if torch.isnan(probs).any() or probs.sum() == 0:
            probs = torch.full((32,), 1.0 / num_actions)
            probs[num_actions:] = 0
            
        dist = Categorical(probs[:num_actions])
        action = dist.sample()
        return action.item(), dist.log_prob(action), dist.entropy(), value

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
    elif mtype in [12, 13]:
        if atype in ["YES", "EFFECT_YES"]: return 1
        elif atype in ["NO", "EFFECT_NO"]: return 0
        return sub_idx
    elif mtype == 14:
        return sub_idx
    elif mtype == 16:
        if atype == "CANCEL_CHAIN": return -1
        return sub_idx
    elif mtype == 19:
        if atype == "POS_FACEUP_ATTACK": return 1
        elif atype == "POS_FACEDOWN_ATTACK": return 2
        elif atype == "POS_FACEUP_DEFENSE": return 4
        elif atype == "POS_FACEDOWN_DEFENSE": return 8
        return sub_idx
    elif atype == "CANCEL_CHAIN":
        return -1
    elif atype == "POS_FACEUP_ATTACK":
        return 1
    elif atype == "POS_FACEUP_DEFENSE":
        return 4
    else:
        return choice_idx

def run_overnight_self_play(max_episodes=50000):
    logger.info("⚔️ [PHASE 40] STARTING CONTINUOUS ALPHAZERO SELF-PLAY + CURVED DECK EVOLUTION ⚔️")
    logger.info(f"🎯 Target: {max_episodes} Episodes | State Dim: 16 | Latent Dim: 32")

    os.makedirs("evolved_decks", exist_ok=True)

    total_cards = len(bridge.cards)
    deck_engine = EvolutionaryDeckEngine(total_cards)

    learning_agent = YGOAlphaZeroBrain()
    frozen_opponent = YGOAlphaZeroBrain()

    if os.path.exists("lgnn_grandmaster.pth"):
        try:
            learning_agent.load_state_dict(torch.load("lgnn_grandmaster.pth"), strict=False)
            logger.success("📂 Bestehendes Grandmaster-Modell geladen!")
        except Exception as e:
            logger.warning(f"Modell-Neustart: {e}")

    frozen_opponent.load_state_dict(learning_agent.state_dict())
    frozen_opponent.eval()

    parser = YGOByteParser()
    buf = bytearray(8192)
    c_buf = (ctypes.c_byte * 8192).from_buffer(buf)

    p0_wins = 0
    p1_wins = 0
    total_steps = 0
    start_time = time.time()
    gen_counter = 4

    m_p0, e_p0 = deck_engine.generate_deck_indices(total_cards, 40, mutation_noise=0.0)
    active_deck_p0_indices = m_p0 + e_p0
    m_p1, e_p1 = deck_engine.generate_deck_indices(total_cards, 40, mutation_noise=0.1)
    active_deck_p1_indices = m_p1 + e_p1

    for episode in range(1, max_episodes + 1):
        if episode % 25 == 0:
            m_p0, e_p0 = deck_engine.generate_deck_indices(total_cards, 40, mutation_noise=0.05)
            active_deck_p0_indices = m_p0 + e_p0
            m_p1, e_p1 = deck_engine.generate_deck_indices(total_cards, 40, mutation_noise=0.15)
            active_deck_p1_indices = m_p1 + e_p1

        m_p0_cids = [bridge.get_card_id(i) for i in m_p0]
        e_p0_cids = [bridge.get_card_id(i) for i in e_p0]
        m_p1_cids = [bridge.get_card_id(i) for i in m_p1]
        e_p1_cids = [bridge.get_card_id(i) for i in e_p1]

        pduel = ocgcore.create_duel(random.randint(100000, 999999))
        
        # Randomize who goes first
        learning_agent_player = random.randint(0, 1)
        
        ocgcore.set_player_info(pduel, 0, 8000, 5, 1)
        ocgcore.set_player_info(pduel, 1, 8000, 5, 1)

        for i, cid in enumerate(m_p0_cids): ocgcore.new_card(pduel, cid, 0, 0, 1, i, 8)
        for i, cid in enumerate(e_p0_cids): ocgcore.new_card(pduel, cid, 0, 0, 64, i, 8)
        for i, cid in enumerate(m_p1_cids): ocgcore.new_card(pduel, cid, 1, 1, 1, i, 8)
        for i, cid in enumerate(e_p1_cids): ocgcore.new_card(pduel, cid, 1, 1, 64, i, 8)


        ocgcore.start_duel(pduel, 0)

        ep_log_probs = []
        ep_values = []
        ep_entropies = []
        ep_rewards = []
        current_step_reward = 0.0
        
        lp = {0: 8000, 1: 8000}
        turn_count = 0
        step_count = 0
        duel_winner = -1
        p0_monsters = 0
        p1_monsters = 0

        while step_count < 150:
            step_count += 1
            total_steps += 1
            status = ocgcore.process(pduel)
            mlen = ocgcore.get_message(pduel, ctypes.cast(c_buf, ctypes.c_void_p))

            if mlen > 0:
                chunk = bytes(buf[:mlen])
                mtype = chunk[0]
                
                if mtype == 40:
                    turn_count += 1
                elif mtype == 91:
                    p = chunk[1]
                    dmg = struct.unpack("<I", chunk[2:6])[0]
                    lp[p] = max(0, lp[p] - dmg)
                    # Positive reward if enemy took damage, negative if we took damage
                    current_step_reward += (dmg / 8000.0) if p != learning_agent_player else -(dmg / 8000.0)
                    if lp[p] == 0:
                        duel_winner = 1 - p

                elif mtype == 18:
                    p = chunk[1]
                    seq = max(0, min(4, p0_monsters if p == 0 else p1_monsters))
                    resp_place = (ctypes.c_byte * 3)(p, 4, seq)
                    ocgcore.set_responseb(pduel, ctypes.cast(resp_place, ctypes.c_void_p))
                    continue

                elif mtype == 16:
                    ocgcore.set_responsei(pduel, -1)
                    continue

                elif mtype == 19:
                    ocgcore.set_responsei(pduel, 1)
                    continue
                elif mtype in [140, 141]:
                    # MSG_ANNOUNCE_RACE or ATTRIB
                    flag = struct.unpack("<I", chunk[3:7])[0]
                    ocgcore.set_responsei(pduel, flag & -flag)
                    continue
                elif mtype == 142:
                    # MSG_ANNOUNCE_CARD
                    val = struct.unpack("<I", chunk[3:7])[0]
                    ocgcore.set_responsei(pduel, val)
                    continue
                elif mtype == 143:
                    # MSG_ANNOUNCE_NUMBER
                    ocgcore.set_responsei(pduel, 0)
                    continue

                elif mtype in [10, 11, 12, 13, 14, 15, 20]:
                    parser.buffer = chunk
                    parser.offset = 1
                    
                    ev = None
                    if mtype == 11: ev = parser.parse_idlecmd()
                    elif mtype == 10: ev = parser.parse_battlecmd()
                    elif mtype == 14: ev = parser.parse_select_option()
                    elif mtype == 15: ev = parser.parse_select_card()
                    elif mtype in [12, 13]: ev = parser.parse_select_yesno()

                    if ev and "legal_actions" in ev and len(ev["legal_actions"]) > 0:
                        player_turn = ev.get("player", 0)
                        actions = ev["legal_actions"]
                        num_act = min(len(actions), 32)

                        state = torch.tensor([
                            float(num_act) / 32.0, float(lp[0]) / 8000.0, float(lp[1]) / 8000.0,
                            float(turn_count) / 20.0, float(mtype) / 20.0,
                            1.0 if player_turn == 0 else 0.0,
                            1.0 if player_turn == 1 else 0.0,
                            1.0 if mtype == 11 else 0.0,
                            1.0 if mtype == 10 else 0.0,
                            0.0, float(step_count) / 100.0,
                            0.5, 0.5, 0.5, 0.5, 1.0
                        ], dtype=torch.float32)

                        if player_turn == 0:
                            # 🤖 LEARNING AGENT
                            act_idx, log_prob, entropy, val = learning_agent.act(state, num_act)
                            ep_log_probs.append(log_prob)
                            ep_values.append(val)
                            ep_entropies.append(entropy)
                            ep_rewards.append(current_step_reward)
                            current_step_reward = 0.0
                            
                            chosen_act = actions[act_idx]
                            if chosen_act.get("type") == "SUMMON": p0_monsters += 1
                            if mtype == 15:
                                min_cards = chosen_act.get('min', 1)
                                resp_arr = bytearray(64)
                                resp_arr[0] = min_cards
                                for j in range(min_cards):
                                    resp_arr[1 + j] = (act_idx + j) % num_act
                                ocgcore.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_arr), 64))
                            else:
                                resp = action_to_response_code(chosen_act, act_idx, mtype)
                                ocgcore.set_responsei(pduel, resp)
                        else:
                            # 🧊 FROZEN OPPONENT
                            with torch.no_grad():
                                act_idx, _, _, _ = frozen_opponent.act(state, num_act)
                            chosen_act = actions[act_idx]
                            if chosen_act.get("type") == "SUMMON": p1_monsters += 1
                            if mtype == 15:
                                min_cards = chosen_act.get('min', 1)
                                resp_arr = bytearray(64)
                                resp_arr[0] = min_cards
                                for j in range(min_cards):
                                    resp_arr[1 + j] = (act_idx + j) % num_act
                                ocgcore.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_arr), 64))
                            else:
                                resp = action_to_response_code(chosen_act, act_idx, mtype)
                                ocgcore.set_responsei(pduel, resp)
                    else:
                        if mtype == 11: ocgcore.set_responsei(pduel, 7)
                        elif mtype == 16: ocgcore.set_responsei(pduel, -1)
                        else: ocgcore.set_responsei(pduel, 0)

            if status == 0 or duel_winner != -1:
                break

        # Duel Outcome with Aggression & Damage Credit
        damage_dealt_p0 = 8000 - lp[1]
        damage_dealt_p1 = 8000 - lp[0]
        
        if duel_winner == 0:
            final_reward = 1.0 + (damage_dealt_p0 / 8000.0)
            p0_wins += 1
            deck_engine.apply_match_feedback(active_deck_p0_indices, won=True)
            deck_engine.apply_match_feedback(active_deck_p1_indices, won=False)
        elif duel_winner == 1:
            final_reward = -1.0 - (damage_dealt_p1 / 8000.0)
            p1_wins += 1
            deck_engine.apply_match_feedback(active_deck_p0_indices, won=False)
            deck_engine.apply_match_feedback(active_deck_p1_indices, won=True)
        else:
            final_reward = (damage_dealt_p0 - damage_dealt_p1) / 8000.0
            if lp[0] > lp[1]: p0_wins += 1
            elif lp[1] > lp[0]: p1_wins += 1

        # Train Actor-Critic (Advantage Policy Gradient)
        if ep_log_probs:
            # Adjust final reward to the perspective of the learning agent
            if learning_agent_player == 1:
                final_reward = -final_reward
                
            policy_loss = 0; value_loss = 0; entropy_loss = 0
            returns = []
            G = final_reward
            for step_r in reversed(ep_rewards):
                G = step_r + 0.99 * G
                returns.insert(0, G)
            returns_tensor = torch.tensor(returns, dtype=torch.float32)
            for log_p, val, ent, ret in zip(ep_log_probs, ep_values, ep_entropies, returns_tensor):
                advantage = ret - val.item()
                policy_loss -= log_p * advantage
                value_loss += nn.functional.mse_loss(val.squeeze(), ret)
                entropy_loss -= 0.01 * ent
                
            total_loss = (policy_loss + 0.5 * value_loss + entropy_loss) / len(ep_log_probs)
            learning_agent.optimizer.zero_grad()
            total_loss.backward()
            torch.nn.utils.clip_grad_norm_(learning_agent.parameters(), 1.0)
            learning_agent.optimizer.step()

        # Checkpoints, AlphaZero Cloning & Automatic Deck Evolution
        if episode % 50 == 0:
            win_rate = (p0_wins / 50.0) * 100.0
            elapsed = time.time() - start_time
            speed = total_steps / max(1e-5, elapsed)
            
            logger.info(f"📊 Episode {episode:05d} | Win Rate: {win_rate:5.1f}% | Total Steps: {total_steps} | Speed: {speed:5.1f} steps/s")
            
            torch.save(learning_agent.state_dict(), "lgnn_grandmaster.pth")
            
            dummy_state = torch.ones((1, 16), dtype=torch.float32)
            combat_tensor = learning_agent.extract_latent(dummy_state)
            torch.save(combat_tensor, "combat_latent_tensor.pt")
            
            if win_rate >= 50.0:
                logger.success(f"🧬 [CLONE EVENT]: Learning Agent triumphiert ({win_rate:.1f}%)! Klone Gewichte...")
                frozen_opponent.load_state_dict(learning_agent.state_dict())

            p0_wins = 0; p1_wins = 0

        # Export Brand-New Evolved Deck Generation every 500 episodes
        if episode % 500 == 0:
            gen_counter += 1
            gen_deck_path = f"evolved_decks/Auratic_Gen{gen_counter}_Evolved.ydk"
            top_idx, top_cards = deck_engine.export_champion_deck(gen_deck_path, 40)
            deck_engine.export_champion_deck("Auratic_Gen2_Evolved.ydk", 40)
            
            logger.success(f"🎴 [DECK EVOLUTION]: Neue Deck-Generation {gen_counter} generiert! ({gen_deck_path})")
            logger.info(f"   Top 5 Synergie-Karten: {', '.join(top_cards)}")

    logger.success("🏆 OVERNIGHT SELF-PLAY + DECK EVOLUTION TRAINING ABGESCHLOSSEN!")

if __name__ == "__main__":
    run_overnight_self_play(50000)
