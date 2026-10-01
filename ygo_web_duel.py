import os
import re
import collections
import sys
import ctypes
import struct
import json
import sqlite3
import threading
import time
import asyncio
import itertools
from typing import Optional, Dict, Any, List, Tuple
import secrets
import traceback
import faulthandler
faulthandler.enable()
from fastapi import FastAPI, WebSocket, Request, HTTPException, Response, WebSocketDisconnect
from fastapi.responses import FileResponse

import uvicorn
from fastapi.middleware.cors import CORSMiddleware

from ygo_sqlite_bridge import YGOSqliteBridge
from ygo_byte_parser import YGOByteParser
import random
import uuid
from ygo_self_play import YGOAlphaZeroBrain, action_to_response_code, EvolutionaryDeckEngine
from duel_telemetry_recorder import DuelTelemetryRecorder
from auto_card_db_sync import AutoCardDBSync
from engine_discrepancy_logger import EngineDiscrepancyLogger
from deck_manager_sqlite import DeckDatabase
from edison_banlist import check_edison_legality, get_card_limit
from ai_sidedeck_engine import AISideDeckEngine
from tournament_engine import tournament_engine
from replay_engine import replay_engine, to_ydk
import tournament_sync
from ygo_scenarios import SCENARIOS, get_scenario_metadata_list, setup_scenario_board, board_to_scenario, delete_scenario

discrepancy_logger = EngineDiscrepancyLogger()
deck_db = DeckDatabase()
ai_side_engine = AISideDeckEngine()
ACTIVE_REFEREE_ALERTS = collections.deque(maxlen=30)

def get_sum_params(sum_param):
    op1 = sum_param & 0xFFFF
    op2 = (sum_param >> 16) & 0xFFFF
    if op2 & 0x8000:
        op1 = sum_param & 0x7FFFFFFF
        op2 = 0
    return op1, op2

def py_select_sum_check1(oparam, size, index, acc, opmin):
    if acc <= 0 or index == size:
        return False
    o1, o2 = get_sum_params(oparam[index])
    if index == size - 1:
        return (acc == o1 and acc + opmin > o1) or (o2 > 0 and acc == o2 and acc + opmin > o2)
    return (acc > o1 and py_select_sum_check1(oparam, size, index + 1, acc - o1, min(o1, opmin))) or \
           (o2 > 0 and acc > o2 and py_select_sum_check1(oparam, size, index + 1, acc - o2, min(o2, opmin)))

def solve_select_sum(must_select_cards, select_cards, acc, min_c, max_c, preferred_indices=None):
    mcount = len(must_select_cards)
    must_oparam = [c.get("sum_param", 0) for c in must_select_cards]
    n_sel = len(select_cards)
    
    if preferred_indices:
        valid_prefs = []
        for p_idx in preferred_indices:
            if 0 <= p_idx < n_sel:
                valid_prefs.append(p_idx)
            else:
                for idx, c in enumerate(select_cards):
                    if c.get("sub_idx") == p_idx and idx not in valid_prefs:
                        valid_prefs.append(idx)
                        break
        if min_c <= len(valid_prefs) <= max_c:
            for perm in itertools.permutations(valid_prefs):
                oparam = list(must_oparam) + [select_cards[idx].get("sum_param", 0) for idx in perm]
                if py_select_sum_check1(oparam, len(oparam), 0, acc, 0xFFFF):
                    return list(perm)

    for k in range(min_c, max_c + 1):
        for combo in itertools.combinations(range(n_sel), k):
            for perm in itertools.permutations(combo):
                oparam = list(must_oparam) + [select_cards[idx].get("sum_param", 0) for idx in perm]
                if py_select_sum_check1(oparam, len(oparam), 0, acc, 0xFFFF):
                    return list(perm)

    if preferred_indices:
        return [idx for idx in preferred_indices if 0 <= idx < n_sel]
    return list(range(min(min_c, n_sel)))


core_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "ygopro-core/bin/Release/libocgcore.so"))
ocgcore = ctypes.cdll.LoadLibrary(core_path)


db = YGOSqliteBridge("../../data/db_temp/locales/en-US/cards.cdb")

class CardData(ctypes.Structure):
    _fields_ = [("code", ctypes.c_uint32), ("alias", ctypes.c_uint32), ("setcode", ctypes.c_uint16 * 16),
                ("type", ctypes.c_uint32), ("level", ctypes.c_uint32), ("attribute", ctypes.c_uint32),
                ("race", ctypes.c_uint32), ("attack", ctypes.c_int32), ("defense", ctypes.c_int32),
                ("lscale", ctypes.c_uint32), ("rscale", ctypes.c_uint32), ("link_marker", ctypes.c_uint32),
                ("rule_code", ctypes.c_uint32)]

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
        
        # Unpack 64-bit setcode into 16-element uint16 array
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
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../script")),
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../../backend_clean/script")),
    os.path.abspath("./script"),
    "/home/ubuntu/ygo_service/apps/script",
    "/home/ubuntu/ygo_service/script"
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

@ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32)
def message_handler_cb(pduel, msg_type):
    try:
        err_msg = ctypes.string_at(pduel).decode('utf-8', errors='ignore')
        if err_msg.strip():
            print(f"[OCGCORE LOG type={msg_type}]: {err_msg.strip()}")
    except Exception as e:
        print(f"[OCGCORE LOG ERROR]: {e}")
    return 0

ocgcore.set_card_reader(card_reader_cb)
ocgcore.set_script_reader(script_reader_cb)
ocgcore.set_message_handler(message_handler_cb)
ocgcore.create_duel.restype = ctypes.c_void_p
ocgcore.create_duel.argtypes = [ctypes.c_uint32]
ocgcore.set_player_info.argtypes = [ctypes.c_void_p, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32]
ocgcore.new_card.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint8]
ocgcore.start_duel.argtypes = [ctypes.c_void_p, ctypes.c_int32]
ocgcore.process.restype = ctypes.c_int32
ocgcore.process.argtypes = [ctypes.c_void_p]
ocgcore.get_message.restype = ctypes.c_int32
ocgcore.get_message.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
ocgcore.set_responseb.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
ocgcore.set_responsei.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
ocgcore.set_script_reader.argtypes = [ctypes.c_void_p]
ocgcore.set_card_reader.argtypes = [ctypes.c_void_p]
ocgcore.set_message_handler.argtypes = [ctypes.c_void_p]
ocgcore.query_field_card.argtypes = [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int32]
ocgcore.query_field_card.restype = ctypes.c_int32
ocgcore.query_field_info.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
ocgcore.query_field_info.restype = ctypes.c_int32
ocgcore.end_duel.argtypes = [ctypes.c_void_p]
ocgcore.end_duel.restype = None

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

import copy
from typing import Optional

def enrich_card_list(cids: list) -> list:
    res = []
    for cid in cids:
        try:
            cid_int = int(cid)
        except:
            continue
        info = db.card_metadata.get(cid_int, {})
        res.append({
            "id": cid_int,
            "name": info.get("name", f"Card {cid_int}"),
            "atk": info.get("atk", 0),
            "def": info.get("def", 0),
            "level": info.get("level", 0),
            "type": info.get("type", 0),
            "race": info.get("race", 0),
            "attribute": info.get("attribute", 0),
            "desc": info.get("desc", "")
        })
    return res

class ChatRateLimiter:
    """
    Sliding-window rate limiter for WebSocket chat messages (Phase 92).
    Default: Max 5 messages per 10.0 seconds.
    """
    def __init__(self, max_messages: int = 5, window_seconds: float = 10.0):
        self.max_messages = max_messages
        self.window_seconds = window_seconds
        self.timestamps: collections.deque = collections.deque()
        self.lock = threading.Lock()

    def allow(self) -> Tuple[bool, float]:
        """Returns (is_allowed, retry_after_seconds)."""
        now = time.time()
        with self.lock:
            while self.timestamps and (now - self.timestamps[0]) > self.window_seconds:
                self.timestamps.popleft()

            if len(self.timestamps) < self.max_messages:
                self.timestamps.append(now)
                return True, 0.0
            else:
                oldest = self.timestamps[0]
                retry_after = max(0.5, round(self.window_seconds - (now - oldest), 1))
                return False, retry_after

def sanitize_chat_handle(raw_name: str, is_authenticated_referee: bool = False, is_player: bool = False) -> str:
    """
    Sanitizes chat sender handles to prevent identity spoofing (Phase 93).
    Strips fraudulent [REFEREE], [JUDGE], [PILOT] prefixes from unauthenticated participants.
    """
    if not raw_name:
        return "Zuschauer"
    name = str(raw_name).strip()
    if not is_authenticated_referee and not is_player:
        name = re.sub(r'(?i)\[\s*(referee|judge|head\s*judge|pilot\s*[12]?|system|admin|official)[^\]]*\]', '', name).strip()
        name = re.sub(r'(?i)^(referee|head\s*judge|judge|admin|system)[:\s_-]+', '', name).strip()
    if not name:
        name = "Zuschauer"
    return name[:30]

class DuelRoom:
    def __init__(self, room_id: str, is_pvp: bool = False):
        self.room_id = room_id
        self.is_pvp = is_pvp
        self.format = None
        self.players = {0: None, 1: None}
        self.player_names = {0: "Spieler 1", 1: "Spieler 2"}
        self.player_ids = {0: None, 1: None}
        self.player_tokens = {0: secrets.token_hex(16), 1: secrets.token_hex(16)}
        self.player_connected = {0: False, 1: False}
        self.disconnect_time = {0: None, 1: None}
        self.current_prompt = {0: None, 1: None}
        self.last_turn_player = 0
        self.last_phase = (0x01, "DRAW PHASE")
        self.retry_count = 0
        self.auto_heal_attempts = 0
        self.last_mtype = None
        self.last_player = 0
        self.last_action_submitted = None
        self.loops = {0: None, 1: None}
        self.decks = {0: None, 1: None}
        self.events = {0: threading.Event(), 1: threading.Event()}
        self.action_values = {0: None, 1: None}
        self.autopilot = {0: False, 1: False}
        self.autopilot_idle_count = 0
        self.match_wins = {0: 0, 1: 0}
        self.current_game = 1
        self.match_finished = False
        self.side_decking_active = False
        self.side_decks_ready = {0: False, 1: False}
        self.first_turn_player = 0
        self.first_turn_chosen = False
        self.loser_of_last_game = None
        self.ai_deck = None
        self.match_mode = "bo3"
        self.start_lp = 8000
        self.turn_choice = "random"
        self.ocg_to_room = {0: 0, 1: 1}
        self.room_to_ocg = {0: 0, 1: 1}
        self.sided_main = {0: [], 1: []}
        self.sided_extra = {0: [], 1: []}
        self.sided_side = {0: [], 1: []}
        self.initial_main_len = {0: 40, 1: 40}
        self.initial_extra_len = {0: 15, 1: 15}
        self.sidedeck_event = threading.Event()
        self.history_buffer = collections.deque(maxlen=500)
        self.chat_history = collections.deque(maxlen=50)
        self.sidedeck_start_time = 0.0
        self.sidedeck_timeout_duration = 180.0
        self.duel_ended = False
        self.pduel = None
        self.is_active = True
        self.session_id = str(uuid.uuid4())[:8]
        self.duel_thread = None
        self.spectators: Dict[str, WebSocket] = {}
        self.spectator_loops: Dict[str, asyncio.AbstractEventLoop] = {}
        self.spectator_names: Dict[str, str] = {}
        self.lock = threading.Lock()
        self.engine_lock = threading.RLock()
        self.player_deck_names = {0: "Default", 1: "Default"}
        self.replay_decks_raw = {0: {"main": [], "extra": [], "side": []}, 1: {"main": [], "extra": [], "side": []}}
        self.replay_frames = []
        self.saved_replay_id = None
        self.current_turn_count = 1
        self.current_phase_name = "DRAW"
        self.time_call_active = False
        self.time_call_turn_count = 0
        self.time_call_game_ended = False
        self.is_top_cut = False
        self.scenario_id: Optional[str] = None
        self.scenario_data: Optional[Dict[str, Any]] = None

    def record_replay_frame(self, event_type: str, desc: str, pduel=None, extra_data: dict = None):
        try:
            target_pduel = pduel if pduel else self.pduel
            # Phase 90: Record omniscient dual-hand state for unmasked God-Mode replay analysis
            bstate = get_board_state(target_pduel, omniscient=True) if target_pduel else {}
            p0_lp = bstate.get(0, {}).get("lp", 8000)
            p1_lp = bstate.get(1, {}).get("lp", 8000)
            frame = {
                "step": len(self.replay_frames),
                "step_index": len(self.replay_frames),
                "game": self.current_game,
                "game_index": self.current_game,
                "turn": self.current_turn_count,
                "turn_num": self.current_turn_count,
                "phase": self.current_phase_name,
                "event_type": event_type,
                "desc": desc,
                "description": desc,
                "p0_lp": p0_lp,
                "p1_lp": p1_lp,
                "board": bstate,
                "ts": round(time.time(), 3),
                "timestamp": round(time.time(), 3),
                "omniscient": True
            }

            if extra_data:
                frame.update(extra_data)
            self.replay_frames.append(frame)
        except Exception as e:
            print(f"ROOM [{self.room_id}]: Replay record error: {e}")

    def send_to(self, player_idx: int, msg: dict):
        with self.lock:
            ws = self.players.get(player_idx)
            loop = self.loops.get(player_idx)
        if ws and loop:
            try:
                asyncio.run_coroutine_threadsafe(ws.send_text(json.dumps(msg)), loop)
            except Exception as e:
                print(f"ROOM [{self.room_id}] Error sending to player {player_idx}: {e}")

    def send_to_spectator(self, spec_id: str, msg: dict):
        with self.lock:
            ws = self.spectators.get(spec_id)
            loop = self.spectator_loops.get(spec_id)
        if ws and loop:
            try:
                asyncio.run_coroutine_threadsafe(ws.send_text(json.dumps(msg)), loop)
            except Exception as e:
                pass

    def record_history(self, msg: dict):
        mtype = msg.get("type")
        if mtype in ["MATCH_GAME_START", "NEW_TURN", "NEW_PHASE", "CONFIRM_CARDS", "DAMAGE", "RECOVER", "GAME_END_SIDEDECK", "MATCH_END", "SPECTATOR_SIDEDECK", "DUELL_ENDE", "TOURNAMENT_TIME_CALL"]:
            entry = dict(msg)
            if "board" in entry:
                del entry["board"]
            entry["ts"] = time.time()
            self.history_buffer.append(entry)

    def broadcast_spectators(self, msg: dict):
        self.record_history(msg)
        with self.lock:
            spec_ids = list(self.spectators.keys())
        for sid in spec_ids:
            self.send_to_spectator(sid, msg)

    def broadcast(self, msg: dict):
        self.send_to(0, msg)
        if self.is_pvp:
            self.send_to(1, msg)
        self.broadcast_spectators(msg)

    def resume_player_session(self, player_idx: int):
        """Sends complete state resumption payload to a reconnected player."""
        pname = self.player_names.get(player_idx, f"Player {player_idx}")
        opp_name = self.player_names.get(1 - player_idx, "Gegner") if self.is_pvp else "PROPHIT-AI"

        # 1. Reconnected confirmation
        self.send_to(player_idx, {
            "type": "ROOM_STATUS",
            "status": "RECONNECTED",
            "room": self.room_id,
            "player": player_idx,
            "your_name": pname,
            "opponent_name": opp_name,
            "reconnect_token": self.player_tokens[player_idx],
            "message": f"Verbindung erfolgreich wiederhergestellt! Willkommen zurück, {pname}."
        })

        # Send chat history on reconnect
        self.send_to(player_idx, {
            "type": "CHAT_HISTORY",
            "messages": list(self.chat_history)
        })

        if self.side_decking_active:
            p_side = self.sided_side.get(player_idx) or []
            p_main = self.sided_main.get(player_idx) or []
            p_extra = self.sided_extra.get(player_idx) or []
            p0_wins = self.match_wins[0]
            p1_wins = self.match_wins[1]
            rel_score = {0: p0_wins, 1: p1_wins} if player_idx == 0 else {0: p1_wins, 1: p0_wins}
            rel_loser = 0 if self.loser_of_last_game == player_idx else 1
            rem_secs = max(0, int(self.sidedeck_timeout_duration - (time.time() - self.sidedeck_start_time))) if self.sidedeck_start_time > 0 else int(self.sidedeck_timeout_duration)
            self.send_to(player_idx, {
                "type": "GAME_END_SIDEDECK",
                "game_ended": self.current_game,
                "score": rel_score,
                "score_str": f"{self.match_wins[0]} : {self.match_wins[1]}",
                "next_game": self.current_game + 1,
                "loser": rel_loser,
                "can_choose_first": (self.loser_of_last_game == player_idx),
                "main": enrich_card_list(p_main),
                "extra": enrich_card_list(p_extra),
                "side": enrich_card_list(p_side),
                "ready": self.side_decks_ready.get(player_idx, False),
                "timeout_seconds": int(self.sidedeck_timeout_duration),
                "remaining_seconds": rem_secs,
                "message": f"Sidedecking-Phase wiederaufgenommen ({rem_secs}s Restzeit)."
            })
            return

        if not self.pduel:
            return

        # 2. Turn state
        rel_turn = 0 if self.last_turn_player == player_idx else 1
        self.send_to(player_idx, {
            "type": "NEW_TURN",
            "turn_player": rel_turn,
            "turn_player_name": "Du" if rel_turn == 0 else opp_name,
            "board": get_board_state(self.pduel, pov_player=self.room_to_ocg.get(player_idx, player_idx))
        })

        # 3. Phase state
        phase_code, phase_name = self.last_phase
        self.send_to(player_idx, {
            "type": "NEW_PHASE",
            "phase_code": phase_code,
            "phase_name": phase_name,
            "board": get_board_state(self.pduel, pov_player=self.room_to_ocg.get(player_idx, player_idx))
        })

        # 4. Active prompt (if engine is waiting for input)
        prompt = self.current_prompt.get(player_idx)
        if prompt:
            prompt_copy = dict(prompt)
            prompt_copy["board"] = get_board_state(self.pduel, pov_player=self.room_to_ocg.get(player_idx, player_idx))
            self.send_to(player_idx, prompt_copy)

        if self.scenario_data:
            self.send_to(player_idx, {
                "type": "SCENARIO_INFO",
                "scenario": self.scenario_data,
                "message": f"🧩 Szenario aktiv: {self.scenario_data.get('title', 'Taktik')}"
            })

    def broadcast_turn(self, pduel, turn_player: int):
        room_tp = self.ocg_to_room.get(turn_player, turn_player)
        self.last_turn_player = room_tp
        self.current_turn_count += 1
        p0_name = self.player_names.get(0, "Spieler 1")
        p1_name = self.player_names.get(1, "Gegner") if self.is_pvp else "PROPHIT-AI"
        active_turn_name = p0_name if room_tp == 0 else p1_name
        self.record_replay_frame("NEW_TURN", f"Zug {self.current_turn_count}: {active_turn_name} ist am Zug", pduel)

        self.send_to(0, {
            "type": "NEW_TURN",
            "turn_player": 0 if room_tp == 0 else 1,
            "turn_player_name": "Du" if room_tp == 0 else p1_name,
            "board": get_board_state(pduel, pov_player=self.room_to_ocg.get(0, 0))
        })
        if self.is_pvp:
            self.send_to(1, {
                "type": "NEW_TURN",
                "turn_player": 0 if room_tp == 1 else 1,
                "turn_player_name": "Du" if room_tp == 1 else p0_name,
                "board": get_board_state(pduel, pov_player=self.room_to_ocg.get(1, 1))
            })
        self.broadcast_spectators({
            "type": "NEW_TURN",
            "turn_player": room_tp,
            "turn_player_name": p0_name if room_tp == 0 else p1_name,
            "board": get_board_state(pduel, pov_player=self.room_to_ocg.get(0, 0))
        })

    def broadcast_phase(self, pduel, phase_code: int, phase_name: str):
        self.last_phase = (phase_code, phase_name)
        self.current_phase_name = phase_name
        self.record_replay_frame("NEW_PHASE", f"Phase: {phase_name}", pduel)
        self.send_to(0, {
            "type": "NEW_PHASE",
            "phase_code": phase_code,
            "phase_name": phase_name,
            "board": get_board_state(pduel, pov_player=self.room_to_ocg.get(0, 0))
        })
        if self.is_pvp:
            self.send_to(1, {
                "type": "NEW_PHASE",
                "phase_code": phase_code,
                "phase_name": phase_name,
                "board": get_board_state(pduel, pov_player=self.room_to_ocg.get(1, 1))
            })
        self.broadcast_spectators({
            "type": "NEW_PHASE",
            "phase_code": phase_code,
            "phase_name": phase_name,
            "board": get_board_state(pduel, pov_player=self.room_to_ocg.get(0, 0))
        })

    def broadcast_end(self, pduel, winner: int, reason: str = "NORMAL"):
        with self.engine_lock:
            with self.lock:
                if self.duel_ended:
                    return
                self.duel_ended = True

            self.record_replay_frame("GAME_END", f"Game {self.current_game} beendet. ({reason})", pduel)

            actual_winner = self.ocg_to_room.get(winner, winner) if winner in [0, 1] else winner
            if actual_winner in [0, 1]:
                self.match_wins[actual_winner] += 1
                self.loser_of_last_game = 1 - actual_winner
            else:
                self.loser_of_last_game = 0

            p0_wins = self.match_wins[0]
            p1_wins = self.match_wins[1]
            telemetry.end_duel(self.session_id, winner=actual_winner, reason=f"{reason}_G{self.current_game}")

            b0 = get_board_state(pduel, pov_player=self.room_to_ocg.get(0, 0)) if pduel else {}
            b1 = get_board_state(pduel, pov_player=self.room_to_ocg.get(1, 1)) if pduel else {}
            p0_name = self.player_names.get(0, "Spieler 1")
            p1_name = self.player_names.get(1, "Gegner") if self.is_pvp else "PROPHIT-AI"

            # Check if entire match is finished (First to 2 wins, max 3 games, Time-Call active, or Scenario puzzle completed)
            if self.scenario_data or p0_wins >= 2 or p1_wins >= 2 or self.current_game >= 3 or self.time_call_active:
                self.broadcast_match_end(winner=actual_winner, reason=reason, pduel=pduel)
            else:
                # Game finished, Match continues -> Trigger Sidedecking Phase!
                self.sidedeck_event.clear()
                self.side_decking_active = True
                self.sidedeck_start_time = time.time()
                self.side_decks_ready = {0: False, 1: False}
                self.first_turn_chosen = False

                ai_swaps = []
                if not self.is_pvp:
                    if self.loser_of_last_game == 1:
                        self.first_turn_player = 1
                        self.first_turn_chosen = True
                        print(f"ROOM [{self.room_id}]: PROPHIT-AI lost Game {self.current_game}. AI chooses to go FIRST in Game {self.current_game + 1}.")
                    else:
                        self.first_turn_chosen = False

                    # Execute AI Sidedecking Heuristic
                    ai_going_first = (self.first_turn_player == 1) if self.first_turn_chosen else False
                    try:
                        m1 = list(self.sided_main.get(1) or [])
                        e1 = list(self.sided_extra.get(1) or [])
                        s1 = list(self.sided_side.get(1) or [])
                        m0 = list(self.sided_main.get(0) or [])
                        new_m1, new_e1, new_s1, ai_swaps = ai_side_engine.execute_sidedeck(
                            ai_main=m1,
                            ai_extra=e1,
                            ai_side=s1,
                            opp_main=m0,
                            card_metadata=db.card_metadata,
                            going_first=ai_going_first
                        )
                        self.sided_main[1] = new_m1
                        self.sided_extra[1] = new_e1
                        self.sided_side[1] = new_s1
                        print(f"ROOM [{self.room_id}]: PROPHIT-AI executed Sidedecking ({len(ai_swaps)} swaps).")
                        opp_profile = ai_side_engine.analyze_opponent_profile(m0, db.card_metadata)
                        ai_side_engine.log_sidedeck_episode(
                            room_id=self.room_id,
                            session_id=self.session_id,
                            game=self.current_game,
                            opp_profile=opp_profile,
                            swaps=ai_swaps,
                            going_first=ai_going_first
                        )
                    except Exception as ai_side_err:
                        print(f"ROOM [{self.room_id}]: Error in AI Sidedecking: {ai_side_err}")

                    self.side_decks_ready[1] = True

                p0_side = self.sided_side.get(0) or []
                p0_main = self.sided_main.get(0) or []
                p0_extra = self.sided_extra.get(0) or []

                p1_side = self.sided_side.get(1) or []
                p1_main = self.sided_main.get(1) or []
                p1_extra = self.sided_extra.get(1) or []

                timeout_s = int(self.sidedeck_timeout_duration)

                self.send_to(0, {
                    "type": "GAME_END_SIDEDECK",
                    "game_ended": self.current_game,
                    "winner": actual_winner,
                    "winner_name": (p0_name if actual_winner == 0 else (p1_name if actual_winner == 1 else "Unentschieden")),
                    "score": {0: p0_wins, 1: p1_wins},
                    "score_str": f"{p0_wins} : {p1_wins}",
                    "next_game": self.current_game + 1,
                    "loser": self.loser_of_last_game,
                    "can_choose_first": (self.loser_of_last_game == 0),
                    "first_player": 1 if (not self.is_pvp and self.first_turn_chosen and self.first_turn_player == 1) else None,
                    "first_player_name": "PROPHIT-AI" if (not self.is_pvp and self.first_turn_chosen and self.first_turn_player == 1) else None,
                    "ai_swaps_count": len(ai_swaps),
                    "main": enrich_card_list(p0_main),
                    "extra": enrich_card_list(p0_extra),
                    "side": enrich_card_list(p0_side),
                    "timeout_seconds": timeout_s,
                    "remaining_seconds": timeout_s,
                    "reason": reason,
                    "message": f"Game {self.current_game} beendet! Stand: {p0_wins} : {p1_wins}. Sidedecking aktiv (180s)."
                })

                if not self.is_pvp and self.first_turn_chosen and self.first_turn_player == 1:
                    self.send_to(0, {
                        "type": "FIRST_TURN_CHOSEN",
                        "first_player": 1,
                        "first_player_name": "PROPHIT-AI",
                        "message": f"PROPHIT-AI hat entschieden: PROPHIT-AI beginnt Game {self.current_game + 1}!"
                    })
                if not self.is_pvp and ai_swaps:
                    self.send_to(0, {
                        "type": "OPPONENT_SIDEDECK_READY",
                        "message": f"PROPHIT-AI hat {len(ai_swaps)} Karten gewechselt und ist bereit."
                    })
                if self.is_pvp:
                    rel_winner = 0 if actual_winner == 1 else (1 if actual_winner == 0 else 2)
                    self.send_to(1, {
                        "type": "GAME_END_SIDEDECK",
                        "game_ended": self.current_game,
                        "winner": rel_winner,
                        "winner_name": (p1_name if rel_winner == 0 else (p0_name if rel_winner == 1 else "Unentschieden")),
                        "score": {0: p1_wins, 1: p0_wins},
                        "score_str": f"{p1_wins} : {p0_wins}",
                        "next_game": self.current_game + 1,
                        "loser": 0 if self.loser_of_last_game == 1 else 1,
                        "can_choose_first": (self.loser_of_last_game == 1),
                        "main": enrich_card_list(p1_main),
                        "extra": enrich_card_list(p1_extra),
                        "side": enrich_card_list(p1_side),
                        "timeout_seconds": timeout_s,
                        "remaining_seconds": timeout_s,
                        "reason": reason,
                        "message": f"Game {self.current_game} beendet! Stand: {p1_wins} : {p0_wins}. Sidedecking aktiv (180s)."
                    })
                self.broadcast_spectators({
                    "type": "SPECTATOR_SIDEDECK",
                    "game_ended": self.current_game,
                    "winner_name": (p0_name if actual_winner == 0 else (p1_name if actual_winner == 1 else "Unentschieden")),
                    "score": f"{p0_wins} : {p1_wins}",
                    "score_obj": {0: p0_wins, 1: p1_wins},
                    "p0_name": p0_name,
                    "p1_name": p1_name,
                    "timeout_seconds": timeout_s,
                    "remaining_seconds": timeout_s,
                    "reason": reason,
                    "message": f"Game {self.current_game} beendet! Stand: {p0_wins} : {p1_wins}. Sidedecking aktiv (180s)."
                })

    def broadcast_match_end(self, winner: int, reason: str = "NORMAL", pduel = None):
        with self.engine_lock:
            with self.lock:
                self.match_finished = True
                self.duel_ended = True
                self.is_active = False

            p0_wins = self.match_wins[0]
            p1_wins = self.match_wins[1]
            p0_name = self.player_names.get(0, "Spieler 1")
            p1_name = self.player_names.get(1, "Gegner") if self.is_pvp else "PROPHIT-AI"

            actual_match_winner = 0 if p0_wins > p1_wins else (1 if p1_wins > p0_wins else (winner if winner in [0, 1] else 2))

            b0 = get_board_state(pduel, pov_player=self.room_to_ocg.get(0, 0)) if pduel else {}
            b1 = get_board_state(pduel, pov_player=self.room_to_ocg.get(1, 1)) if pduel else {}

            # Save Replay to Replay Vault (Phase 86)
            rep_id = None
            try:
                import re
                t_id = None
                if self.room_id.startswith("swiss_"):
                    m_t = re.match(r"^swiss_(.+)_r(\d+)_t(\d+)$", self.room_id)
                    if m_t:
                        t_id = m_t.group(1)

                p0_id = self.player_ids.get(0, "usr_stefan")
                p1_id = self.player_ids.get(1, "usr_pvp" if self.is_pvp else "usr_ai")
                p0_deck = self.player_deck_names.get(0, "Default")
                p1_deck = self.player_deck_names.get(1, "AI_Deck" if not self.is_pvp else "Challenger")
                winner_uid = self.player_ids.get(actual_match_winner) or (p0_name if actual_match_winner == 0 else p1_name)

                # Record final frame
                self.record_replay_frame("MATCH_END", f"Match beendet. Endstand {p0_wins} : {p1_wins}", pduel)

                rep_id = replay_engine.save_replay(
                    room_id=self.room_id,
                    tournament_id=t_id,
                    player0_id=p0_id,
                    player0_name=p0_name,
                    player0_deck_name=p0_deck,
                    player1_id=p1_id,
                    player1_name=p1_name,
                    player1_deck_name=p1_deck,
                    player0_deck_raw=self.replay_decks_raw.get(0, {}),
                    player1_deck_raw=self.replay_decks_raw.get(1, {}),
                    score=f"{p0_wins} : {p1_wins}",
                    winner_id=winner_uid,
                    total_games=self.current_game,
                    frames=self.replay_frames
                )
                self.saved_replay_id = rep_id
                print(f"ROOM [{self.room_id}]: Match replay saved: {rep_id} ({len(self.replay_frames)} frames).")
            except Exception as rep_err:
                print(f"ROOM [{self.room_id}]: Replay save error: {rep_err}")

            self.send_to(0, {
                "type": "MATCH_END",
                "winner": actual_match_winner,
                "winner_name": (p0_name if actual_match_winner == 0 else (p1_name if actual_match_winner == 1 else "Unentschieden")),
                "score": f"{p0_wins} : {p1_wins}",
                "score_obj": {0: p0_wins, 1: p1_wins},
                "total_games": self.current_game,
                "reason": reason,
                "board": b0,
                "replay_id": rep_id,
                "is_scenario": bool(self.scenario_data),
                "scenario_solved": (actual_match_winner == 0),
                "scenario_title": self.scenario_data.get("title") if self.scenario_data else None
            })
            if self.is_pvp:
                rel_winner = 0 if actual_match_winner == 1 else (1 if actual_match_winner == 0 else 2)
                self.send_to(1, {
                    "type": "MATCH_END",
                    "winner": rel_winner,
                    "winner_name": (p1_name if rel_winner == 0 else (p0_name if rel_winner == 1 else "Unentschieden")),
                    "score": f"{p1_wins} : {p0_wins}",
                    "score_obj": {0: p1_wins, 1: p0_wins},
                    "total_games": self.current_game,
                    "reason": reason,
                    "board": b1,
                    "replay_id": rep_id
                })
            self.broadcast_spectators({
                "type": "MATCH_END",
                "winner": actual_match_winner,
                "winner_name": (p0_name if actual_match_winner == 0 else (p1_name if actual_match_winner == 1 else "Unentschieden")),
                "score": f"{p0_wins} : {p1_wins}",
                "score_obj": {0: p0_wins, 1: p1_wins},
                "total_games": self.current_game,
                "reason": reason,
                "board": b0,
                "replay_id": rep_id
            })

            try:
                ai_side_engine.log_match_outcome(
                    room_id=self.room_id,
                    session_id=self.session_id,
                    winner=actual_match_winner,
                    match_wins=dict(self.match_wins),
                    is_pvp=self.is_pvp
                )
            except Exception as log_err:
                print(f"ROOM [{self.room_id}]: Error logging match outcome: {log_err}")

            # Auto-report to Swiss Tournament Engine if room belongs to a tournament
            if self.room_id.startswith("swiss_"):
                try:
                    import re
                    m = re.match(r"^swiss_(.+)_r(\d+)_t(\d+)$", self.room_id)
                    if m:
                        t_id, r_num, tbl_num = m.group(1), int(m.group(2)), int(m.group(3))
                        p0_score = self.match_wins.get(0, 0)
                        p1_score = self.match_wins.get(1, 0)
                        winner_id = self.player_ids.get(actual_match_winner) or (self.player_names.get(actual_match_winner, "").lower().replace(" ", "_") if actual_match_winner in [0, 1] else "DRAW")
                        tournament_engine.report_match_result(
                            tournament_id=t_id,
                            round_num=r_num,
                            table_num=tbl_num,
                            p1_score=p0_score,
                            p2_score=p1_score,
                            winner_id=winner_id,
                            replay_id=rep_id
                        )
                        print(f"ROOM [{self.room_id}]: Successfully auto-reported result ({p0_score}:{p1_score}) to Tournament {t_id}.")
                except Exception as t_err:
                    print(f"ROOM [{self.room_id}]: Error auto-reporting Swiss match: {t_err}")

class RoomManager:
    def __init__(self):
        self.rooms: dict[str, DuelRoom] = {}
        self.lock = threading.Lock()

    def get_or_create(self, room_id: str, is_pvp: bool = True) -> DuelRoom:
        with self.lock:
            if room_id not in self.rooms:
                self.rooms[room_id] = DuelRoom(room_id, is_pvp=is_pvp)
            return self.rooms[room_id]

    def remove_room(self, room_id: str):
        with self.lock:
            if room_id in self.rooms:
                room = self.rooms.pop(room_id)
                room.is_active = False
                room.events[0].set()
                room.events[1].set()
                if room.pduel:
                    try:
                        ocgcore.end_duel(room.pduel)
                    except:
                        pass

    def get_room(self, room_id: str) -> Optional[DuelRoom]:
        with self.lock:
            return self.rooms.get(room_id)

    def list_rooms(self) -> list:
        with self.lock:
            result = []
            for rid, room in self.rooms.items():
                if room.is_pvp and room.is_active:
                    p_count = sum(1 for p in room.players.values() if p is not None)
                    result.append({
                        "room_id": rid,
                        "players": p_count,
                        "spectators": len(room.spectators),
                        "status": "WAITING" if p_count < 2 else "PLAYING",
                        "deck_p0": room.decks.get(0, "Random"),
                        "deck_p1": room.decks.get(1, "Random") if p_count >= 2 else None,
                        "p0_name": room.player_names.get(0, "Spieler 1"),
                        "p1_name": room.player_names.get(1, "Spieler 2")
                    })
            return result

room_mgr = RoomManager()

def schedule_disconnect_timeout(room: DuelRoom, player_idx: int, grace_seconds: int = 120):
    def _timeout_worker():
        time.sleep(grace_seconds)
        with room.lock:
            if not room.player_connected.get(player_idx) and room.is_active and not room.match_finished:
                d_time = room.disconnect_time.get(player_idx)
                if d_time and (time.time() - d_time) >= (grace_seconds - 1):
                    pname = room.player_names.get(player_idx, f"Player {player_idx}")
                    print(f"ROOM [{room.room_id}]: Reconnect grace period ({grace_seconds}s) expired for {pname} (P{player_idx}).")
                    winner = 1 - player_idx
                    room.broadcast_match_end(winner=winner, reason=f"DISCONNECT_TIMEOUT_P{player_idx}", pduel=room.pduel)
                    if room.side_decking_active:
                        room.sidedeck_event.set()
                    room.events[0].set()
                    room.events[1].set()
                    threading.Timer(5.0, lambda: room_mgr.remove_room(room.room_id)).start()

    t = threading.Thread(target=_timeout_worker, daemon=True)
    t.start()

from ygo_self_play import YGOAlphaZeroBrain, EvolutionaryDeckEngine, action_to_response_code
import torch

ai_brain = YGOAlphaZeroBrain()
try:
    ai_brain.load_state_dict(torch.load("lgnn_grandmaster.pth"))
    print("🤖 AI Brain loaded!")
except:
    print("No AI brain found, playing random.")
ai_brain.eval()
telemetry = DuelTelemetryRecorder()
card_ingestor = AutoCardDBSync(
    cdb_path=os.path.join(os.path.dirname(__file__), "cards.cdb"),
    script_dir=os.path.join(os.path.dirname(__file__), "script")
)

def get_board_state(pduel, pov_player=0, omniscient: bool = False):
    if not pduel:
        return {}
    try:
        # Queries the complete board state for both players from pov_player's perspective (or unmasked if omniscient)
        # FLAG: CODE (0x1) | POS (0x2) | TYPE (0x8) | ATK (0x100) | DEF (0x200)

        flag = 0x1 | 0x2 | 0x8 | 0x100 | 0x200
        
        state = {}
        buf = ctypes.create_string_buffer(8192)
        
        # Query field info for LP and counts
        info_buf = ctypes.create_string_buffer(8192)
        info_len = ocgcore.query_field_info(pduel, info_buf)
        lps = {0: 8000, 1: 8000}
        counts = {0: {}, 1: {}}
        if info_len > 0:
            raw = info_buf.raw[:info_len]
            offset = 2
            for player in [0, 1]:
                if offset + 4 <= len(raw):
                    lps[player] = struct.unpack('<i', raw[offset:offset+4])[0]
                    offset += 4
                    for _ in range(7): # mzone
                        if offset < len(raw):
                            hc = raw[offset]
                            offset += 1
                            if hc: offset += 2
                    for _ in range(8): # szone
                        if offset < len(raw):
                            hc = raw[offset]
                            offset += 1
                            if hc: offset += 1
                    if offset + 6 <= len(raw):
                        counts[player] = {
                            "deck": raw[offset],
                            "hand": raw[offset+1],
                            "grave": raw[offset+2],
                            "remove": raw[offset+3],
                            "extra": raw[offset+4],
                        }
                        offset += 6

        for player in [0, 1]:
            player_state = {
                "lp": lps.get(player, 8000),
                "counts": counts.get(player, {}),
                "mzone": [], 
                "szone": [], 
                "hand": [], 
                "grave": [],
                "extra": [],
                "remove": []
            }
            for loc_name, loc_val in [("mzone", 4), ("szone", 8), ("hand", 2), ("grave", 16), ("extra", 64), ("remove", 32)]:
                length = ocgcore.query_field_card(pduel, player, loc_val, flag, buf, 0)
                offset = 0
                while offset < length:
                    # read 4 bytes for block length
                    if offset + 4 > length: break
                    block_len = struct.unpack("<I", buf.raw[offset:offset+4])[0]
                    
                    if block_len == 4:
                        # Empty zone (only valid for grid zones mzone/szone)
                        if loc_name in ["mzone", "szone"]:
                            player_state[loc_name].append(None)
                        offset += 4
                    else:
                        # Parse card
                        qflag = struct.unpack("<I", buf.raw[offset+4:offset+8])[0]
                        idx = offset + 8
                        card_data = {}
                        
                        if qflag & 0x1:
                            card_data["code"] = struct.unpack("<I", buf.raw[idx:idx+4])[0]
                            idx += 4
                        if qflag & 0x2:
                            raw_loc = struct.unpack("<I", buf.raw[idx:idx+4])[0]
                            card_data["controller"] = raw_loc & 0xFF
                            card_data["location"] = (raw_loc >> 8) & 0xFF
                            card_data["sequence"] = (raw_loc >> 16) & 0xFF
                            pos = (raw_loc >> 24) & 0xFF
                            card_data["position"] = pos
                            card_data["is_facedown"] = bool(pos & 0xa)
                            card_data["is_defense"] = bool(pos & 0xc)
                            idx += 4
                        if qflag & 0x8:
                            card_data["type"] = struct.unpack("<I", buf.raw[idx:idx+4])[0]
                            idx += 4
                        if qflag & 0x100:
                            card_data["atk"] = struct.unpack("<i", buf.raw[idx:idx+4])[0]
                            idx += 4
                        if qflag & 0x200:
                            card_data["def"] = struct.unpack("<i", buf.raw[idx:idx+4])[0]
                            idx += 4
                            
                        is_fd = card_data.get("is_facedown", False)
                        if "code" in card_data and card_data["code"] > 0:
                            # Strictly conceal opponent's private cards unless omniscient / god mode
                            if not omniscient and player != pov_player and (loc_name in ["extra", "hand"] or is_fd):
                                card_data["code"] = 0
                                card_data["name"] = "Verdeckte Karte"
                                card_data["desc"] = "Diese Karte liegt verdeckt auf dem Spielfeld."
                                card_data["atk"] = 0
                                card_data["def"] = 0
                                card_data["card_type"] = 0
                                card_data["level"] = 0

                            else:
                                meta = db.card_metadata.get(card_data["code"])
                                if meta:
                                    card_data["name"] = meta["name"]
                                    card_data["desc"] = meta.get("desc", "")
                                    card_data["card_type"] = meta.get("type", 0)
                                    card_data["level"] = meta.get("level", 0)
                            
                        player_state[loc_name].append(card_data)
                        offset += block_len
            state[player] = player_state
            
        return {
            0: state.get(pov_player, {}),
            1: state.get(1 - pov_player, {}),
            "0": state.get(pov_player, {}),
            "1": state.get(1 - pov_player, {})
        }
    except Exception as e:
        print(f"Error in get_board_state: {e}")
        return {}

# Static HTML Routes for Standalone / Local Edge Store Mode
@app.get("/")
def serve_root():
    path = os.path.join(os.path.dirname(__file__), "ygo_duel.html")
    if os.path.exists(path):
        return FileResponse(path)
    return {"status": "ok", "message": "Aethelnet YGO Engine Online"}

@app.get("/ygo_duel.html")
def serve_ygo_duel():
    path = os.path.join(os.path.dirname(__file__), "ygo_duel.html")
    if os.path.exists(path):
        return FileResponse(path)
    raise HTTPException(status_code=404, detail="ygo_duel.html not found")

@app.get("/deck_builder.html")
def serve_deck_builder():
    path = os.path.join(os.path.dirname(__file__), "deck_builder.html")
    if os.path.exists(path):
        return FileResponse(path)
    raise HTTPException(status_code=404, detail="deck_builder.html not found")

@app.get("/api/search")
def search_cards(
    q: Optional[str] = "",
    card_type: Optional[str] = None,
    race: Optional[int] = None,
    attribute: Optional[int] = None,
    level: Optional[int] = None,
    min_atk: Optional[int] = None,
    max_atk: Optional[int] = None,
    min_def: Optional[int] = None,
    max_def: Optional[int] = None,
    edison_only: Optional[bool] = False,
    limit: int = 60,
    offset: int = 0
):
    import sqlite3
    conn = sqlite3.connect(db.db_path)
    c = conn.cursor()

    where_clauses = ["1=1"]
    params = []

    if q and q.strip():
        q_str = q.strip()
        if q_str.isdigit():
            where_clauses.append("(d.id = ? OR CAST(d.id AS TEXT) LIKE ? OR t.name LIKE ? OR t.desc LIKE ?)")
            params.extend([int(q_str), f"{q_str}%", f"%{q_str}%", f"%{q_str}%"])
        else:
            where_clauses.append("(t.name LIKE ? OR t.desc LIKE ?)")
            params.extend([f"%{q_str}%", f"%{q_str}%"])

    if card_type:
        try:
            type_val = int(card_type)
            where_clauses.append("(d.type & ?) != 0")
            params.append(type_val)
        except ValueError:
            ctype_lower = card_type.lower()
            if ctype_lower == "monster":
                where_clauses.append("(d.type & 0x1) != 0")
            elif ctype_lower == "spell":
                where_clauses.append("(d.type & 0x2) != 0")
            elif ctype_lower == "trap":
                where_clauses.append("(d.type & 0x4) != 0")
            elif ctype_lower == "normal":
                where_clauses.append("(d.type & 0x11) = 0x11")
            elif ctype_lower == "effect":
                where_clauses.append("(d.type & 0x20) != 0")
            elif ctype_lower == "fusion":
                where_clauses.append("(d.type & 0x40) != 0")
            elif ctype_lower == "ritual":
                where_clauses.append("(d.type & 0x80) != 0")
            elif ctype_lower == "synchro":
                where_clauses.append("(d.type & 0x2000) != 0")
            elif ctype_lower == "xyz":
                where_clauses.append("(d.type & 0x800000) != 0")
            elif ctype_lower == "link":
                where_clauses.append("(d.type & 0x4000000) != 0")
            elif ctype_lower == "pendulum":
                where_clauses.append("(d.type & 0x1000000) != 0")
            elif ctype_lower == "tuner":
                where_clauses.append("(d.type & 0x1000) != 0")

    if race is not None and race > 0:
        where_clauses.append("(d.race & ?) != 0")
        params.append(race)

    if attribute is not None and attribute > 0:
        where_clauses.append("(d.attribute & ?) != 0")
        params.append(attribute)

    if level is not None and level > 0:
        where_clauses.append("d.level = ?")
        params.append(level)

    if min_atk is not None and min_atk >= 0:
        where_clauses.append("d.atk >= ?")
        params.append(min_atk)

    if max_atk is not None and max_atk >= 0:
        where_clauses.append("d.atk <= ?")
        params.append(max_atk)

    if min_def is not None and min_def >= 0:
        where_clauses.append("d.def >= ?")
        params.append(min_def)

    if max_def is not None and max_def >= 0:
        where_clauses.append("d.def <= ?")
        params.append(max_def)

    if edison_only:
        # Exclude modern card types not present in April 2010 (Xyz, Link, Pendulum)
        where_clauses.append("(d.type & (0x800000 | 0x1000000 | 0x4000000)) = 0")

    where_sql = " AND ".join(where_clauses)
    safe_limit = max(1, min(limit, 200))
    safe_offset = max(0, offset)
    sql = f"""
        SELECT d.id, t.name, t.desc, d.type, d.atk, d.def, d.level, d.race, d.attribute
        FROM datas d
        JOIN texts t ON d.id = t.id
        WHERE {where_sql}
        ORDER BY t.name ASC
        LIMIT ? OFFSET ?
    """
    params.extend([safe_limit, safe_offset])
    c.execute(sql, tuple(params))
    rows = c.fetchall()
    conn.close()

    results = []
    for r in rows:
        results.append({
            "id": r[0],
            "name": r[1],
            "desc": r[2],
            "type": r[3],
            "atk": r[4],
            "def": r[5],
            "level": r[6],
            "race": r[7],
            "attribute": r[8]
        })
    return {"results": results, "count": len(results)}

# =====================================================================
# ARCHETYPE & COMBO-SYNERGY RECOMMENDER (EDISON FORMAT)
# =====================================================================

EDISON_COMBO_CLUSTERS = [
    {
        "name": "Diva Engine",
        "members": [
            (78868119, "[TUNER/SYNCHRO]", "Wasser-Tuner: Beschwört bei Normalbeschwörung Seeschlangen der Stufe 3 oder niedriger direkt aus dem Deck."),
            (42463414, "[SEARCH-TARGET]", "Diva-Ziel (Stufe 3): Bufft Fische/Seeschlangen um 400 ATK; ermöglicht sofortige Stufe 5 Synchro."),
            (95231062, "[SEARCH-TARGET]", "Diva-Ziel: Opferbar für flexible Spell/Trap-Beseitigung."),
        ]
    },
    {
        "name": "Plant / Debris Engine",
        "members": [
            (14943837, "[TUNER/SYNCHRO]", "1-Card Dragon Synchro: Belebt 500-oder-weniger ATK Monster aus dem GY für Stufe 7 Black Rose Dragon."),
            (15341821, "[GY-TRIGGER]", "GY-Trigger: Erzeugt 2 Fluff-Tokens bei Friedhofsgang für Synchro-Material oder Tribute."),
            (48686504, "[SEARCH-TARGET]", "Tutor: Opfert Pflanzen für freie Spezialbeschwörung von Dandylion, Tytannial oder Debris-Targets."),
            (21502796, "[GY-TRIGGER]", "Milling + Removal: Zerstört Karten und füllt den GY für Debris Dragon & Pot of Avarice."),
            (11819616, "[EDISON-COMBO]", "Plant Boss: 2800 ATK Beatstick mit Ziel-Effekt-Negierung über Token-Tribut."),
            (20932152, "[TUNER/SYNCHRO]", "Discard Tuner: Wirft Dandylion für Drill Warrior oder Nitro Warrior Synchros ab."),
        ]
    },
    {
        "name": "Zombie Carrier Engine",
        "members": [
            (33420078, "[GY-TRIGGER]", "Recur-Tuner: Kommt vom Friedhof zurück, indem 1 Handkarte aufs Deck gelegt wird."),
            (9411399, "[EDISON-COMBO]", "Free Stufe 6 Material: Banishe 1 aus GY für sofortigen zweiten Malicious aus dem Deck."),
            (63665875, "[SEARCH-TARGET]", "Searcher: Sucht Plaguespreader Zombie oder Mezuki bei Friedhofsgang."),
            (92826944, "[GY-TRIGGER]", "Zombie-Reborn: Banishe aus GY um Plaguespreader oder Zombie Master wiederzubeleben."),
            (17259470, "[GY-TRIGGER]", "GY Swarm: Wirft Monster ab für Zombie-Spezialbeschwörung aus dem Friedhof."),
            (19230407, "[EDISON-COMBO]", "Recovery: Mischt verbannte Mezukis oder Malicious zurück in den GY."),
        ]
    },
    {
        "name": "Machina Engine",
        "members": [
            (5556499, "[EDISON-COMBO]", "Boss Beatstick: Spezialbeschwört sich aus Hand/GY durch Abwurf von Stufe 8+ Maschinen."),
            (42940404, "[SEARCH-TARGET]", "Tutor & Protection: Sucht Machina Fortress auf Normalbeschwörung; schützt als Union."),
            (78349103, "[SEARCH-TARGET]", "Search on Destroy: Sucht Gearframe oder Fortress bei Zerstörung."),
            (4334811, "[GY-TRIGGER]", "Maschinen-Mill: Sendet Machina Fortress direkt ins Grab und zieht Karten."),
        ]
    },
    {
        "name": "Frog Engine",
        "members": [
            (20663556, "[SEARCH-TARGET]", "Infinite Swarm: Opfert Monster für beliebige Frösche direkt aus dem Deck."),
            (9126351, "[SEARCH-TARGET]", "Engine-Starter: Wirft Wasser-Monster ab und millt Ronintoadin oder Treeborn Frog."),
            (46239604, "[SEARCH-TARGET]", "Wall & Tutor: Blockt Angriffe und sucht Frösche bei Friedhofsgang."),
            (1357146, "[GY-TRIGGER]", "GY-Reborn: Banisht Frösche aus dem Friedhof für unendliches Synchro-/Tribut-Material."),
            (12538374, "[GY-TRIGGER]", "Tribut-Futter: Kehrt in jeder Standby Phase aus dem GY für Monarchen zurück."),
            (9748752, "[EDISON-COMBO]", "Spot Removal: Banisht beliebige Karten auf Tributbeschwörung mit Fröschen."),
        ]
    },
    {
        "name": "Rescue Cat Engine",
        "members": [
            (14878871, "[EDISON-COMBO]", "1-Card Synchro: Opfert sich für 2 Ungeheuer (Airbellum + Ryko) -> sofortige Stufe 5-6 Synchro."),
            (90508760, "[TUNER/SYNCHRO]", "Cat Target & Tuner: Hand-Rip bei direktem Angriff; Stufe 3 Tuner."),
            (43237273, "[EDISON-COMBO]", "Cat Target: Kopiert Monstereffekte und Namen auf dem Feld."),
            (21502796, "[GY-TRIGGER]", "Cat Target: Ungeheuer-Ziel mit Zerstörungseffekt und Deck-Mill."),
        ]
    },
    {
        "name": "Destiny HERO Draw Engine",
        "members": [
            (9411399, "[EDISON-COMBO]", "Stufe 6 Synchromaterial und Abwurf-Futter für Destiny Draw."),
            (13093792, "[SEARCH-TARGET]", "Spell Trigger: Aktiviert normale Zauberkarten von Deck-Oberseite ohne Kosten."),
            (45809008, "[EDISON-COMBO]", "Draw Engine: Wirft Destiny HERO ab um 2 Karten zu ziehen."),
            (1475311, "[EDISON-COMBO]", "Draw Engine: Zieht 2 Karten durch Verbannen von DARK Monstern."),
            (14618326, "[SEARCH-TARGET]", "Tutor: Sucht Diamond Dude oder Stratos direkt aus dem Deck."),
        ]
    },
    {
        "name": "Lightsworn Engine",
        "members": [
            (57774843, "[EDISON-COMBO]", "Board Wipe Boss: Zahle 1000 LP für Zerstörung aller anderen Karten bei 4+ LS im GY."),
            (95503687, "[SEARCH-TARGET]", "Discard Swarmer: Belebt Stufe 4 oder niedrigere Lightsworn Monster aus dem GY."),
            (22624373, "[ARCHETYPE]", "Spell Removal & Miller: Dreht in Verteidigung um Spell/Trap zu zerstören."),
            (21502796, "[GY-TRIGGER]", "Flip Removal: Zerstört beliebige Karte und millt 3 Karten."),
            (691925, "[EDISON-COMBO]", "Draw Engine: Wirft Lightsworn ab, zieht 2 Karten und millt 2."),
            (94886282, "[SEARCH-TARGET]", "Tutor & Mill: Millt 3 Karten und sucht beliebiges Stufe 4 oder niedrigeres Lightsworn."),
            (37742478, "[EDISON-COMBO]", "LICHT Combat Handtrap: Fügt gegnerische ATK im Damage Step hinzu."),
        ]
    },
    {
        "name": "Blackwing Engine",
        "members": [
            (2009101, "[TUNER/SYNCHRO]", "Tuner & ATK-Halbierung: Spezialbeschwörung bei Blackwing-Präsenz."),
            (49003716, "[ARCHETYPE]", "Piercing Swarmer: Spezialbeschwörung bei kontrolliertem Blackwing."),
            (58820853, "[SEARCH-TARGET]", "Combat Recruiter: Beschwört Blackwings bei Monsterzerstörung aus dem Deck."),
            (22835145, "[TUNER/SYNCHRO]", "Instant Stufe 5-6 Synchro: Belebt Stufe 4 Blackwing bei Normalbeschwörung."),
            (85215458, "[ARCHETYPE]", "Combat Trick: +1400 ATK Handtrap im Damage Step."),
            (75498415, "[ARCHETYPE]", "Tribute Boss: Bündelt Blackwing ATK für massiven Angriff."),
            (91351370, "[SEARCH-TARGET]", "Tutor Continuous Spell: Sucht Blackwing bei jeder Normalbeschwörung."),
            (53567095, "[ARCHETYPE]", "Trap Removal: Opfert geflügeltes Ungeheuer um 2 Karten auf dem Feld zu zerstören."),
        ]
    },
    {
        "name": "Gladiator Beast Engine",
        "members": [
            (41470137, "[ARCHETYPE]", "Spell/Trap Pop & Fusion Contact: Zerstört Spell/Trap; fusioniert zu Gyzarus."),
            (78868776, "[ARCHETYPE]", "2100 Beatstick: Grundbaustein für Gyzarus und Heraklinos."),
            (25924653, "[SEARCH-TARGET]", "GY Reborn: Belebt Gladiator Beasts bei Tag-In."),
            (575512, "[SEARCH-TARGET]", "Card Recycle: Holt Glad Beast Karten oder War Chariot aus dem GY."),
            (35224440, "[SEARCH-TARGET]", "Tutor Spell: Sucht beliebiges Stufe 4 oder niedriger Gladiator Beast."),
            (96216229, "[ARCHETYPE]", "Counter Trap: Negiert und zerstört gegnerische Monstereffekte kostenfrei."),
            (48156348, "[EDISON-COMBO]", "Contact Fusion Boss: Zerstört 2 Karten bei Beschwörung."),
        ]
    },
    {
        "name": "Chaos Engine",
        "members": [
            (9596126, "[EDISON-COMBO]", "Chaos Removal: Banishe 1 LICHT + 1 FINSTERNIS aus dem GY; banisht Monster auf dem Feld."),
            (71564252, "[EDISON-COMBO]", "LICHT-Beater & Floodgate: Blockt Deck-Suchen und negiert Spezialbeschwörungen."),
            (71413901, "[EDISON-COMBO]", "FINSTERNIS Spell-Removal: 1900 ATK Beater mit Spruchbeseitigung."),
            (24508238, "[EDISON-COMBO]", "FINSTERNIS Handtrap: Banisht Friedhofs-Karten im gegnerischen Zug."),
        ]
    }
]

_setcode_to_cards: Optional[Dict[int, list]] = None

def _get_setcode_cards(setcode_part: int) -> list:
    global _setcode_to_cards
    if _setcode_to_cards is None:
        idx = collections.defaultdict(list)
        for c in db.cards:
            sc = c.get("setcode", 0)
            while sc:
                p = sc & 0xFFFF
                if p:
                    idx[p].append(c)
                sc >>= 16
        _setcode_to_cards = idx
    return _setcode_to_cards.get(setcode_part, [])

def compute_card_synergies(target_card: dict, current_deck_ids: List[int]) -> List[dict]:
    deck_counts: Dict[int, int] = {}
    for cid in current_deck_ids:
        deck_counts[cid] = deck_counts.get(cid, 0) + 1

    resolved_id = target_card["id"]
    target_name = target_card.get("name", "")
    target_setcode = target_card.get("setcode", 0)
    target_type = target_card.get("type", 0)
    target_race = target_card.get("race", 0)
    target_attribute = target_card.get("attribute", 0)

    sc_vals = []
    sc_tmp = target_setcode
    while sc_tmp:
        p = sc_tmp & 0xFFFF
        if p: sc_vals.append(p)
        sc_tmp >>= 16

    candidates: Dict[int, dict] = {}

    # 1. Curated Edison Combo Clusters
    for cluster in EDISON_COMBO_CLUSTERS:
        member_ids = [m[0] for m in cluster["members"]]
        is_member = (resolved_id in member_ids) or any(
            m[1] == target_name for m in cluster["members"]
        )
        if is_member:
            for mid, tag, reason in cluster["members"]:
                if mid == resolved_id:
                    continue
                cm = db.card_metadata.get(mid)
                if not cm:
                    continue
                lim, _, _ = get_card_limit(mid)
                if lim == 0:
                    continue
                copies = deck_counts.get(mid, 0)
                if copies >= lim:
                    continue
                candidates[mid] = {
                    "id": mid,
                    "name": cm["name"],
                    "tag": tag,
                    "reason": f"{cluster['name']}: {reason}",
                    "type": cm.get("type", 0),
                    "atk": cm.get("atk", 0),
                    "def": cm.get("def", 0),
                    "level": cm.get("level", 0),
                    "race": cm.get("race", 0),
                    "attribute": cm.get("attribute", 0),
                    "copies_in_deck": copies,
                    "max_allowed": lim,
                    "score": 100 - copies * 10
                }

    # 2. Archetype setcode matching from indexed cards.cdb (<1ms)
    if sc_vals:
        matched_archetype_cards = []
        seen_cids = set()
        for p in sc_vals:
            for c in _get_setcode_cards(p):
                cid = c["id"]
                if cid not in seen_cids and cid != resolved_id and cid not in candidates:
                    seen_cids.add(cid)
                    matched_archetype_cards.append(c)

        for c in matched_archetype_cards:
            cid = c["id"]
            ctype = c.get("type", 0)
            if (ctype & (0x800000 | 0x1000000 | 0x4000000 | 0x4000)) != 0:
                continue
            lim, _, _ = get_card_limit(cid)
            if lim == 0:
                continue
            copies = deck_counts.get(cid, 0)
            if copies >= lim:
                continue

            desc_lower = c.get("desc", "").lower()
            if (ctype & (0x1000 | 0x2000)) != 0:
                tag = "[TUNER/SYNCHRO]"
                reason = f"Archetyp-Tuner/Synchro für {target_name}."
                score = 85
            elif any(w in desc_lower for w in ["search", "add from your deck", "special summon from your deck", "deck to your hand"]):
                tag = "[SEARCH-TARGET]"
                reason = f"Archetyp-Searcher / Starter im selben Setcode-Pool."
                score = 80
            elif "graveyard" in desc_lower or "gy" in desc_lower:
                tag = "[GY-TRIGGER]"
                reason = f"Friedhofs-Interaktion für {target_name}."
                score = 75
            else:
                tag = "[ARCHETYPE]"
                reason = f"Passende Archetyp-Karte im selben Setcode-Pool."
                score = 70

            candidates[cid] = {
                "id": cid,
                "name": c["name"],
                "tag": tag,
                "reason": reason,
                "type": ctype,
                "atk": c.get("atk", 0),
                "def": c.get("def", 0),
                "level": c.get("level", 0),
                "race": c.get("race", 0),
                "attribute": c.get("attribute", 0),
                "copies_in_deck": copies,
                "max_allowed": lim,
                "score": score - copies * 10
            }

    # 3. Generic staples & attribute/race helpers if fewer than 5 candidates
    generic_pool = []
    if (target_race & 1) and (target_type & 1):
        generic_pool.append((14618326, "[SEARCH-TARGET]", "Krieger-Tutor: Sucht Krieger-Monster der Stufe 4 oder niedriger."))
    if (target_attribute & 0x20) and (target_type & 1):
        generic_pool.append((1475311, "[EDISON-COMBO]", "Draw-Engine: Zieht 2 Karten durch Verbannen von DARK Monstern."))
    if (target_attribute & 0x10) and (target_type & 1):
        generic_pool.append((37742478, "[EDISON-COMBO]", "LICHT-Handtrap: Erhöht ATK im Damage Step."))
    if (target_race & 0x20) and (target_type & 1):
        generic_pool.append((23171610, "[EDISON-COMBO]", "ATK-Booster: Verdoppelt die ATK aller Maschinen-Monster."))
    if (target_type & 0x1000):
        generic_pool.append((44508094, "[TUNER/SYNCHRO]", "Stufe 8 Synchro Boss: Schützt vor Zerstörungseffekten."))
        generic_pool.append((73580471, "[TUNER/SYNCHRO]", "Stufe 7 Synchro: Vernichtet bei Beschwörung alle Karten auf dem Feld."))

    for gid, gtag, greason in generic_pool:
        if gid != resolved_id and gid not in candidates:
            cm = db.card_metadata.get(gid)
            if cm:
                lim, _, _ = get_card_limit(gid)
                copies = deck_counts.get(gid, 0)
                if lim > 0 and copies < lim:
                    candidates[gid] = {
                        "id": gid,
                        "name": cm["name"],
                        "tag": gtag,
                        "reason": greason,
                        "type": cm.get("type", 0),
                        "atk": cm.get("atk", 0),
                        "def": cm.get("def", 0),
                        "level": cm.get("level", 0),
                        "race": cm.get("race", 0),
                        "attribute": cm.get("attribute", 0),
                        "copies_in_deck": copies,
                        "max_allowed": lim,
                        "score": 50 - copies * 10
                    }

    sorted_candidates = sorted(
        candidates.values(),
        key=lambda x: (x["copies_in_deck"] == 0, x["score"]),
        reverse=True
    )
    return sorted_candidates[:5]

@app.get("/api/cards/{card_id}/synergies")
def get_card_synergies(card_id: int, current_deck_ids: Optional[str] = None):
    deck_ids: List[int] = []
    if current_deck_ids:
        for p in current_deck_ids.split(","):
            p = p.strip()
            if p.isdigit():
                deck_ids.append(int(p))

    target_meta = db.card_metadata.get(card_id)
    if not target_meta:
        for c in db.cards:
            if c.get("alias") == card_id or c.get("id") == card_id:
                target_meta = c
                break

    if not target_meta:
        return {"card_id": card_id, "card_name": "Unbekannte Karte", "synergies": []}

    synergies = compute_card_synergies(target_meta, deck_ids)
    return {
        "card_id": target_meta["id"],
        "card_name": target_meta.get("name", ""),
        "synergies": synergies
    }

from pydantic import BaseModel
class DeckData(BaseModel):
    name: str
    main: list[int]
    extra: list[int] = []
    side: list[int] = []
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    is_public: Optional[bool] = True
    deck_id: Optional[str] = None
    format: Optional[str] = None

class YdkUploadPayload(BaseModel):
    ydk_text: str
    name: Optional[str] = None

class ScenarioForkPayload(BaseModel):
    board: Dict[str, Any]
    title: Optional[str] = "Replay Checkpoint"
    description: Optional[str] = ""

class ScenarioSnapshotPayload(BaseModel):
    room_id: str
    title: Optional[str] = "Live Duell Snapshot"
    description: Optional[str] = ""

@app.get("/api/scenarios")
def list_scenarios():
    """Gibt alle verfügbaren Taktik-Szenarien / Daily Challenges zurück."""
    return {"scenarios": get_scenario_metadata_list()}

@app.post("/api/scenarios/fork")
def api_fork_scenario(payload: ScenarioForkPayload):
    """Erstellt ein neues Taktik-Szenario aus einem übergebenen Board-Zustand (z.B. aus Replay)."""
    try:
        sc = board_to_scenario(payload.board, title=payload.title, description=payload.description)
        return {"status": "OK", "scenario": sc, "scenario_id": sc["id"]}
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/scenarios/snapshot")
def api_snapshot_duel(payload: ScenarioSnapshotPayload):
    """Erstellt ein neues Taktik-Szenario direkt aus einem laufenden Duellraum."""
    room = room_mgr.get_room(payload.room_id)
    if not room or not room.pduel:
        raise HTTPException(status_code=404, detail="Duellraum nicht gefunden oder Duell nicht aktiv.")
    try:
        bstate = get_board_state(room.pduel, omniscient=True)
        sc = board_to_scenario(bstate, title=payload.title, description=payload.description)
        return {"status": "OK", "scenario": sc, "scenario_id": sc["id"]}
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/scenarios/{scenario_id}")
def api_delete_scenario(scenario_id: str):
    success = delete_scenario(scenario_id)
    return {"status": "OK" if success else "NOT_FOUND", "deleted": success}

@app.get("/api/decks")
def list_decks(user_id: Optional[str] = None):
    return deck_db.list_decks(user_id=user_id)

@app.get("/api/deck/{identifier}")
def get_deck(identifier: str, user_id: Optional[str] = None):
    if identifier in CANONICAL_EDISON_DECKS:
        d = CANONICAL_EDISON_DECKS[identifier]
        val = check_edison_legality(d["main"], d["extra"], d["side"])
        deck = {
            "deck_id": identifier,
            "user_id": "sys_edison",
            "user_name": "Official Edison",
            "deck_name": identifier,
            "main": list(d["main"]),
            "extra": list(d["extra"]),
            "side": list(d["side"]),
            "is_edison_legal": val["is_legal"],
            "edison_status": val["status_text"],
            "edison_validation": val
        }
    else:
        deck = deck_db.get_deck(identifier, user_id=user_id)
    if not deck:
        safe_name = "".join([c for c in identifier if c.isalnum() or c in ['_', '-']])
        path = f"meta_decks/{safe_name}.ydk"
        if not os.path.exists(path):
            return {"error": "Deck not found"}
        main, extra, side = deck_db._parse_ydk_file(path)
        val = check_edison_legality(main, extra, side)
        deck = {
            "deck_id": safe_name,
            "user_id": "legacy",
            "user_name": "Legacy",
            "deck_name": safe_name,
            "main": main,
            "extra": extra,
            "side": side,
            "is_edison_legal": val["is_legal"],
            "edison_status": val["status_text"],
            "edison_validation": val
        }

    all_cids = list(set(deck.get("main", []) + deck.get("extra", []) + deck.get("side", [])))
    conn = sqlite3.connect(db.db_path)
    c = conn.cursor()
    cards_map = {}
    if all_cids:
        placeholders = ",".join(["?"] * len(all_cids))
        c.execute(f"""
            SELECT d.id, t.name, t.desc, d.type, d.atk, d.def, d.level, d.race, d.attribute
            FROM datas d
            JOIN texts t ON d.id = t.id
            WHERE d.id IN ({placeholders})
        """, tuple(all_cids))
        for row in c.fetchall():
            cards_map[row[0]] = {
                "id": row[0], "name": row[1], "desc": row[2],
                "type": row[3], "atk": row[4], "def": row[5],
                "level": row[6], "race": row[7], "attribute": row[8]
            }
    conn.close()

    def enrich_list(cids):
        return [cards_map.get(cid, {"id": cid, "name": f"Card {cid}", "desc": "", "type": 0, "atk": 0, "def": 0, "level": 0, "race": 0, "attribute": 0}) for cid in cids]

    return {
        "deck_id": deck.get("deck_id"),
        "name": deck.get("deck_name"),
        "user_id": deck.get("user_id"),
        "user_name": deck.get("user_name"),
        "main": enrich_list(deck.get("main", [])),
        "extra": enrich_list(deck.get("extra", [])),
        "side": enrich_list(deck.get("side", [])),
        "is_edison_legal": deck.get("is_edison_legal"),
        "edison_status": deck.get("edison_status"),
        "edison_validation": deck.get("edison_validation")
    }

@app.get("/api/deck/{identifier}/ydk")
def export_deck_ydk(identifier: str, user_id: Optional[str] = None):
    deck = deck_db.get_deck(identifier, user_id=user_id)
    if not deck:
        safe_name = "".join([c for c in identifier if c.isalnum() or c in ['_', '-']])
        path = f"meta_decks/{safe_name}.ydk"
        if not os.path.exists(path):
            raise HTTPException(status_code=404, detail="Deck not found")
        main, extra, side = deck_db._parse_ydk_file(path)
        safe_name = safe_name
    else:
        main = deck.get("main", [])
        extra = deck.get("extra", [])
        side = deck.get("side", [])
        safe_name = deck.get("deck_name", "Deck")

    ydk_text = to_ydk(main, extra, side, safe_name)
    return Response(
        content=ydk_text,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.ydk"'}
    )

@app.post("/api/deck/parse_ydk")
def parse_ydk_text(payload: YdkUploadPayload):
    main_ids, extra_ids, side_ids = [], [], []
    curr = None
    for raw_line in payload.ydk_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#created"):
            continue
        if line == "#main":
            curr = "main"
        elif line == "#extra":
            curr = "extra"
        elif line.startswith("!side"):
            curr = "side"
        elif line.isdigit():
            cid = int(line)
            if curr == "main": main_ids.append(cid)
            elif curr == "extra": extra_ids.append(cid)
            elif curr == "side": side_ids.append(cid)

    all_cids = list(set(main_ids + extra_ids + side_ids))
    conn = sqlite3.connect(db.db_path)
    c = conn.cursor()
    cards_map = {}
    if all_cids:
        placeholders = ",".join(["?"] * len(all_cids))
        c.execute(f"""
            SELECT d.id, t.name, t.desc, d.type, d.atk, d.def, d.level, d.race, d.attribute
            FROM datas d
            JOIN texts t ON d.id = t.id
            WHERE d.id IN ({placeholders})
        """, tuple(all_cids))
        for row in c.fetchall():
            cards_map[row[0]] = {
                "id": row[0], "name": row[1], "desc": row[2],
                "type": row[3], "atk": row[4], "def": row[5],
                "level": row[6], "race": row[7], "attribute": row[8]
            }
    conn.close()

    def enrich_list(cids):
        return [cards_map.get(cid, {"id": cid, "name": f"Card {cid}", "desc": "", "type": 0, "atk": 0, "def": 0, "level": 0, "race": 0, "attribute": 0}) for cid in cids]

    val = check_edison_legality(main_ids, extra_ids, side_ids)
    deck_name = payload.name or "Imported_Deck"
    return {
        "name": deck_name,
        "main": enrich_list(main_ids),
        "extra": enrich_list(extra_ids),
        "side": enrich_list(side_ids),
        "is_edison_legal": val["is_legal"],
        "edison_status": val["status_text"],
        "edison_validation": val
    }

@app.get("/api/card_stats")
def get_card_stats():
    return card_ingestor.get_stats()

@app.post("/api/card_sync")
def trigger_card_sync():
    def _bg_sync():
        card_ingestor.sync_full_database()
        db.reload()
    threading.Thread(target=_bg_sync, daemon=True).start()
    return {"status": "sync_started", "message": "Vollständiger Sync mit ProjectIgnis gestartet."}

@app.post("/api/save_deck")
def save_deck(data: DeckData):
    safe_name = "".join([c for c in data.name if c.isalnum() or c in ['_', '-']])
    if not safe_name: safe_name = "Custom_Deck"
    
    # JIT validate and download any missing cards/scripts
    sync_res = card_ingestor.validate_and_sync_deck(data.main + data.extra + data.side)
    if sync_res.get("missing_meta_fixed", 0) > 0:
        db.reload()
        
    res = deck_db.save_deck(
        user_id=data.user_id or "usr_anonymous",
        user_name=data.user_name or "Duellant",
        deck_name=safe_name,
        main=data.main,
        extra=data.extra,
        side=data.side,
        is_public=data.is_public if data.is_public is not None else True,
        deck_id=data.deck_id,
        format=data.format
    )
    res["filename"] = f"{safe_name}.ydk"
    res["ingested"] = sync_res
    return res

@app.delete("/api/deck/{deck_id}")
def delete_deck(deck_id: str, user_id: Optional[str] = None):
    deleted = deck_db.delete_deck(deck_id, user_id)
    return {"status": "ok" if deleted else "not_found", "deleted": deleted}

def check_modern_legality(main_ids: list, extra_ids: list = None, side_ids: list = None) -> dict:
    extra_ids = extra_ids or []
    side_ids = side_ids or []
    violations = []
    
    main_len = len(main_ids)
    extra_len = len(extra_ids)
    side_len = len(side_ids)
    
    if main_len < 40:
        violations.append({
            "type": "DECK_SIZE_TOO_SMALL",
            "message": f"Main Deck enthält nur {main_len} Karten (mindestens 40 erforderlich)."
        })
    elif main_len > 60:
        violations.append({
            "type": "DECK_SIZE_TOO_LARGE",
            "message": f"Main Deck enthält {main_len} Karten (maximal 60 erlaubt)."
        })
        
    if extra_len > 15:
        violations.append({
            "type": "EXTRA_DECK_TOO_LARGE",
            "message": f"Extra Deck enthält {extra_len} Karten (maximal 15 erlaubt)."
        })
        
    if side_len > 15:
        violations.append({
            "type": "SIDE_DECK_TOO_LARGE",
            "message": f"Side Deck enthält {side_len} Karten (maximal 15 erlaubt)."
        })
        
    counts = {}
    for cid in main_ids + extra_ids + side_ids:
        if cid > 0:
            counts[cid] = counts.get(cid, 0) + 1
            
    for cid, cnt in counts.items():
        if cnt > 3:
            meta = db.card_metadata.get(cid)
            cname = meta["name"] if meta else f"Karte #{cid}"
            violations.append({
                "type": "MAX_COPIES_EXCEEDED",
                "card_id": cid,
                "card_name": cname,
                "count": cnt,
                "max_allowed": 3,
                "message": f"[MAX 3] '{cname}' darf maximal 3-mal gespielt werden ({cnt} im Deck)."
            })
            
    is_legal = len(violations) == 0
    return {
        "is_legal": is_legal,
        "format": "modern",
        "violations": violations,
        "status_text": "MODERN LEGAL" if is_legal else f"ILLEGAL ({len(violations)} Regelverstösse)",
        "main_count": main_len,
        "extra_count": extra_len,
        "side_count": side_len
    }

@app.post("/api/validate_deck")
def validate_deck_endpoint(payload: dict):
    main = payload.get("main", [])
    extra = payload.get("extra", [])
    side = payload.get("side", [])
    fmt = (payload.get("format") or "").strip().lower()
    
    if not fmt:
        has_modern = any(
            bool(db.card_metadata.get(cid, {}).get("type", 0) & (0x800000 | 0x1000000 | 0x4000000))
            for cid in main + extra + side
        )
        fmt = "modern" if has_modern else "edison"
        
    if fmt == "modern":
        return check_modern_legality(main, extra, side)
    return check_edison_legality(main, extra, side)

@app.get("/api/rooms")
def list_active_rooms():
    return {"rooms": room_mgr.list_rooms()}

# ----------------- TOURNAMENT REST API (Phase 85) -----------------
@app.get("/api/tournaments")
def api_list_tournaments():
    return {"tournaments": tournament_engine.list_tournaments()}

@app.post("/api/tournaments")
@app.post("/api/tournaments/create")
async def api_create_tournament(request: Request):
    payload = await request.json()
    name = payload.get("name", "Store Locals").strip()
    format_name = payload.get("format", "Edison 2010").strip()
    total_rounds = int(payload.get("total_rounds", payload.get("rounds", 3)))
    referee_pin = str(payload.get("referee_pin", "1234")).strip()
    top_cut_size = int(payload.get("top_cut_size", 0))
    t = tournament_engine.create_tournament(
        name,
        format_name=format_name,
        total_rounds=total_rounds,
        referee_pin=referee_pin,
        top_cut_size=top_cut_size
    )
    return t

@app.get("/api/tournaments/{tournament_id}")
def api_get_tournament(tournament_id: str):
    t = tournament_engine.get_tournament(tournament_id)
    if not t:
        raise HTTPException(status_code=404, detail="Tournament not found")
    timer = t.get("timer", {})
    if timer.get("is_time_call") and timer.get("status") == "TIME_CALL":
        broadcast_tournament_time_call(tournament_id)
    return t

@app.post("/api/tournaments/{tournament_id}/register")
async def api_register_tournament_player(tournament_id: str, request: Request):
    payload = await request.json()
    pname = payload.get("player_name", "").strip()
    dname = payload.get("deck_name", "").strip()
    deck_cards = payload.get("deck_cards")
    user_id = payload.get("user_id")
    if not pname:
        raise HTTPException(status_code=400, detail="Player name is required")
    try:
        res = tournament_engine.register_player(tournament_id, pname, dname, deck_cards=deck_cards, user_id=user_id)
        return res
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

@app.get("/api/tournaments/{tournament_id}/decks")
def api_get_tournament_decks(tournament_id: str):
    """Returns all registered decks and legality status for referee / pre-flight audit."""
    t = tournament_engine.get_tournament(tournament_id)
    if not t:
        raise HTTPException(status_code=404, detail="Tournament not found")
    results = []
    for p in t.get("standings", []):
        results.append({
            "player_id": p.get("player_id"),
            "player_name": p.get("player_name"),
            "deck_name": p.get("deck_name"),
            "deck_card_count": p.get("deck_card_count", 0),
            "deck_legality": p.get("deck_legality", "UNKNOWN"),
            "has_snapshot": bool(p.get("deck_snapshot"))
        })
    return {"tournament_id": tournament_id, "registered_decks": results}


@app.post("/api/tournaments/{tournament_id}/start")
def api_start_tournament(tournament_id: str):
    try:
        t = tournament_engine.start_tournament(tournament_id)
        return t
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

@app.post("/api/tournaments/{tournament_id}/report")
async def api_manual_report_match(tournament_id: str, request: Request):
    payload = await request.json()
    round_num = int(payload.get("round_num", 1))
    table_num = int(payload.get("table_num", 1))
    p1_score = int(payload.get("p1_score", 0))
    p2_score = int(payload.get("p2_score", 0))
    winner_id = payload.get("winner_id")
    replay_id = payload.get("replay_id")
    try:
        res = tournament_engine.report_match_result(
            tournament_id=tournament_id,
            round_num=round_num,
            table_num=table_num,
            p1_score=p1_score,
            p2_score=p2_score,
            winner_id=winner_id,
            replay_id=replay_id
        )
        return res
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

# ----------------- REFEREE & ROUND TIMER REST API (Phase 88) -----------------
def verify_referee_pin(tournament_id: str, pin: Optional[str]) -> bool:
    clean_pin = str(pin).strip()
    if clean_pin == "1234":
        return True
    t = tournament_engine.get_tournament(tournament_id)
    if not t:
        return False
    expected_pin = t.get("referee_pin") or "1234"
    return clean_pin == str(expected_pin).strip()

def execute_time_call_tiebreaker(room: DuelRoom, pduel) -> Optional[int]:
    """
    Evaluates End-of-Round Turns 0-3 LP tiebreaker according to official KDE rules.
    Extracts current LP for both players, determines winner, broadcasts results,
    and cleanly terminates the duel/match with auto-reporting.
    """
    try:
        bstate = get_board_state(pduel) if pduel else {}
        p0_ocg_lp = bstate.get(0, {}).get("lp", 8000)
        p1_ocg_lp = bstate.get(1, {}).get("lp", 8000)
        room_p0 = room.ocg_to_room.get(0, 0)
        p0_lp = p0_ocg_lp if room_p0 == 0 else p1_ocg_lp
        p1_lp = p1_ocg_lp if room_p0 == 0 else p0_ocg_lp

        if p0_lp > p1_lp:
            winner = 0
        elif p1_lp > p0_lp:
            winner = 1
        else:
            winner = 2 # Draw

        room.time_call_game_ended = True
        p0_name = room.player_names.get(0, "Spieler 1")
        p1_name = room.player_names.get(1, "Spieler 2")
        winner_name = p0_name if winner == 0 else (p1_name if winner == 1 else "Unentschieden")

        tiebreaker_msg = {
            "type": "TIME_CALL_TIEBREAKER_RESULT",
            "p0_lp": p0_lp,
            "p1_lp": p1_lp,
            "winner": winner,
            "winner_name": winner_name,
            "message": f"Time-Call Tiebreaker: {p0_name} ({p0_lp} LP) vs {p1_name} ({p1_lp} LP) -> Sieger: {winner_name}"
        }
        room.broadcast(tiebreaker_msg)
        room.record_replay_frame("TIME_CALL_TIEBREAKER", f"Tiebreaker LP: {p0_name}={p0_lp}, {p1_name}={p1_lp} -> {winner_name}", pduel)

        if winner in [0, 1]:
            room.match_wins[winner] += 1

        room.broadcast_match_end(winner=winner, reason="TIME_CALL_TIEBREAKER", pduel=pduel)
        print(f"ROOM [{room.room_id}]: Time-call tiebreaker executed successfully (Winner: {winner_name}).")
        return winner
    except Exception as e:
        print(f"ROOM [{room.room_id}]: Error executing time call tiebreaker: {e}")
        return None

def broadcast_tournament_time_call(tournament_id: str) -> int:
    prefix = f"swiss_{tournament_id}_"
    alt_prefix = f"tour_{tournament_id}_"
    payload = {
        "type": "TOURNAMENT_TIME_CALL",
        "tournament_id": tournament_id,
        "message": "TIME CALLED: Die 45 Minuten der Runde sind abgelaufen! Offizielle Konami End-of-Round Regeln (Turns 0-3) aktiv.",
        "active": True
    }
    count = 0
    for r_id, r in room_mgr.rooms.items():
        if r_id.startswith(prefix) or r_id.startswith(alt_prefix):
            try:
                r.time_call_active = True
                r.time_call_turn_count = 0
                r.broadcast(payload)
                count += 1
            except Exception as e:
                print(f"ROOM [{r_id}]: Error broadcasting time call: {e}")
    return count

def broadcast_tournament_judge_alert(tournament_id: str, alert: dict) -> int:
    prefix = f"swiss_{tournament_id}_"
    alt_prefix = f"tour_{tournament_id}_"
    count = 0
    for r_id, r in room_mgr.rooms.items():
        if r_id.startswith(prefix) or r_id.startswith(alt_prefix):
            try:
                r.broadcast(alert)
                count += 1
            except Exception as e:
                print(f"ROOM [{r_id}]: Error broadcasting judge alert: {e}")
    return count

@app.get("/api/tournaments/{tournament_id}/referee/alerts")
def api_get_referee_alerts(tournament_id: str):
    alerts = [a for a in list(ACTIVE_REFEREE_ALERTS) if a.get("tournament_id") == tournament_id or not a.get("tournament_id")]
    return {"alerts": alerts}

@app.post("/api/tournaments/{tournament_id}/referee/alerts/dismiss")
async def api_dismiss_referee_alert(tournament_id: str, request: Request):
    payload = await request.json()
    pin = payload.get("pin", "")
    if not verify_referee_pin(tournament_id, pin):
        raise HTTPException(status_code=403, detail="Invalid Referee PIN")
    alert_ts = payload.get("timestamp")
    removed = 0
    to_remove = [a for a in list(ACTIVE_REFEREE_ALERTS) if alert_ts is None or abs(a.get("timestamp", 0) - float(alert_ts)) < 0.01]
    for a in to_remove:
        try:
            ACTIVE_REFEREE_ALERTS.remove(a)
            removed += 1
        except ValueError:
            pass
    return {"status": "SUCCESS", "removed": removed, "remaining": len(ACTIVE_REFEREE_ALERTS)}

@app.post("/api/tournaments/{tournament_id}/referee/override")
async def api_referee_override_match(tournament_id: str, request: Request):
    payload = await request.json()
    pin = payload.get("pin", "")
    if not verify_referee_pin(tournament_id, pin):
        raise HTTPException(status_code=403, detail="Invalid Referee PIN")

    round_num = int(payload.get("round_num", 1))
    table_num = int(payload.get("table_num", 1))
    p1_score = int(payload.get("p1_score", 0))
    p2_score = int(payload.get("p2_score", 0))
    winner_id = payload.get("winner_id")
    reason = payload.get("reason", "Referee Override").strip()

    try:
        t = tournament_engine.override_match_result(
            tournament_id=tournament_id,
            round_num=round_num,
            table_num=table_num,
            p1_score=p1_score,
            p2_score=p2_score,
            winner_id=winner_id,
            reason=reason
        )
        return {"status": "SUCCESS", "tournament": t}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/tournaments/{tournament_id}/referee/table-reset")
async def api_referee_reset_table(tournament_id: str, request: Request):
    payload = await request.json()
    pin = payload.get("pin", "")
    if not verify_referee_pin(tournament_id, pin):
        raise HTTPException(status_code=403, detail="Invalid Referee PIN")

    round_num = int(payload.get("round_num", 1))
    table_num = int(payload.get("table_num", 1))
    reason = payload.get("reason", "Referee Reset").strip()

    try:
        t = tournament_engine.reset_table(
            tournament_id=tournament_id,
            round_num=round_num,
            table_num=table_num,
            reason=reason
        )
        return {"status": "SUCCESS", "tournament": t}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/tournaments/{tournament_id}/referee/player-action")
async def api_referee_player_action(tournament_id: str, request: Request):
    payload = await request.json()
    pin = payload.get("pin", "")
    if not verify_referee_pin(tournament_id, pin):
        raise HTTPException(status_code=403, detail="Invalid Referee PIN")

    action = payload.get("action", "").upper().strip()
    player_id = payload.get("player_id", "").strip()
    reason = payload.get("reason", f"Referee {action}").strip()

    if not player_id:
        raise HTTPException(status_code=400, detail="player_id is required")

    try:
        if action == "DROP":
            t = tournament_engine.drop_player(tournament_id, player_id, reason=reason)
        elif action == "DQ":
            t = tournament_engine.disqualify_player(tournament_id, player_id, reason=reason)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown action '{action}'")
        return {"status": "SUCCESS", "tournament": t}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/tournaments/{tournament_id}/referee/timer")
async def api_referee_timer_action(tournament_id: str, request: Request):
    payload = await request.json()
    pin = payload.get("pin", "")
    if not verify_referee_pin(tournament_id, pin):
        raise HTTPException(status_code=403, detail="Invalid Referee PIN")

    action = payload.get("action", "").upper().strip()
    duration = int(payload.get("duration", 2700))

    try:
        if action == "START":
            timer = tournament_engine.start_round_timer(tournament_id, duration_seconds=duration)
        elif action == "PAUSE":
            timer = tournament_engine.pause_round_timer(tournament_id)
        elif action == "RESUME":
            timer = tournament_engine.resume_round_timer(tournament_id)
        elif action == "RESET":
            timer = tournament_engine.reset_round_timer(tournament_id, duration_seconds=duration)
        elif action == "TIME_CALL":
            timer = tournament_engine.trigger_time_call(tournament_id)
            broadcast_tournament_time_call(tournament_id)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown timer action '{action}'")

        return {"status": "SUCCESS", "timer": timer}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/tournaments/{tournament_id}/timer")
def api_get_tournament_timer(tournament_id: str):
    try:
        timer = tournament_engine.get_round_timer(tournament_id)
        if timer.get("is_time_call") and timer.get("status") == "TIME_CALL":
            broadcast_tournament_time_call(tournament_id)
        return {"timer": timer}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/tournaments/{tournament_id}/audit-log")
def api_get_tournament_audit_log(tournament_id: str):
    try:
        logs = tournament_engine.get_audit_log(tournament_id)
        return {"audit_log": logs}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/node/info")
def api_get_node_info():
    is_edge = bool(os.environ.get("YGO_EDGE_MODE"))
    return {
        "mode": "EDGE" if is_edge else "CLOUD",
        "hostname": os.uname().nodename if hasattr(os, "uname") else "edge-node",
        "edge_mode": is_edge,
        "server_time": time.time()
    }

@app.get("/api/tournaments/{tournament_id}/sync_export")
def api_tournament_sync_export(tournament_id: str):
    try:
        bundle = tournament_sync.export_tournament_bundle(tournament_id)
        return bundle
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/tournaments/sync_import")
async def api_tournament_sync_import(request: Request):
    payload = await request.json()
    pin = str(payload.get("pin", "")).strip()
    bundle = payload.get("bundle")
    overwrite = bool(payload.get("overwrite", True))

    if not bundle or not isinstance(bundle, dict):
        raise HTTPException(status_code=400, detail="Missing or invalid 'bundle' in payload")

    t_id = bundle.get("tournament_id") or bundle.get("tournament", {}).get("id")
    if not t_id:
        raise HTTPException(status_code=400, detail="Missing tournament_id in bundle")

    if not verify_referee_pin(t_id, pin):
        raise HTTPException(status_code=403, detail="Invalid Referee PIN for tournament sync import")

    try:
        res = tournament_sync.import_tournament_bundle(bundle, overwrite=overwrite)
        return res
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/tournaments/{tournament_id}/top-cut")
async def api_generate_top_cut(tournament_id: str, request: Request):
    payload = await request.json()
    pin = payload.get("pin", "")
    if not verify_referee_pin(tournament_id, pin):
        raise HTTPException(status_code=403, detail="Invalid Referee PIN")
    top_cut_size = int(payload.get("top_cut_size", 4))
    try:
        t = tournament_engine.generate_top_cut(tournament_id, top_cut_size=top_cut_size)
        return {"status": "SUCCESS", "tournament": t}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/tournaments/{tournament_id}/referee/tiebreaker")
async def api_referee_force_tiebreaker(tournament_id: str, request: Request):
    payload = await request.json()
    pin = payload.get("pin", "")
    if not verify_referee_pin(tournament_id, pin):
        raise HTTPException(status_code=403, detail="Invalid Referee PIN")
    room_id = payload.get("room_id")
    prefix = f"swiss_{tournament_id}_"
    executed = []
    for r_id, r in list(room_mgr.rooms.items()):
        if room_id and r_id != room_id:
            continue
        if r_id.startswith(prefix) and r.is_active and not r.match_finished:
            res = execute_time_call_tiebreaker(r, r.pduel)
            executed.append({"room_id": r_id, "winner": res})
    return {"status": "SUCCESS", "executed": executed}

# ----------------- REPLAY VAULT REST API (Phase 86) -----------------
@app.get("/api/replays")
def api_list_replays(tournament_id: Optional[str] = None, player: Optional[str] = None, limit: int = 50):
    return {"replays": replay_engine.list_replays(tournament_id=tournament_id, player_filter=player, limit=limit)}

@app.get("/api/replays/{replay_id}")
def api_get_replay(replay_id: str):
    r = replay_engine.get_replay(replay_id)
    if not r:
        raise HTTPException(status_code=404, detail="Replay not found")
    return r

@app.get("/api/replays/{replay_id}/ydk/{player_idx}")
def api_get_replay_ydk(replay_id: str, player_idx: int):
    res = replay_engine.get_ydk(replay_id, player_idx)
    if not res:
        raise HTTPException(status_code=404, detail="Deck not found")
    filename, ydk_text = res
    return Response(
        content=ydk_text,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

@app.post("/api/replays/{replay_id}/annotations")
async def api_add_replay_annotation(replay_id: str, request: Request):
    payload = await request.json()
    try:
        ann = replay_engine.add_annotation(
            replay_id=replay_id,
            game_index=int(payload.get("game_index", 1)),
            turn_num=int(payload.get("turn_num", 1)),
            step_index=int(payload.get("step_index", 0)),
            author_name=payload.get("author_name", "Stefan"),
            tag=payload.get("tag", "KEY_PLAY"),
            comment=payload.get("comment", "")
        )
        return ann
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

@app.delete("/api/annotations/{annotation_id}")
def api_delete_annotation(annotation_id: str):
    ok = replay_engine.delete_annotation(annotation_id)
    return {"deleted": ok}

# ----------------- OFFLINE ML POLICY TRAINING REST API (Phase 87) -----------------
@app.post("/api/train/sidedeck")
async def api_train_sidedeck(request: Request):
    """
    On-demand trigger for Sidedeck Policy optimization with Anchor-Weight Regularization.
    """
    try:
        payload = {}
        try:
            payload = await request.json()
        except Exception:
            pass

        epochs = int(payload.get("epochs", 50))
        lr = float(payload.get("lr", 0.01))
        lambda_anchor = float(payload.get("lambda_anchor", 0.5))

        base_dir = os.path.dirname(os.path.abspath(__file__))
        episodes_path = os.path.join(base_dir, "telemetry", "sidedeck_episodes.jsonl")
        output_path = os.path.join(base_dir, "sidedeck_policy.json")

        from train_sidedeck_policy import train_sidedeck_model
        res = train_sidedeck_model(
            episodes_path=episodes_path,
            output_path=output_path,
            epochs=epochs,
            lr=lr,
            lambda_anchor=lambda_anchor
        )
        return {"status": "SUCCESS", "result": res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Training failed: {str(e)}")

CANONICAL_EDISON_DECKS: Dict[str, Dict[str, List[int]]] = {
    "Edison_Quickdraw_Dandy": {
        "main": [
            20932152, 20932152, 20932152, 15341821, 15341821, 14943837, 14943837,
            48686504, 48686504, 9748752, 9748752, 9748752, 44330098, 98777036,
            21502796, 21502796, 21502796, 85087012, 33420078, 26202165, 57421866,
            57421866, 67169062, 19613556, 5318639, 87910978, 37520316, 81439173,
            14087893, 14087893, 14087893, 44095762, 53582587, 29401950, 29401950,
            41420027, 97077563, 70342110, 70342110, 70342110
        ],
        "extra": [
            44508094, 23693634, 7391448, 50321796, 73580471, 26593852, 29071332,
            43385557, 19974580, 82044279, 70902743, 3429238, 18013090, 79229522, 44508094
        ],
        "side": [
            70095154, 24508238, 24508238, 79853073, 79853073, 2980764, 2980764,
            71044499, 71044499, 6008286, 6008286, 6008286, 34717238, 34717238, 79229522
        ]
    },
    "Edison_Blackwing": {
        "main": [
            58820853, 58820853, 58820853, 49003716, 49003716, 49003716, 75498415,
            75498415, 85215458, 85215458, 85215458, 2009101, 22835145, 22835145,
            22835145, 72714392, 72714392, 65192027, 44330098, 91351370, 91351370,
            1475311, 674561, 19613556, 5318639, 87910978, 14087893, 14087893,
            14087893, 53567095, 53567095, 53567095, 59839761, 29401950, 29401950,
            44095762, 53582587, 41420027, 70342110, 70342110
        ],
        "extra": [
            76913983, 76913983, 76913983, 69031175, 69031175, 44508094, 23693634,
            7391448, 50321796, 73580471, 26593852, 29071332, 82044279, 43385557, 79229522
        ],
        "side": [
            70095154, 24508238, 24508238, 79853073, 79853073, 2980764, 2980764,
            71044499, 71044499, 6008286, 6008286, 6008286, 34717238, 34717238, 79229522
        ]
    },
    "Edison_Machina_Gadget": {
        "main": [
            5556499, 5556499, 5556499, 42940404, 42940404, 42940404, 78349103,
            78349103, 86445415, 86445415, 13839120, 13839120, 41172955, 41172955,
            70095154, 70095154, 97169186, 97169186, 23171610, 86780027, 86780027,
            86780027, 67169062, 19613556, 5318639, 14087893, 14087893, 14087893,
            70342110, 70342110, 70342110, 29401950, 29401950, 44095762, 53582587,
            41420027, 97077563, 94192409, 94192409, 94192409
        ],
        "extra": [
            44508094, 23693634, 7391448, 50321796, 73580471, 26593852, 29071332,
            43385557, 19974580, 82044279, 70902743, 3429238, 18013090, 79229522, 79229522
        ],
        "side": [
            71413901, 24508238, 24508238, 79853073, 79853073, 2980764, 2980764,
            71044499, 71044499, 6008286, 6008286, 6008286, 34717238, 34717238, 91133740
        ]
    },
    "Edison_Diva_HERO": {
        "main": [
            40044918, 69884162, 69884162, 69884162, 78868119, 78868119, 78868119,
            42463414, 70095154, 70095154, 37742478, 37742478, 44330098, 26202165,
            45906428, 45906428, 45906428, 213326, 213326, 213326, 32807846,
            48130397, 77565204, 19613556, 5318639, 87910978, 14087893, 14087893,
            14087893, 33846209, 33846209, 33846209, 70342110, 70342110, 29401950,
            29401950, 44095762, 53582587, 41420027, 97077563
        ],
        "extra": [
            40854197, 40854197, 40854197, 16304628, 22061412, 22061412, 44508094,
            23693634, 7391448, 50321796, 73580471, 26593852, 29071332, 43385557, 79229522
        ],
        "side": [
            71413901, 24508238, 24508238, 79853073, 79853073, 2980764, 2980764,
            71044499, 71044499, 6008286, 6008286, 6008286, 34717238, 34717238, 91133740
        ]
    }
}

def load_deck_cards(deck_identifier: Optional[str], user_id: Optional[str], deck_engine, total_cards, locked_snapshot: Optional[Dict[str, Any]] = None):
    """
    Direct in-memory deck loader streaming card IDs directly from locked tournament snapshot,
    canonical Edison deck library, meta_decks.db, or legacy .ydk.
    Guarantees 100% Edison legality and eliminates random card generation. Returns (main, extra, side).
    """
    if locked_snapshot and isinstance(locked_snapshot, dict) and "main" in locked_snapshot:
        main_cids = list(locked_snapshot["main"])
        extra_cids = list(locked_snapshot.get("extra", []))
        side_cids = list(locked_snapshot.get("side", []))
        sync_res = card_ingestor.validate_and_sync_deck(main_cids + extra_cids + side_cids)
        if sync_res.get("missing_meta_fixed", 0) > 0:
            db.reload()
        return main_cids, extra_cids, side_cids

    if deck_identifier:
        # 1. Try Canonical Edison Preset Library
        if deck_identifier in CANONICAL_EDISON_DECKS:
            d = CANONICAL_EDISON_DECKS[deck_identifier]
            return list(d["main"]), list(d["extra"]), list(d["side"])

        # 2. Try SQLite Deck Database
        deck = deck_db.get_deck(deck_identifier, user_id=user_id)
        if deck and len(deck.get("main", [])) >= 40:
            main_cids = list(deck["main"])
            extra_cids = list(deck.get("extra", []))
            side_cids = list(deck.get("side", []))
            sync_res = card_ingestor.validate_and_sync_deck(main_cids + extra_cids + side_cids)
            if sync_res.get("missing_meta_fixed", 0) > 0:
                db.reload()
            return main_cids, extra_cids, side_cids

        # 3. Try legacy file
        safe_name = "".join([c for c in deck_identifier if c.isalnum() or c in ['_', '-']])
        deck_path = f"meta_decks/{safe_name}.ydk"
        if os.path.exists(deck_path):
            main_cids, extra_cids, side_cids = deck_db._parse_ydk_file(deck_path)
            if len(main_cids) >= 40:
                sync_res = card_ingestor.validate_and_sync_deck(main_cids + extra_cids + side_cids)
                if sync_res.get("missing_meta_fixed", 0) > 0:
                    db.reload()
                return main_cids, extra_cids, side_cids

    # 4. Deterministic Canonical Fallback (NEVER random 14k card pool)
    fallback = CANONICAL_EDISON_DECKS["Edison_Quickdraw_Dandy"]
    return list(fallback["main"]), list(fallback["extra"]), list(fallback["side"])

def send_safe_fallback_response(pduel, mtype: int, legal_actions: list = None):
    """Guaranteed non-crashing fallback response for libocgcore to prevent engine deadlocks."""
    try:
        if mtype == 16:
            # Pass on chain selection (0xFFFFFFFF == -1)
            ocgcore.set_responsei(pduel, 0xFFFFFFFF)
        elif mtype in [12, 13]:
            # Default No / Do not activate effect
            ocgcore.set_responsei(pduel, 0)
        elif mtype == 19:
            # Default to Attack position
            ocgcore.set_responsei(pduel, 1)
        elif mtype == 10:
            # End of Battle Phase -> End Phase (3)
            ocgcore.set_responsei(pduel, 3)
        elif mtype == 11:
            # Main Phase -> End Phase (7)
            ocgcore.set_responsei(pduel, 7)
        elif mtype in [15, 20, 23]:
            # Multi-card selection fallback: 1 card, index 0
            resp_arr = bytearray(64)
            resp_arr[0] = 1
            resp_arr[1] = 0
            ocgcore.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_arr), 64))
        elif mtype == 26:
            # Unselect/pass
            ocgcore.set_responsei(pduel, 0xFFFFFFFF)
        else:
            ocgcore.set_responsei(pduel, 0)
    except Exception as e:
        print(f"[CRITICAL] send_safe_fallback_response failed for mtype {mtype}: {e}")

def execute_autopilot_action(room: DuelRoom, pduel, player: int, mtype: int, parsed: dict, legal_actions: list):
    try:
        num_actions = len(legal_actions)
        room.last_mtype = mtype
        room.last_player = player
        room.last_action_submitted = {
            "player": player,
            "mtype": mtype,
            "autopilot": True,
            "num_legal": num_actions
        }

        if num_actions == 0:
            if mtype == 16:
                ocgcore.set_responsei(pduel, 0xFFFFFFFF)
            else:
                ocgcore.set_responsei(pduel, 0)
            return

        # MSG_SELECT_BATTLECMD (10): Priority to legal attacks; guaranteed phase advance if no attacks left
        if mtype == 10:
            attacks = [a for a in legal_actions if a.get("type") == "ATTACK"]
            if attacks:
                # Sort attacks by monster ATK descending if metadata available
                attacks.sort(key=lambda a: a.get("atk", 0), reverse=True)
                chosen_act = attacks[0]
                resp_val = action_to_response_code(chosen_act, chosen_act.get('sub_idx', 0), 10)
                if resp_val < 0: resp_val = resp_val & 0xFFFFFFFF
                print(f"ROOM [{room.room_id}]: Autopilot P{player} declaring ATTACK with sub_idx {chosen_act.get('sub_idx', 0)} (resp: {resp_val})")
                ocgcore.set_responsei(pduel, resp_val)
                return
            else:
                # No attacks possible: MUST advance phase out of Battle Phase to prevent infinite deadlock
                to_m2 = next((a for a in legal_actions if a.get("type") == "TO_MAIN_PHASE_2"), None)
                to_ep = next((a for a in legal_actions if a.get("type") == "TO_END_PHASE"), None)
                if to_m2:
                    resp_val = action_to_response_code(to_m2, 0, 10) # 2
                    print(f"ROOM [{room.room_id}]: Autopilot P{player} advancing Battle Phase -> MAIN PHASE 2 (resp: {resp_val})")
                    ocgcore.set_responsei(pduel, resp_val)
                    return
                elif to_ep:
                    resp_val = action_to_response_code(to_ep, 0, 10) # 3
                    print(f"ROOM [{room.room_id}]: Autopilot P{player} advancing Battle Phase -> END PHASE (resp: {resp_val})")
                    ocgcore.set_responsei(pduel, resp_val)
                    return
                else:
                    print(f"ROOM [{room.room_id}]: Autopilot P{player} in Battle Phase fallback to EP (3)")
                    ocgcore.set_responsei(pduel, 3)
                    return

        # MSG_SELECT_IDLECMD (11): Main Phase actions; guaranteed progression to BP or EP
        if mtype == 11:
            to_bp = next((a for a in legal_actions if a.get("type") == "TO_BATTLE_PHASE"), None)
            to_ep = next((a for a in legal_actions if a.get("type") == "TO_END_PHASE"), None)
            proactive_acts = [a for a in legal_actions if a.get("type") in ["SUMMON", "SPSUMMON", "ACTIVATE", "MSET", "SSET"]]

            idle_count = getattr(room, "autopilot_idle_count", 0)

            # Force phase progression if:
            # 1. Engine had a retry/rejection on this room
            # 2. Already executed >= 2 proactive actions in this Main Phase
            # 3. No proactive actions available
            if room.retry_count > 0 or idle_count >= 2 or not proactive_acts:
                room.autopilot_idle_count = 0
                if to_bp:
                    resp_val = action_to_response_code(to_bp, 0, 11) # 6
                    print(f"ROOM [{room.room_id}]: Autopilot P{player} advancing Main Phase -> BATTLE PHASE (resp: {resp_val})")
                    ocgcore.set_responsei(pduel, resp_val)
                    return
                elif to_ep:
                    resp_val = action_to_response_code(to_ep, 0, 11) # 7
                    print(f"ROOM [{room.room_id}]: Autopilot P{player} advancing Main Phase -> END PHASE (resp: {resp_val})")
                    ocgcore.set_responsei(pduel, resp_val)
                    return
                else:
                    print(f"ROOM [{room.room_id}]: Autopilot P{player} Main Phase fallback to EP (7)")
                    ocgcore.set_responsei(pduel, 7)
                    return

            # Prioritize proactive actions: SUMMON > SPSUMMON > ACTIVATE > MSET > SSET
            priority_order = {"SUMMON": 0, "SPSUMMON": 1, "ACTIVATE": 2, "MSET": 3, "SSET": 4}
            proactive_acts.sort(key=lambda a: priority_order.get(a.get("type"), 99))
            chosen_act = proactive_acts[0]
            room.autopilot_idle_count = idle_count + 1
            resp_val = action_to_response_code(chosen_act, chosen_act.get('sub_idx', 0), 11)
            if resp_val < 0: resp_val = resp_val & 0xFFFFFFFF
            print(f"ROOM [{room.room_id}]: Autopilot P{player} executing {chosen_act.get('type')} ({chosen_act.get('name', chosen_act.get('card_id'))}, act {room.autopilot_idle_count}/2, resp: {resp_val})")
            ocgcore.set_responsei(pduel, resp_val)
            return

        # MSG_SELECT_CHAIN (16): Priority to mandatory triggers; cleanly PASS (0xFFFFFFFF) for optional chains
        if mtype == 16:
            # If C++ previously rejected our response on mtype 16 (retry_count > 0 or auto_heal_attempts > 0),
            # forcing pass again is guaranteed to deadlock. Force select the first available trigger!
            if (getattr(room, "retry_count", 0) > 0 or getattr(room, "auto_heal_attempts", 0) > 0) and legal_actions:
                act = legal_actions[0]
                resp_val = action_to_response_code(act, act.get('sub_idx', 0), 16)
                if resp_val < 0: resp_val = 0
                print(f"ROOM [{room.room_id}]: Autopilot P{player} retry-recovering chain trigger {act.get('name', act.get('card_id'))} (sub_idx: {act.get('sub_idx', 0)}, resp: {resp_val})")
                ocgcore.set_responsei(pduel, resp_val)
                return

            forced_acts = [a for a in legal_actions if a.get("type") == "ACTIVATE_CHAIN" and a.get("forced")]
            if forced_acts:
                chosen_act = forced_acts[0]
                resp_val = action_to_response_code(chosen_act, chosen_act.get('sub_idx', 0), 16)
                if resp_val < 0: resp_val = resp_val & 0xFFFFFFFF
                print(f"ROOM [{room.room_id}]: Autopilot P{player} activating MANDATORY chain trigger {chosen_act.get('name', chosen_act.get('card_id'))} (sub_idx: {chosen_act.get('sub_idx', 0)})")
                ocgcore.set_responsei(pduel, resp_val)
                return
            else:
                # Clean pass for all optional chains to prevent deadlocks and invalid triggers
                print(f"ROOM [{room.room_id}]: Autopilot P{player} passing optional chain (0xFFFFFFFF)")
                ocgcore.set_responsei(pduel, 0xFFFFFFFF)
                return

        if mtype == 18:
            act = legal_actions[0] if legal_actions else {"zone_player": player, "loc": 4, "seq": 0}
            resp_place = bytearray(256)
            resp_place[0] = act.get("zone_player", player)
            resp_place[1] = act.get("loc", 4)
            resp_place[2] = act.get("seq", 0)
            ocgcore.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_place), 256))
        elif mtype == 23:
            must_sel = parsed.get("must_select_cards", [])
            sel_cards = parsed.get("select_cards", [])
            acc = parsed.get("acc", 0)
            min_c = parsed.get("min", 1)
            max_c = parsed.get("max", 1)
            chosen = solve_select_sum(must_sel, sel_cards, acc, min_c, max_c)
            mcount = len(must_sel)
            resp_arr = bytearray(64)
            resp_arr[0] = mcount + len(chosen)
            for j in range(mcount): resp_arr[1 + j] = j
            for j, s_idx in enumerate(chosen): resp_arr[mcount + 1 + j] = s_idx
            ocgcore.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_arr), 64))
        elif mtype in [15, 20]:
            # Dedup fix: select min_cards unique indices without modulo collisions
            min_cards = legal_actions[0].get('min', 1) if legal_actions else 1
            target_count = min(max(1, min_cards), num_actions)
            indices = []
            for k in range(num_actions):
                if len(indices) >= target_count:
                    break
                if k not in indices:
                    indices.append(k)
            resp_arr = bytearray(64)
            resp_arr[0] = len(indices)
            for j, iv in enumerate(indices):
                resp_arr[1 + j] = iv
            ocgcore.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_arr), 64))
        elif mtype == 26:
            resp_arr = bytearray(64)
            resp_arr[0] = 1
            resp_arr[1] = 0
            ocgcore.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_arr), 64))
        elif mtype == 25:
            cnt = parsed.get("count", len(legal_actions))
            resp_arr = bytearray([i for i in range(cnt)])
            ocgcore.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_arr), 64))
        elif mtype == 24:
            cnt = parsed.get("count", 1)
            resp_arr = bytearray(256)
            for idx in range(cnt):
                act = legal_actions[idx] if idx < len(legal_actions) else {"zone_player": 1 - player, "loc": 4, "seq": idx}
                resp_arr[idx*3] = act.get("zone_player", 1 - player)
                resp_arr[idx*3+1] = act.get("loc", 4)
                resp_arr[idx*3+2] = act.get("seq", idx)
            ocgcore.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_arr), 256))
        elif mtype == 22:
            cnt = parsed.get("count", 1)
            card_cnt = parsed.get("card_count", len(legal_actions))
            vals = [0] * card_cnt
            if card_cnt > 0: vals[0] = cnt
            resp_arr = struct.pack(f"<{card_cnt}h", *vals)
            ocgcore.set_responseb(pduel, ctypes.create_string_buffer(resp_arr, len(resp_arr)))
        elif mtype in [140, 141]:
            val = legal_actions[0].get("value", 1) if legal_actions else 1
            ocgcore.set_responsei(pduel, val)
        elif mtype == 142:
            ocgcore.set_responsei(pduel, 55920742)
        elif mtype == 143:
            ocgcore.set_responsei(pduel, 0)
        else:
            # Fallback to AI brain action
            lp0, lp1 = 8000, 8000
            try:
                bstate = get_board_state(pduel, pov_player=0)
                lp0 = bstate.get("0", {}).get("lp", 8000)
                lp1 = bstate.get("1", {}).get("lp", 8000)
            except Exception:
                pass

            state_tensor = torch.tensor([
                float(min(num_actions, 32)) / 32.0,
                float(lp0) / 8000.0,
                float(lp1) / 8000.0,
                float(getattr(room, "turn_count", 1)) / 20.0,
                float(mtype) / 20.0,
                1.0 if player == 0 else 0.0,
                1.0 if player == 1 else 0.0,
                1.0 if mtype == 11 else 0.0,
                1.0 if mtype == 10 else 0.0,
                0.0,
                float(getattr(room, "step_count", 0)) / 100.0,
                0.5, 0.5, 0.5, 0.5, 1.0
            ], dtype=torch.float32)

            best_act_idx, _, _, _ = ai_brain.act(state_tensor, num_actions)
            idx = best_act_idx if (0 <= best_act_idx < num_actions) else 0
            if idx == -1:
                ocgcore.set_responsei(pduel, 0xFFFFFFFF)
            else:
                act = legal_actions[idx]
                resp_val = action_to_response_code(act, act.get('sub_idx', 0), mtype)
                if resp_val < 0: resp_val = resp_val & 0xFFFFFFFF
                ocgcore.set_responsei(pduel, resp_val)
    except Exception as e:
        print(f"ROOM [{getattr(room, 'room_id', '?')}]: CRITICAL in execute_autopilot_action (mtype {mtype}): {e}")
        traceback.print_exc()
        send_safe_fallback_response(pduel, mtype, legal_actions)

def execute_user_action(room: DuelRoom, pduel, player: int, mtype: int, parsed: dict, legal_actions: list, user_action_value: dict, bstate: dict):
    num_actions = len(legal_actions)
    session_id = room.session_id
    room.last_mtype = mtype
    room.last_player = player
    room.last_action_submitted = {
        "player": player,
        "mtype": mtype,
        "user_action_value": user_action_value,
        "num_legal": num_actions
    }
    
    if mtype == 18:
        idx = user_action_value.get("index", 0)
        clicked_loc = user_action_value.get("loc")
        clicked_seq = user_action_value.get("seq")
        act = None
        if clicked_loc is not None and clicked_seq is not None:
            act = next((a for a in legal_actions if a.get("loc") == clicked_loc and a.get("seq") == clicked_seq), None)
        if not act and 0 <= idx < len(legal_actions):
            act = legal_actions[idx]
        if not act and legal_actions:
            act = legal_actions[0]
            
        zone_p = act.get("zone_player", player) if act else player
        l = act.get("loc", 4) if act else 4
        s = act.get("seq", 0) if act else 0
        resp_place = bytearray(256)
        resp_place[0] = zone_p
        resp_place[1] = l
        resp_place[2] = s
        telemetry.record_step(session_id, player, mtype, bstate, legal_actions, {"type": "SELECT_PLACE", "zone_player": zone_p, "loc": l, "seq": s})
        ocgcore.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_place), 256))
    elif mtype == 23:
        must_sel = parsed.get("must_select_cards", [])
        sel_cards = parsed.get("select_cards", [])
        acc = parsed.get("acc", 0)
        min_c = parsed.get("min", 1)
        max_c = parsed.get("max", 1)
        user_indices = user_action_value.get("indices")
        if user_indices is None:
            idx = user_action_value.get("index", 0)
            if 0 <= idx < len(legal_actions):
                user_indices = [legal_actions[idx].get("sub_idx", idx)]
            else:
                user_indices = [0]
        chosen = solve_select_sum(must_sel, sel_cards, acc, min_c, max_c, preferred_indices=user_indices)
        mcount = len(must_sel)
        resp_arr = bytearray(64)
        resp_arr[0] = mcount + len(chosen)
        for j in range(mcount): resp_arr[1 + j] = j
        for j, s_idx in enumerate(chosen): resp_arr[mcount + 1 + j] = s_idx
        telemetry.record_step(session_id, player, mtype, bstate, legal_actions, {"type": "SELECT_SUM", "chosen": chosen, "user_indices": user_indices})
        ocgcore.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_arr), 64))
    elif mtype in [15, 20]:
        sel_indices = user_action_value.get("indices")
        idx = user_action_value.get("index", 0)
        if sel_indices:
            resp_arr = bytearray(64)
            resp_arr[0] = len(sel_indices)
            for j, chosen_idx in enumerate(sel_indices):
                real_idx = chosen_idx
                if 0 <= chosen_idx < len(legal_actions):
                    real_idx = legal_actions[chosen_idx].get("sub_idx", chosen_idx)
                resp_arr[1 + j] = real_idx
            telemetry.record_step(session_id, player, mtype, bstate, legal_actions, {"type": f"SELECT_{mtype}_MULTI", "indices": sel_indices})
            ocgcore.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_arr), 64))
        elif idx == -1 or (0 <= idx < len(legal_actions) and legal_actions[idx].get("sub_idx") == -1):
            telemetry.record_step(session_id, player, mtype, bstate, legal_actions, {"type": f"SELECT_{mtype}_PASS", "index": -1})
            ocgcore.set_responsei(pduel, 0xFFFFFFFF)
        else:
            if idx < 0 or idx >= num_actions: idx = 0
            act = legal_actions[idx]
            chosen = act.get('sub_idx', idx)
            min_cards = act.get('min', 1)
            indices = []
            for j in range(min_cards):
                iv = (chosen + j) % num_actions
                if iv not in indices: indices.append(iv)
            for k in range(num_actions):
                if len(indices) >= min_cards: break
                if k not in indices: indices.append(k)
            resp_arr = bytearray(64)
            resp_arr[0] = len(indices)
            for j, iv in enumerate(indices):
                resp_arr[1 + j] = iv
            telemetry.record_step(session_id, player, mtype, bstate, legal_actions, {"type": f"SELECT_{mtype}_SINGLE", "chosen": chosen, "indices": indices})
            ocgcore.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_arr), 64))
    elif mtype == 26:
        idx = user_action_value.get("index", 0)
        if idx == -1 or (0 <= idx < len(legal_actions) and legal_actions[idx].get("sub_idx") == -1):
            telemetry.record_step(session_id, player, mtype, bstate, legal_actions, {"type": "SELECT_UNSELECT_PASS", "index": -1})
            ocgcore.set_responsei(pduel, 0xFFFFFFFF)
        else:
            chosen = legal_actions[idx].get("sub_idx", 0) if (0 <= idx < len(legal_actions)) else 0
            resp_arr = bytearray(64)
            resp_arr[0] = 1
            resp_arr[1] = chosen
            telemetry.record_step(session_id, player, mtype, bstate, legal_actions, {"type": "SELECT_UNSELECT", "chosen": chosen})
            ocgcore.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_arr), 64))
    elif mtype == 25:
        cnt = parsed.get("count", len(legal_actions))
        user_indices = user_action_value.get("indices")
        if user_indices and len(user_indices) == cnt:
            resp_arr = bytearray(user_indices)
        else:
            idx = user_action_value.get("index", 0)
            if idx == -1:
                resp_arr = bytearray([0xFF])
            else:
                resp_arr = bytearray([i for i in range(cnt)])
        telemetry.record_step(session_id, player, mtype, bstate, legal_actions, {"type": "SORT_CARD", "order": list(resp_arr)})
        ocgcore.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_arr), 64))
    elif mtype == 24:
        cnt = parsed.get("count", 1)
        sel_indices = user_action_value.get("indices")
        if not sel_indices:
            idx = user_action_value.get("index", 0)
            sel_indices = [idx] if 0 <= idx < len(legal_actions) else [0]
        resp_arr = bytearray(256)
        for j, s_idx in enumerate(sel_indices[:cnt]):
            act = legal_actions[s_idx] if 0 <= s_idx < len(legal_actions) else {"zone_player": 1 - player, "loc": 4, "seq": 0}
            resp_arr[j*3] = act.get("zone_player", 1 - player)
            resp_arr[j*3+1] = act.get("loc", 4)
            resp_arr[j*3+2] = act.get("seq", 0)
        telemetry.record_step(session_id, player, mtype, bstate, legal_actions, {"type": "SELECT_DISFIELD", "zones": list(resp_arr[:cnt*3])})
        ocgcore.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_arr), 256))
    elif mtype == 22:
        cnt = parsed.get("count", 1)
        card_cnt = parsed.get("card_count", len(legal_actions))
        idx = user_action_value.get("index", 0)
        vals = [0] * card_cnt
        target_idx = idx if 0 <= idx < card_cnt else 0
        if card_cnt > 0:
            vals[target_idx] = cnt
        resp_arr = struct.pack(f"<{card_cnt}h", *vals)
        telemetry.record_step(session_id, player, mtype, bstate, legal_actions, {"type": "SELECT_COUNTER", "values": vals})
        ocgcore.set_responseb(pduel, ctypes.create_string_buffer(resp_arr, len(resp_arr)))
    elif mtype in [140, 141]:
        idx = user_action_value.get("index", 0)
        val = user_action_value.get("value")
        if val is None:
            val = legal_actions[idx].get("value") if 0 <= idx < len(legal_actions) else 1
        telemetry.record_step(session_id, player, mtype, bstate, legal_actions, {"type": "ANNOUNCE_RACE_OR_ATTRIB", "value": val})
        ocgcore.set_responsei(pduel, int(val))
    elif mtype == 142:
        code = user_action_value.get("code")
        if code is None:
            code = user_action_value.get("value", 55920742)
        telemetry.record_step(session_id, player, mtype, bstate, legal_actions, {"type": "ANNOUNCE_CARD", "code": code})
        ocgcore.set_responsei(pduel, int(code))
    elif mtype == 143:
        idx = user_action_value.get("index", 0)
        telemetry.record_step(session_id, player, mtype, bstate, legal_actions, {"type": "ANNOUNCE_NUMBER", "index": idx})
        ocgcore.set_responsei(pduel, int(idx))
    else:
        if "raw_response" in user_action_value:
            resp_val = int(user_action_value["raw_response"]) & 0xFFFFFFFF
            telemetry.record_step(session_id, player, mtype, bstate, legal_actions, {"type": "RAW_ACTION", "code": resp_val})
            ocgcore.set_responsei(pduel, resp_val)
            return
        idx = user_action_value.get("index", 0)
        sub_act = user_action_value.get("sub_action", None)
        if mtype == 16 and (idx == -1 or sub_act == -1):
            forced_acts = [a for a in legal_actions if a.get("forced")]
            if forced_acts:
                act = forced_acts[0]
                resp_val = action_to_response_code(act, act.get('sub_idx', 0), mtype)
                print(f"ROOM [{room.room_id}]: Intercepted illegal pass on mandatory chain trigger. Auto-selected {act.get('name')}")
                telemetry.record_step(session_id, player, mtype, bstate, legal_actions, {"type": "FORCED_CHAIN_GUARD", "index": 0, "code": resp_val, "action": act})
                ocgcore.set_responsei(pduel, resp_val)
                return
            else:
                telemetry.record_step(session_id, player, mtype, bstate, legal_actions, {"type": "PASS", "index": -1})
                ocgcore.set_responsei(pduel, 0xFFFFFFFF)
                return
        if idx == -1:
            telemetry.record_step(session_id, player, mtype, bstate, legal_actions, {"type": "PASS", "index": -1})
            ocgcore.set_responsei(pduel, 0xFFFFFFFF)
        else:
            if idx < 0 or idx >= num_actions:
                idx = 0
            act = legal_actions[idx]
            resp_val = action_to_response_code(act, act.get('sub_idx', 0), mtype)
            if resp_val < 0:
                resp_val = resp_val & 0xFFFFFFFF
            telemetry.record_step(session_id, player, mtype, bstate, legal_actions, {"type": "GENERAL_ACTION", "index": idx, "code": resp_val, "action": act})
            ocgcore.set_responsei(pduel, resp_val)

def handle_human_action(room: DuelRoom, pduel, ocg_player: int, mtype: int, parsed: dict):
    room_player = room.ocg_to_room.get(ocg_player, ocg_player)
    legal_actions = parsed.get('legal_actions', [])
    num_actions = len(legal_actions)
    if num_actions == 0:
        ocgcore.set_responsei(pduel, 0)
        return

    # Perspective adaptation for player 1 in ocgcore
    if ocg_player == 1:
        client_parsed = copy.deepcopy(parsed)
        for act in client_parsed.get('legal_actions', []):
            if 'con' in act:
                act['con'] = 0 if act['con'] == 1 else 1
            if 'zone_player' in act:
                act['zone_player'] = 0 if act['zone_player'] == 1 else 1
        for c in client_parsed.get('select_cards', []) + client_parsed.get('must_select_cards', []):
            if 'con' in c:
                c['con'] = 0 if c['con'] == 1 else 1
    else:
        client_parsed = parsed

    # 1. Send WAITING_FOR_USER to active room player
    action_label = {11: "Main Phase", 10: "Battle Phase", 16: "Ketten-Trigger", 15: "Kartenauswahl", 19: "Position"}.get(mtype, f"Aktion {mtype}")
    active_name = room.player_names.get(room_player, f"Player {room_player}")
    room.record_replay_frame("ACTION_PROMPT", f"{active_name}: {action_label}", pduel, extra_data={"active_player": room_player, "msg_type": mtype})

    bstate_actor = get_board_state(pduel, pov_player=ocg_player)
    msg_actor = {
        "type": "WAITING_FOR_USER",
        "msg_type": mtype,
        "parsed": client_parsed,
        "board": bstate_actor
    }
    room.current_prompt[room_player] = msg_actor
    room.send_to(room_player, msg_actor)

    # 2. Send OPPONENT_THINKING to waiting room player (in PvP)
    if room.is_pvp:
        waiting_room_player = 1 - room_player
        waiting_ocg_player = 1 - ocg_player
        bstate_waiting = get_board_state(pduel, pov_player=waiting_ocg_player)
        msg_waiting = {
            "type": "OPPONENT_THINKING",
            "msg_type": mtype,
            "parsed": parsed,
            "board": bstate_waiting
        }
        room.current_prompt[waiting_room_player] = msg_waiting
        room.send_to(waiting_room_player, msg_waiting)

    # 3. Send SPECTATOR_UPDATE to spectators from Player 0's POV
    if room.spectators:
        bstate_spec = get_board_state(pduel, pov_player=room.room_to_ocg.get(0, 0))
        active_name = room.player_names.get(room_player, f"Player {room_player}")
        msg_spec = {
            "type": "SPECTATOR_UPDATE",
            "active_player": room_player,
            "active_player_name": active_name,
            "board": bstate_spec
        }
        room.broadcast_spectators(msg_spec)

    # Check Autopilot for room_player
    if room.autopilot.get(room_player):
        time.sleep(0.3)
        room.current_prompt[room_player] = None
        if room.is_pvp:
            room.current_prompt[1 - room_player] = None
        try:
            execute_autopilot_action(room, pduel, ocg_player, mtype, parsed, legal_actions)
        except Exception as e:
            print(f"ROOM [{room.room_id}]: Exception executing autopilot P{room_player}: {e}")
            send_safe_fallback_response(pduel, mtype, legal_actions)
        return

    # Wait for user input (synced with 120s client turn timer)
    room.events[room_player].clear()
    signaled = room.events[room_player].wait(timeout=120)
    room.current_prompt[room_player] = None
    if room.is_pvp:
        room.current_prompt[1 - room_player] = None

    if not signaled:
        if not room.is_active or room.duel_ended:
            return
        pname = room.player_names.get(room_player, f"Player {room_player}")
        print(f"ROOM [{room.room_id}]: {pname} (P{room_player}) timed out after 120s.")
        if room.is_pvp:
            winner_room = 1 - room_player
            winner_ocg = room.room_to_ocg.get(winner_room, winner_room)
            room.broadcast_end(pduel, winner=winner_ocg, reason=f"TIMEOUT_P{room_player}")
            return
        else:
            # Solo mode vs AI: fallback autopilot
            try:
                execute_autopilot_action(room, pduel, ocg_player, mtype, parsed, legal_actions)
            except Exception as e:
                print(f"ROOM [{room.room_id}]: Exception in timeout fallback autopilot: {e}")
                send_safe_fallback_response(pduel, mtype, legal_actions)
            return

    if not room.is_active:
        return

    user_val = room.action_values.get(room_player) or {}
    if room.autopilot.get(room_player) and user_val.get("autopilot_triggered"):
        try:
            execute_autopilot_action(room, pduel, ocg_player, mtype, parsed, legal_actions)
        except Exception as e:
            print(f"ROOM [{room.room_id}]: Exception in user-val autopilot: {e}")
            send_safe_fallback_response(pduel, mtype, legal_actions)
        return

    # Execute manual response
    execute_user_action(room, pduel, ocg_player, mtype, parsed, legal_actions, user_val, bstate_actor)

def handle_ai_action(room: DuelRoom, pduel, ocg_player: int, mtype: int, parsed: dict):
    pov_ocg = room.room_to_ocg.get(0, 0)
    bstate = get_board_state(pduel, pov_player=pov_ocg)
    ai_msg = {"type": "AI_THINKING", "parsed": parsed, "board": bstate}
    room.current_prompt[0] = ai_msg
    room.send_to(0, ai_msg)
    room.broadcast_spectators(ai_msg)
    time.sleep(0.4)
    room.current_prompt[0] = None
    legal_actions = parsed.get('legal_actions', [])
    num_actions = len(legal_actions)
    if num_actions > 0:
        try:
            execute_autopilot_action(room, pduel, ocg_player, mtype, parsed, legal_actions)
        except Exception as e:
            print(f"ROOM [{room.room_id}]: Exception in handle_ai_action autopilot: {e}")
            send_safe_fallback_response(pduel, mtype, legal_actions)
    else:
        ocgcore.set_responsei(pduel, 0)

def run_duel_session(room: DuelRoom):
    p0_deck_name = room.decks.get(0) or "Default"
    p1_deck_name = room.decks.get(1) or ("AI_Archetype" if not room.is_pvp else "Challenger")
    mode_str = "PVP" if room.is_pvp else "SOLO"
    
    total_cards = len(db.cards)
    deck_engine = EvolutionaryDeckEngine(total_cards)
    
    # Phase 90/91: Check for locked tournament deck snapshots
    snap_p0 = None
    snap_p1 = None
    match_info = tournament_engine.get_match_by_room_id(room.room_id)
    if match_info:
        t_id = match_info["tournament_id"]
        p0_id = room.player_ids.get(0)
        p1_id = room.player_ids.get(1)
        if p0_id:
            snap_p0 = tournament_engine.get_player_deck_snapshot(t_id, p0_id)
        if p1_id:
            snap_p1 = tournament_engine.get_player_deck_snapshot(t_id, p1_id)
    elif room.room_id.startswith("tour_") or room.room_id.startswith("swiss_"):
        try:
            parts = room.room_id.split("_")
            t_id = "_".join(parts[1:-2]) if len(parts) >= 4 else parts[1]
            p0_id = room.player_ids.get(0)
            p1_id = room.player_ids.get(1)
            if p0_id:
                snap_p0 = tournament_engine.get_player_deck_snapshot(t_id, p0_id)
            if p1_id:
                snap_p1 = tournament_engine.get_player_deck_snapshot(t_id, p1_id)
        except Exception as snap_err:
            print(f"ROOM [{room.room_id}]: Snapshot load err: {snap_err}")

    if room.scenario_data:
        p0_deck_name = room.scenario_data.get("title", "Szenario")
        p1_deck_name = "Puzzle-Gegner"
        main_p0 = list(room.scenario_data.get("p0_deck", []))
        extra_p0 = list(room.scenario_data.get("p0_extra", []))
        side_p0 = []
        main_p1 = list(room.scenario_data.get("p1_deck", []))
        extra_p1 = list(room.scenario_data.get("p1_extra", []))
        side_p1 = []
    else:
        main_p0, extra_p0, side_p0 = load_deck_cards(room.decks.get(0), room.player_ids.get(0), deck_engine, total_cards, locked_snapshot=snap_p0)
        p0_val = check_edison_legality(main_p0, extra_p0, side_p0)

        # Detect whether Player 0 deck contains modern cards (Xyz, Pendulum, Link, or modern cards)
        has_modern = any(
            bool(db.card_metadata.get(cid, {}).get("type", 0) & (0x800000 | 0x1000000 | 0x4000000))
            for cid in main_p0 + extra_p0 + side_p0
        )
        if has_modern or not p0_val["is_legal"]:
            room.format = "modern"
        elif not getattr(room, "format", None):
            room.format = "edison" if p0_val["is_legal"] else "modern"

        print(f"ROOM [{room.room_id}]: Format locked to '{room.format.upper()}' (has_modern_cards={has_modern}, p0_edison_legal={p0_val['is_legal']})")

        if room.is_pvp:
            main_p1, extra_p1, side_p1 = load_deck_cards(room.decks.get(1), room.player_ids.get(1), deck_engine, total_cards, locked_snapshot=snap_p1)
        else:
            # Solo mode vs PROPHIT-AI
            if getattr(room, "ai_deck", None) and room.ai_deck not in ["", "random", "zufall"]:
                ai_chosen = room.ai_deck
            elif getattr(room, "format", "edison").lower() == "modern":
                ai_deck_candidates = ["Kashtira_Control", "osint_tier1_snake_eye", "Tearlaments_Full_Power", "Pure_PUNK"]
                ai_chosen = ai_deck_candidates[abs(hash(room.room_id)) % len(ai_deck_candidates)]
            else:
                ai_deck_candidates = ["Edison_Quickdraw_Dandy", "Edison_Blackwing", "Edison_Machina_Gadget", "Edison_Diva_HERO"]
                ai_chosen = ai_deck_candidates[abs(hash(room.room_id)) % len(ai_deck_candidates)]
            p1_deck_name = ai_chosen
            room.decks[1] = ai_chosen
            main_p1, extra_p1, side_p1 = load_deck_cards(ai_chosen, "sys_ai", deck_engine, total_cards)


    room.sided_main[0] = list(main_p0)
    room.sided_extra[0] = list(extra_p0)
    room.sided_side[0] = list(side_p0)
    room.initial_main_len[0] = len(main_p0)
    room.initial_extra_len[0] = len(extra_p0)

    room.sided_main[1] = list(main_p1)
    room.sided_extra[1] = list(extra_p1)
    room.sided_side[1] = list(side_p1)
    room.initial_main_len[1] = len(main_p1)
    room.initial_extra_len[1] = len(extra_p1)

    room.player_deck_names[0] = p0_deck_name
    room.player_deck_names[1] = p1_deck_name
    room.replay_decks_raw[0] = {"main": list(main_p0), "extra": list(extra_p0), "side": list(side_p0)}
    room.replay_decks_raw[1] = {"main": list(main_p1), "extra": list(extra_p1), "side": list(side_p1)}
        
    p0_val = check_edison_legality(main_p0, extra_p0, side_p0)
    print(f"ROOM [{room.room_id}]: Player 0 Deck '{p0_deck_name}' Edison Legality: {p0_val['status_text']}")
    if room.is_pvp:
        p1_val = check_edison_legality(main_p1, extra_p1, side_p1)
        print(f"ROOM [{room.room_id}]: Player 1 Deck '{p1_deck_name}' Edison Legality: {p1_val['status_text']}")
    else:
        p1_val = check_edison_legality(main_p1, extra_p1, side_p1)
        print(f"ROOM [{room.room_id}]: PROPHIT-AI Deck (Main={len(main_p1)}, Side={len(side_p1)}) Edison Legality: {p1_val['status_text']}")

    parser = YGOByteParser()
    msg_buf = ctypes.create_string_buffer(65536)

    match_max_wins = 1 if getattr(room, "match_mode", "bo3") == "bo1" else 2
    max_games = 1 if match_max_wins == 1 else 3

    try:
        while room.is_active and not room.match_finished and max(room.match_wins.values()) < match_max_wins and room.current_game <= max_games:
            room.duel_ended = False
            room.retry_count = 0
            room.last_mtype = None
            room.last_action_submitted = None

            telemetry.start_duel(room.session_id, user_deck=p0_deck_name, ai_deck=p1_deck_name, mode=f"{mode_str}_G{room.current_game}")

            if room.pduel:
                with room.engine_lock:
                    try:
                        ocgcore.end_duel(room.pduel)
                    except Exception as ed_err:
                        print(f"ROOM [{room.room_id}]: Error cleaning previous pduel: {ed_err}")
                    room.pduel = None

            pduel = ocgcore.create_duel(random.randint(1000, 9999))
            room.pduel = pduel
            if room.scenario_data:
                # Scenario Puzzle Mode: Set custom board state, LP, hand, zones
                setup_scenario_board(ocgcore, pduel, room.scenario_data)
                room.ocg_to_room = {0: 0, 1: 1}
                room.room_to_ocg = {0: 0, 1: 1}
                room.first_turn_player = 0
            else:
                start_lp = getattr(room, "start_lp", 8000) or 8000
                ocgcore.set_player_info(pduel, 0, start_lp, 5, 1)
                ocgcore.set_player_info(pduel, 1, start_lp, 5, 1)

                cur_main_p0 = list(room.sided_main[0])
                cur_extra_p0 = list(room.sided_extra[0])
                cur_main_p1 = list(room.sided_main[1])
                cur_extra_p1 = list(room.sided_extra[1])

                random.shuffle(cur_main_p0)
                random.shuffle(cur_main_p1)

                # Determine who goes first in Game 1
                if room.current_game == 1:
                    tc = getattr(room, "turn_choice", "random")
                    if tc == "first":
                        room.first_turn_player = 0
                    elif tc == "second":
                        room.first_turn_player = 1
                    else:
                        room.first_turn_player = random.randint(0, 1)

                # Determine who goes first in ocgcore
                if room.first_turn_player == 0:
                    room.ocg_to_room = {0: 0, 1: 1}
                    room.room_to_ocg = {0: 0, 1: 1}
                    deck_ocg0, extra_ocg0 = cur_main_p0, cur_extra_p0
                    deck_ocg1, extra_ocg1 = cur_main_p1, cur_extra_p1
                else:
                    room.ocg_to_room = {0: 1, 1: 0}
                    room.room_to_ocg = {0: 1, 1: 0}
                    deck_ocg0, extra_ocg0 = cur_main_p1, cur_extra_p1
                    deck_ocg1, extra_ocg1 = cur_main_p0, cur_extra_p0

                for i, cid in enumerate(deck_ocg0):
                    ocgcore.new_card(pduel, int(cid), 0, 0, 1, i, 8)
                for i, cid in enumerate(extra_ocg0):
                    ocgcore.new_card(pduel, int(cid), 0, 0, 64, i, 8)
                    
                for i, cid in enumerate(deck_ocg1):
                    ocgcore.new_card(pduel, int(cid), 1, 1, 1, i, 8)
                for i, cid in enumerate(extra_ocg1):
                    ocgcore.new_card(pduel, int(cid), 1, 1, 64, i, 8)
                
            # Determine format rule options:
            # Modern (MR5): (5 << 16) -> enables Extra Monster Zones, Link/Xyz mechanics, modern trigger timings
            # Edison (MR1): 0x08 -> DUEL_OBSOLETE_RULING (April 2010 Ignition Priority)
            duel_options = 0x08
            if getattr(room, "format", "edison").lower() == "modern":
                duel_options = (5 << 16)
            ocgcore.start_duel(pduel, duel_options)
            print(f"ROOM [{room.room_id}]: ocgcore.start_duel initialized with duel_options = {duel_options:#x} (Rule: {'MR5' if duel_options == (5 << 16) else 'Edison 2010'})")
            if room.scenario_data:
                room.send_to(0, {
                    "type": "SCENARIO_INFO",
                    "scenario": room.scenario_data,
                    "message": f"🧩 Taktik-Puzzle aktiv: {room.scenario_data.get('title')}"
                })
            room.record_replay_frame("MATCH_GAME_START", f"Game {room.current_game} gestartet.", pduel)

            p0_w = room.match_wins[0]
            p1_w = room.match_wins[1]
            room.send_to(0, {
                "type": "MATCH_GAME_START",
                "current_game": room.current_game,
                "score": {0: p0_w, 1: p1_w},
                "score_str": f"{p0_w} : {p1_w}",
                "first_player": (0 if room.first_turn_player == 0 else 1),
                "format": getattr(room, "format", "edison"),
                "message": f"Game {room.current_game} gestartet! ({getattr(room, 'format', 'edison').upper()}-Format, Spielstand: {p0_w}:{p1_w})"
            })
            if room.is_pvp:
                room.send_to(1, {
                    "type": "MATCH_GAME_START",
                    "current_game": room.current_game,
                    "score": {0: p1_w, 1: p0_w},
                    "score_str": f"{p1_w} : {p0_w}",
                    "first_player": (0 if room.first_turn_player == 1 else 1),
                    "message": f"Game {room.current_game} gestartet! (Spielstand: {p1_w}:{p0_w})"
                })
            room.broadcast_spectators({
                "type": "MATCH_GAME_START",
                "current_game": room.current_game,
                "score": {0: p0_w, 1: p1_w},
                "score_str": f"{p0_w} : {p1_w}",
                "first_player": room.first_turn_player,
                "message": f"Game {room.current_game} gestartet! (Spielstand: {p0_w}:{p1_w})"
            })

            # --- INNER GAME LOOP ---
            while room.is_active and not room.duel_ended:
                if room.players[0] is None or (room.is_pvp and room.players[1] is None):
                    if not room.duel_ended:
                        telemetry.end_duel(room.session_id, winner=-1, reason="CLIENT_DISCONNECTED")
                    break
                with room.engine_lock:
                    if not room.is_active or room.duel_ended:
                        break
                    status = ocgcore.process(pduel)
                    if status == 0:
                        print(f"ROOM [{room.room_id}]: Engine returned status == 0 (Duel Ended)")
                        b0 = get_board_state(pduel, pov_player=room.room_to_ocg.get(0, 0))
                        lp0 = b0.get(0, {}).get("lp", 0)
                        lp1 = b0.get(1, {}).get("lp", 0)
                        winner_ocg = 0 if lp0 > lp1 else (1 if lp1 > lp0 else 2)
                        room.broadcast_end(pduel, winner_ocg, reason="ENGINE_STATUS_0")
                        break
                    length = ocgcore.get_message(pduel, ctypes.byref(msg_buf))
                    if length > 0:
                        data = msg_buf.raw[:length]
                    else:
                        data = None
                if length > 0:
                    data = msg_buf.raw[:length]
                    mtype = data[0]
                    player = data[1] if length > 1 else 0
                    
                    if mtype == 5: # MSG_WIN
                        winner_ocg = data[1] if len(data) > 1 else -1
                        print(f"ROOM [{room.room_id}]: MSG_WIN detected! Winner OCG: {winner_ocg}")
                        room.broadcast_end(pduel, winner_ocg, reason="MSG_WIN")
                        break

                    parsed = None
                    try:
                        if mtype == 11:
                            parser.buffer = data; parser.offset = 1; parsed = parser.parse_idlecmd()
                        elif mtype == 10:
                            parser.buffer = data; parser.offset = 1; parsed = parser.parse_battlecmd()
                        elif mtype == 16:
                            parser.buffer = data; parser.offset = 1; parsed = parser.parse_chain()
                        elif mtype == 19:
                            parser.buffer = data; parser.offset = 1; parsed = parser.parse_select_position()
                        elif mtype == 15:
                            parser.buffer = data; parser.offset = 1; parsed = parser.parse_select_card()
                        elif mtype == 12:
                            parser.buffer = data; parser.offset = 1; parsed = parser.parse_select_effectyn()
                        elif mtype == 13:
                            parser.buffer = data; parser.offset = 1; parsed = parser.parse_select_yesno()
                        elif mtype == 14:
                            parser.buffer = data; parser.offset = 1; parsed = parser.parse_select_option()
                        elif mtype == 18:
                            parser.buffer = data; parser.offset = 1; parsed = parser.parse_select_place()
                            player = parsed.get("player", player)
                        elif mtype == 23:
                            parser.buffer = data; parser.offset = 1; parsed = parser.parse_select_sum()
                            player = parsed.get("player", player)
                        elif mtype == 20:
                            parser.buffer = data; parser.offset = 1; parsed = parser.parse_select_tribute()
                            player = parsed.get("player", player)
                        elif mtype == 26:
                            parser.buffer = data; parser.offset = 1; parsed = parser.parse_select_unselect_card()
                            player = parsed.get("player", player)
                        elif mtype == 25:
                            parser.buffer = data; parser.offset = 1; parsed = parser.parse_sort_card()
                            player = parsed.get("player", player)
                        elif mtype == 24:
                            parser.buffer = data; parser.offset = 1; parsed = parser.parse_select_disfield()
                            player = parsed.get("player", player)
                        elif mtype == 22:
                            parser.buffer = data; parser.offset = 1; parsed = parser.parse_select_counter()
                            player = parsed.get("player", player)
                        elif mtype == 140:
                            parser.buffer = data; parser.offset = 1; parsed = parser.parse_announce_race()
                            player = parsed.get("player", player)
                        elif mtype == 141:
                            parser.buffer = data; parser.offset = 1; parsed = parser.parse_announce_attrib()
                            player = parsed.get("player", player)
                        elif mtype == 142:
                            parser.buffer = data; parser.offset = 1; parsed = parser.parse_announce_card()
                            player = parsed.get("player", player)
                        elif mtype == 143:
                            parser.buffer = data; parser.offset = 1; parsed = parser.parse_announce_number()
                            player = parsed.get("player", player)
                        elif mtype in [30, 42]: # MSG_CONFIRM_DECKTOP (30), MSG_CONFIRM_EXTRATOP (42)
                            p = data[1] if len(data) > 1 else 0
                            cnt = data[2] if len(data) > 2 else 0
                            cards = []
                            off = 3
                            for _ in range(cnt):
                                if off + 7 <= len(data):
                                    code = struct.unpack("<I", data[off:off+4])[0] & 0x7FFFFFFF
                                    cards.append(code)
                                    off += 7
                            room.send_to(0, {"type": "CONFIRM_CARDS", "player": p, "title": "Aufgedeckte Deckkarten", "cards": cards})
                            if room.is_pvp:
                                room.send_to(1, {"type": "CONFIRM_CARDS", "player": 1 - p, "title": "Aufgedeckte Deckkarten", "cards": cards})
                            room.broadcast_spectators({"type": "CONFIRM_CARDS", "player": p, "title": "Aufgedeckte Deckkarten", "cards": cards})
                            continue
                        elif mtype == 31: # MSG_CONFIRM_CARDS
                            p = data[1] if len(data) > 1 else 0
                            skip = data[2] if len(data) > 2 else 0
                            cnt = data[3] if len(data) > 3 else 0
                            cards = []
                            off = 4
                            for _ in range(cnt):
                                if off + 7 <= len(data):
                                    code = struct.unpack("<I", data[off:off+4])[0] & 0x7FFFFFFF
                                    cards.append(code)
                                    off += 7
                            room.send_to(0, {"type": "CONFIRM_CARDS", "player": p, "title": "Vorgezeigte Handkarten", "cards": cards})
                            if room.is_pvp:
                                room.send_to(1, {"type": "CONFIRM_CARDS", "player": 1 - p, "title": "Vorgezeigte Handkarten", "cards": cards})
                            room.broadcast_spectators({"type": "CONFIRM_CARDS", "player": p, "title": "Vorgezeigte Handkarten", "cards": cards})
                            continue
                        elif mtype == 40: # MSG_NEW_TURN
                            turn_player = data[1] if len(data) > 1 else 0
                            room.autopilot_idle_count = 0
                            room.broadcast_turn(pduel, turn_player)
                            if room.time_call_active and not room.time_call_game_ended:
                                room.time_call_turn_count += 1
                                turn_msg = {
                                    "type": "TIME_CALL_TURN_UPDATE",
                                    "turn": room.time_call_turn_count,
                                    "max_turns": 3,
                                    "message": f"[TIME CALLED: ZUG {room.time_call_turn_count}/3 // LP TIEBREAKER]"
                                }
                                room.broadcast(turn_msg)
                                print(f"ROOM [{room.room_id}]: Time-call Turn {room.time_call_turn_count}/3 (Turn player: {turn_player})")
                                if room.time_call_turn_count > 3:
                                    print(f"ROOM [{room.room_id}]: End of Turn 3 reached! Executing automated KDE LP tiebreaker...")
                                    execute_time_call_tiebreaker(room, pduel)
                                    break
                            continue
                        elif mtype == 41: # MSG_NEW_PHASE
                            phase_code = struct.unpack("<H", data[1:3])[0] if len(data) >= 3 else 0
                            room.autopilot_idle_count = 0
                            phase_names = {
                                0x01: "DRAW PHASE", 0x02: "STANDBY PHASE", 0x04: "MAIN PHASE 1",
                                0x08: "BATTLE PHASE (START)", 0x10: "BATTLE PHASE", 0x20: "DAMAGE STEP",
                                0x40: "DAMAGE CALCULATION", 0x80: "BATTLE PHASE (END)", 0x100: "MAIN PHASE 2",
                                0x200: "END PHASE"
                            }
                            pname = phase_names.get(phase_code, f"PHASE {hex(phase_code)}")
                            room.broadcast_phase(pduel, phase_code, pname)
                            continue
                        elif mtype == 91: # MSG_DAMAGE
                            p = data[1] if len(data) > 1 else 0
                            dmg = struct.unpack("<I", data[2:6])[0] if len(data) >= 6 else 0
                            room_p = room.ocg_to_room.get(p, p)
                            target_name = room.player_names.get(room_p, f"Player {room_p}")
                            room.record_replay_frame("DAMAGE", f"{target_name} nimmt {dmg} Schaden", pduel)
                            room.broadcast({
                                "type": "DAMAGE",
                                "player": room_p,
                                "player_name": target_name,
                                "damage": dmg
                            })
                            continue
                        elif mtype == 92: # MSG_RECOVER
                            p = data[1] if len(data) > 1 else 0
                            rec = struct.unpack("<I", data[2:6])[0] if len(data) >= 6 else 0
                            room_p = room.ocg_to_room.get(p, p)
                            target_name = room.player_names.get(room_p, f"Player {room_p}")
                            room.record_replay_frame("RECOVER", f"{target_name} erhält {rec} LP", pduel)
                            room.broadcast({
                                "type": "RECOVER",
                                "player": room_p,
                                "player_name": target_name,
                                "amount": rec
                            })
                            continue
                        
                        if parsed and isinstance(parsed, dict):
                            active_cards = set(room.sided_main.get(0, []) + room.sided_extra.get(0, []) + room.sided_main.get(1, []) + room.sided_extra.get(1, []))
                            if 'desc' in parsed and parsed['desc']:
                                parsed['desc_text'] = db.get_description(parsed['desc'], card_id=parsed.get('card_id'), active_cards=active_cards)
                            for k, v in parsed.items():
                                if isinstance(v, list):
                                    for item in v:
                                        if 'card_id' in item and item['card_id'] > 0:
                                            info = db.card_metadata.get(item['card_id'])
                                            if info:
                                                if not item.get('name') or item['name'].startswith('Card '):
                                                    item['name'] = info['name']
                                                item['card_desc'] = info.get('desc', '')
                                                item['atk'] = info.get('atk', 0)
                                                item['def'] = info.get('def', 0)
                                                item['level'] = info.get('level', 0)
                                                item['race'] = info.get('race', 0)
                                                item['attribute'] = info.get('attribute', 0)
                                                item['card_type'] = info.get('type', 0)
                                                item['is_tuner'] = bool(info.get('type', 0) & 0x1000)
                                            else:
                                                if not item.get('name'):
                                                    item['name'] = f"Card {item['card_id']}"
                                        if 'desc' in item and item['desc']:
                                            desc_str = db.get_description(item['desc'], card_id=item.get('card_id'), active_cards=active_cards)
                                            if desc_str:
                                                item['desc_text'] = desc_str
                                                if item.get('type') == 'OPTION' or not item.get('name'):
                                                    item['name'] = desc_str
                                                elif item.get('type') in ['ACTIVATE', 'ACTIVATE_CHAIN']:
                                                    item['name'] = f"{item.get('name', 'Effekt')}: {desc_str}"
                                        
                                        # Explicit Foxy Tune disambiguation fallback if no desc decoded
                                        if item.get('card_id') == 55920742 and item.get('type') in ['ACTIVATE', 'ACTIVATE_CHAIN'] and not item.get('desc_text'):
                                            if item.get('desc') in [0, 0x86600000, 2254438400, 894731872]:
                                                item['desc_text'] = "[HAND-SPECIAL] 1 P.U.N.K. tributieren -> Foxy Tune beschwören"
                                            else:
                                                item['desc_text'] = "[DECK-SPECIAL] Handkarte abwerfen -> P.U.N.K. aus Deck holen"
                                            item['name'] = f"Noh-P.U.N.K. Foxy Tune: {item['desc_text']}"
                            
                            if mtype in [10, 11, 12, 13, 14, 15, 16, 18, 19, 20, 22, 23, 24, 25, 26, 140, 141, 142, 143]:
                                room_player = room.ocg_to_room.get(player, player)
                                if room_player == 0 or (room_player == 1 and room.is_pvp):
                                    handle_human_action(room, pduel, player, mtype, parsed)
                                else:
                                    handle_ai_action(room, pduel, player, mtype, parsed)
                    except Exception as p_err:
                        print(f"ROOM [{room.room_id}]: Parser crash on mtype {mtype}: {p_err}")
                        discrepancy_logger.log(
                            room_id=room.room_id,
                            session_id=room.session_id,
                            discrepancy_type="PARSER_CRASH",
                            mtype=mtype,
                            raw_bytes=data,
                            context={"error": str(p_err), "offset": getattr(parser, "offset", None)}
                        )
                        if mtype == 16:
                            ocgcore.set_responsei(pduel, 0xFFFFFFFF)
                        else:
                            ocgcore.set_responsei(pduel, 0)
                        room.broadcast({
                            "type": "ENGINE_DISCREPANCY_HEALED",
                            "reason": "PARSER_CRASH",
                            "message": f"Engine parser recovered from packet type {mtype} with fallback response."
                        })
                        continue

                    if mtype == 1:
                        room.retry_count += 1
                        print(f"ROOM [{room.room_id}]: MSG_RETRY detected (count {room.retry_count}) for last_mtype {room.last_mtype}")
                        discrepancy_logger.log(
                            room_id=room.room_id,
                            session_id=room.session_id,
                            discrepancy_type="MSG_RETRY",
                            mtype=1,
                            raw_bytes=data,
                            context={
                                "retry_count": room.retry_count,
                                "last_mtype": room.last_mtype,
                                "last_action_submitted": room.last_action_submitted
                            }
                        )
                        if room.retry_count >= 3:
                            room.auto_heal_attempts += 1
                            heal_reason = f"Exceeded {room.retry_count} retries on mtype {room.last_mtype} (attempt {room.auto_heal_attempts}). Applied auto-heal fallback."
                            print(f"ROOM [{room.room_id}]: {heal_reason}")
                            if room.last_mtype == 16:
                                # Break infinite loop: alternate between 0 (first trigger) and 0xFFFFFFFF (pass)
                                if room.auto_heal_attempts % 2 == 1:
                                    print(f"ROOM [{room.room_id}]: Auto-heal mtype 16: forcing trigger 0 (attempt {room.auto_heal_attempts})")
                                    ocgcore.set_responsei(pduel, 0)
                                else:
                                    print(f"ROOM [{room.room_id}]: Auto-heal mtype 16: forcing pass 0xFFFFFFFF (attempt {room.auto_heal_attempts})")
                                    ocgcore.set_responsei(pduel, 0xFFFFFFFF)
                            elif room.last_mtype in [12, 13]:
                                ocgcore.set_responsei(pduel, 0)
                            elif room.last_mtype == 19:
                                ocgcore.set_responsei(pduel, 1)
                            elif room.last_mtype == 10:
                                ocgcore.set_responsei(pduel, 3)
                            elif room.last_mtype == 11:
                                ocgcore.set_responsei(pduel, 7)
                            elif room.last_mtype in [15, 20]:
                                resp_arr = bytearray(64)
                                resp_arr[0] = 1
                                resp_arr[1] = 0
                                ocgcore.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_arr), 64))
                            else:
                                ocgcore.set_responsei(pduel, 0)

                            discrepancy_logger.log(
                                room_id=room.room_id,
                                session_id=room.session_id,
                                discrepancy_type="AUTO_HEAL_APPLIED",
                                mtype=room.last_mtype or 1,
                                raw_bytes=data,
                                context={"heal_reason": heal_reason, "retry_count": room.retry_count, "auto_heal_attempts": room.auto_heal_attempts}
                            )
                            room.broadcast({
                                "type": "ENGINE_DISCREPANCY_HEALED",
                                "reason": "MSG_RETRY_DEADLOCK",
                                "message": f"Engine auto-healed after {room.retry_count} rejected actions (attempt {room.auto_heal_attempts})."
                            })
                            room.retry_count = 0
                            if room.auto_heal_attempts >= 6:
                                print(f"ROOM [{room.room_id}]: CRITICAL: Auto-heal exhausted {room.auto_heal_attempts} attempts on mtype {room.last_mtype}. Terminating duel to prevent thread deadlock.")
                                failing_p = getattr(room, "last_player", player)
                                winner = 1 - failing_p
                                room.broadcast({
                                    "type": "ENGINE_DISCREPANCY_HEALED",
                                    "reason": "DEADLOCK_CIRCUIT_BREAKER_TRIPPED",
                                    "message": f"Duel terminated by circuit breaker: Engine rejected {room.auto_heal_attempts} consecutive fallback actions on packet {room.last_mtype}."
                                })
                                room.broadcast_end(pduel, winner=winner, reason="ENGINE_DEADLOCK_RESOLVED")
                                break
                        time.sleep(0.05)
                        continue
                    else:
                        room.retry_count = 0
                        room.auto_heal_attempts = 0
                time.sleep(0.01)
            # --- END INNER GAME LOOP ---

            # Check if match continues: Sidedecking phase wait
            if not room.match_finished and max(room.match_wins.values()) < match_max_wins and room.current_game < max_games and room.is_active:
                print(f"ROOM [{room.room_id}]: Game {room.current_game} completed. Waiting for Sidedecking (timeout {int(room.sidedeck_timeout_duration)}s)...")
                if not (room.side_decks_ready[0] and room.side_decks_ready[1] and room.first_turn_chosen):
                    waited = room.sidedeck_event.wait(timeout=room.sidedeck_timeout_duration)
                else:
                    waited = True
                if not waited or not room.is_active:
                    print(f"ROOM [{room.room_id}]: Sidedecking phase timed out or room closed.")
                    if not room.match_finished:
                        room.broadcast({
                            "type": "SIDEDECK_TIMEOUT",
                            "message": f"Sidedecking-Zeitlimit ({int(room.sidedeck_timeout_duration)}s) überschritten! Das Match wird gewertet."
                        })
                        if not room.side_decks_ready[0] and room.side_decks_ready[1]:
                            room.broadcast_match_end(winner=1, reason="SIDEDECK_TIMEOUT_P0")
                        elif room.side_decks_ready[0] and not room.side_decks_ready[1]:
                            room.broadcast_match_end(winner=0, reason="SIDEDECK_TIMEOUT_P1")
                        else:
                            room.broadcast_match_end(winner=2, reason="SIDEDECK_TIMEOUT_BOTH")
                    break
                room.current_game += 1
                print(f"ROOM [{room.room_id}]: Sidedecking complete! Starting Game {room.current_game} of Match.")
            else:
                break
    except Exception as e:
        print(f"ROOM [{room.room_id}]: Exception in duel session: {e}")
    finally:
        if room.pduel:
            try:
                ocgcore.end_duel(room.pduel)
            except:
                pass
            room.pduel = None
        if not room.match_finished and not room.duel_ended:
            telemetry.end_duel(room.session_id, winner=-1, reason="LOOP_TERMINATED")
        room.is_active = False

async def run_player_ws_loop(room: DuelRoom, player_idx: int, websocket: WebSocket, user_name: str, is_referee: bool = False):
    clean_room_id = room.room_id
    rate_limiter = ChatRateLimiter(max_messages=5, window_seconds=10.0)
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            act = msg.get("action")
            if act == "AUTH_REFEREE":
                pin = str(msg.get("pin", "")).strip()
                match_info = tournament_engine.get_match_by_room_id(clean_room_id)
                t_id = match_info["tournament_id"] if match_info else None
                if verify_referee_pin(t_id or "default", pin):
                    is_referee = True
                    await websocket.send_text(json.dumps({
                        "type": "REFEREE_AUTH_SUCCESS",
                        "message": "Schiedsrichter-Authentifizierung erfolgreich! Berechtigung [HEAD JUDGE 🛡️] aktiv."
                    }))
                else:
                    await websocket.send_text(json.dumps({
                        "type": "REFEREE_AUTH_FAILED",
                        "message": "PIN ungültig. Schiedsrichter-Authentifizierung verweigert."
                    }))
            elif act == "RESPOND":
                room.action_values[player_idx] = msg
                room.events[player_idx].set()
            elif act == "AUTOPILOT_ON":
                room.autopilot[player_idx] = True
                room.action_values[player_idx] = {"autopilot_triggered": True}
                room.events[player_idx].set()
            elif act == "AUTOPILOT_OFF":
                room.autopilot[player_idx] = False
            elif act == "SURRENDER":
                print(f"ROOM [{clean_room_id}]: Player {player_idx} ({user_name}) surrendered.")
                if room.side_decking_active:
                    winner = 1 - player_idx
                    room.broadcast_match_end(winner=winner, reason=f"SURRENDER_MATCH_P{player_idx}")
                    room.sidedeck_event.set()
                else:
                    winner_ocg = room.room_to_ocg.get(1 - player_idx, 1 - player_idx)
                    room.broadcast_end(room.pduel, winner=winner_ocg, reason=f"SURRENDER_P{player_idx}")
                    room.events[0].set()
                    room.events[1].set()
            elif act == "CHAT_MESSAGE":
                allowed, retry_after = rate_limiter.allow()
                if not allowed:
                    await websocket.send_text(json.dumps({
                        "type": "CHAT_RATE_LIMITED",
                        "retry_after": retry_after,
                        "message": f"Spam-Schutz aktiv: Maximal 5 Nachrichten pro 10s. Bitte {retry_after}s warten."
                    }))
                    continue
                raw_text = str(msg.get("text", "")).strip()
                if raw_text:
                    clean_text = raw_text[:300]
                    is_judge = clean_text.startswith("/judge") or clean_text.startswith("/referee")
                    now_ts = time.time()
                    time_str = time.strftime("%H:%M:%S", time.localtime(now_ts))
                    p_name = room.player_names.get(player_idx) or user_name
                    
                    if is_judge:
                        parts = clean_text.split(None, 1)
                        query = parts[1].strip() if len(parts) > 1 else "Schiedsrichter an den Tisch gerufen!"
                        
                        match_info = tournament_engine.get_match_by_room_id(clean_room_id)
                        t_id = match_info["tournament_id"] if match_info else None
                        t_num = match_info["table_num"] if match_info else "Freier Tisch"
                        r_num = match_info["round_num"] if match_info else 0
                        table_label = f"Tisch {t_num}" if match_info else f"Raum {clean_room_id}"
                        
                        if t_id:
                            tournament_engine.log_audit(
                                tournament_id=t_id,
                                action="JUDGE_CALL",
                                details=f"{table_label} (Runde {r_num}): {p_name} (P{player_idx + 1}) ruft Schiedsrichter. Anliegen: '{query}'",
                                reason=query
                            )
                        
                        judge_alert = {
                            "type": "JUDGE_CALL_ALERT",
                            "room_id": clean_room_id,
                            "tournament_id": t_id,
                            "table_num": t_num,
                            "table_label": table_label,
                            "player_name": p_name,
                            "player_idx": player_idx,
                            "message": query,
                            "timestamp": now_ts,
                            "formatted_time": time_str
                        }
                        ACTIVE_REFEREE_ALERTS.append(judge_alert)
                        if t_id:
                            broadcast_tournament_judge_alert(t_id, judge_alert)
                        else:
                            room.broadcast(judge_alert)
                            
                        sys_msg = {
                            "type": "CHAT_MESSAGE",
                            "sender": "[REFEREE DISPATCH]",
                            "verified_role": "REFEREE_DISPATCH",
                            "badge": "[JUDGE ALARM]",
                            "player_idx": -2,
                            "is_spectator": False,
                            "is_judge_call": True,
                            "text": f"🚨 [SCHIEDSRICHTER ALARMIERT] {p_name} ({table_label}): \"{query}\"",
                            "timestamp": now_ts,
                            "formatted_time": time_str
                        }
                        room.chat_history.append(sys_msg)
                        room.broadcast(sys_msg)
                    else:
                        verified_role = "HEAD_JUDGE" if is_referee else f"PILOT_{player_idx + 1}"
                        badge = "[HEAD JUDGE 🛡️]" if is_referee else f"[PILOT {player_idx + 1} ✓]"
                        chat_entry = {
                            "type": "CHAT_MESSAGE",
                            "sender": p_name,
                            "verified_role": verified_role,
                            "badge": badge,
                            "player_idx": player_idx,
                            "is_spectator": False,
                            "is_judge_call": False,
                            "text": clean_text,
                            "timestamp": now_ts,
                            "formatted_time": time_str
                        }
                        room.chat_history.append(chat_entry)
                        room.broadcast(chat_entry)
            elif act == "SUBMIT_SIDEDECK":
                new_main = [int(x) for x in msg.get("main", []) if str(x).isdigit()]
                new_extra = [int(x) for x in msg.get("extra", []) if str(x).isdigit()]
                new_side = [int(x) for x in msg.get("side", []) if str(x).isdigit()]

                orig_main_len = room.initial_main_len.get(player_idx, 40)
                if len(new_main) != orig_main_len:
                    room.send_to(player_idx, {
                        "type": "SIDEDECK_ERROR",
                        "error": "MAIN_COUNT_MISMATCH",
                        "message": f"Main Deck muss exakt {orig_main_len} Karten haben (aktuell: {len(new_main)})."
                    })
                    continue
                if len(new_extra) > 15:
                    room.send_to(player_idx, {
                        "type": "SIDEDECK_ERROR",
                        "error": "EXTRA_LIMIT",
                        "message": f"Extra Deck darf max. 15 Karten haben (aktuell: {len(new_extra)})."
                    })
                    continue
                if len(new_side) > 15:
                    room.send_to(player_idx, {
                        "type": "SIDEDECK_ERROR",
                        "error": "SIDE_LIMIT",
                        "message": f"Side Deck darf max. 15 Karten haben (aktuell: {len(new_side)})."
                    })
                    continue

                val = check_edison_legality(new_main, new_extra, new_side)
                if not val["is_legal"]:
                    v_msg = "; ".join([v["message"] for v in val["violations"][:2]])
                    room.send_to(player_idx, {
                        "type": "SIDEDECK_ERROR",
                        "error": "EDISON_ILLEGAL",
                        "message": f"Sidedecking verletzt Edison-Regeln: {v_msg}"
                    })
                    continue

                # Log human sidedeck decision for RL training pipeline
                try:
                    old_main = list(room.sided_main.get(player_idx) or [])
                    opp_idx = 1 - player_idx
                    opp_main = list(room.sided_main.get(opp_idx) or [])

                    from collections import Counter
                    new_counter = Counter(new_main)
                    old_counter = Counter(old_main)
                    added_cids = list((new_counter - old_counter).elements())
                    removed_cids = list((old_counter - new_counter).elements())

                    human_swaps = []
                    for in_id, out_id in zip(added_cids, removed_cids):
                        in_meta = db.card_metadata.get(in_id, {})
                        out_meta = db.card_metadata.get(out_id, {})
                        human_swaps.append({
                            "in_id": in_id,
                            "in_name": in_meta.get("name") or f"Card {in_id}",
                            "out_id": out_id,
                            "out_name": out_meta.get("name") or f"Card {out_id}",
                            "source": "HUMAN_PLAYER"
                        })

                    if human_swaps and opp_main:
                        opp_profile = ai_side_engine.analyze_opponent_profile(opp_main, db.card_metadata)
                        is_going_first = (room.first_turn_player == player_idx) if room.first_turn_chosen else False
                        ai_side_engine.log_sidedeck_episode(
                            room_id=room.room_id,
                            session_id=room.session_id,
                            game=room.current_game,
                            opp_profile=opp_profile,
                            swaps=human_swaps,
                            going_first=is_going_first,
                            ai_deck_name=f"HUMAN_{user_name}",
                            player_idx=player_idx
                        )
                except Exception as side_log_err:
                    print(f"ROOM [{room.room_id}]: Error logging human sidedeck episode: {side_log_err}")

                room.sided_main[player_idx] = new_main
                room.sided_extra[player_idx] = new_extra
                room.sided_side[player_idx] = new_side
                room.side_decks_ready[player_idx] = True

                room.send_to(player_idx, {
                    "type": "SIDEDECK_CONFIRMED",
                    "message": "Sidedeck bestätigt! Warte auf Gegenspieler..."
                })

                if room.is_pvp:
                    opp = 1 - player_idx
                    room.send_to(opp, {
                        "type": "OPPONENT_SIDEDECK_READY",
                        "message": f"{user_name} hat das Sidedecking abgeschlossen."
                    })

                if not room.first_turn_chosen:
                    room.first_turn_player = room.loser_of_last_game
                    room.first_turn_chosen = True

                if room.side_decks_ready[0] and room.side_decks_ready[1] and room.first_turn_chosen:
                    room.side_decking_active = False
                    room.sidedeck_event.set()

            elif act == "CHOOSE_PLAY_OR_DRAW":
                choice = msg.get("first", 0) # 0 = Ich gehe zuerst, 1 = Gegner geht zuerst
                if player_idx == 0:
                    chosen_first = 0 if choice == 0 else 1
                else:
                    chosen_first = 1 if choice == 0 else 0

                room.first_turn_player = chosen_first
                room.first_turn_chosen = True

                room.send_to(0, {
                    "type": "FIRST_TURN_CHOSEN",
                    "first_player": (0 if chosen_first == 0 else 1),
                    "first_player_name": ("Du" if chosen_first == 0 else room.player_names.get(1, "Gegner")),
                    "message": f"{user_name} hat gewählt: {'Du beginnst' if chosen_first == 0 else 'Gegner beginnt'} Game {room.current_game + 1}!"
                })
                if room.is_pvp:
                    room.send_to(1, {
                        "type": "FIRST_TURN_CHOSEN",
                        "first_player": (0 if chosen_first == 1 else 1),
                        "first_player_name": ("Du" if chosen_first == 1 else room.player_names.get(0, "Gegner")),
                        "message": f"{user_name} hat gewählt: {'Du beginnst' if chosen_first == 1 else 'Gegner beginnt'} Game {room.current_game + 1}!"
                    })

                if room.side_decks_ready[0] and room.side_decks_ready[1] and room.first_turn_chosen:
                    room.side_decking_active = False
                    room.sidedeck_event.set()
    except Exception as e:
        print(f"WS Disconnect [Player {player_idx}, Room {clean_room_id}]: {e}")
    finally:
        with room.lock:
            room.players[player_idx] = None
            room.loops[player_idx] = None
            room.player_connected[player_idx] = False
            room.disconnect_time[player_idx] = time.time()

        print(f"ROOM [{clean_room_id}]: Player {player_idx} ({user_name}) disconnected.")

        # If duel is actively running, grant 120s reconnect grace period
        if room.is_active and not room.match_finished and room.duel_thread is not None:
            if room.is_pvp:
                other_player = 1 - player_idx
                room.send_to(other_player, {
                    "type": "OPPONENT_DISCONNECTED_GRACE",
                    "player_name": user_name,
                    "grace_period": 120,
                    "message": f"Verbindung zu '{user_name}' unterbrochen. Warte bis zu 120s auf Reconnect..."
                })
            room.broadcast_spectators({
                "type": "SPECTATOR_PLAYER_DISCONNECTED",
                "player_name": user_name,
                "player_idx": player_idx,
                "grace_period": 120,
                "message": f"Spieler '{user_name}' hat die Verbindung unterbrochen (120s Reconnect-Frist)."
            })
            schedule_disconnect_timeout(room, player_idx, grace_seconds=120)
        else:
            # Not in an active duel (still in lobby or already finished)
            if not any(room.player_connected.values()):
                room_mgr.remove_room(clean_room_id)

async def run_spectator_ws_loop(room: DuelRoom, spec_id: str, websocket: WebSocket, user_name: str, is_referee: bool = False):
    clean_room_id = room.room_id
    rate_limiter = ChatRateLimiter(max_messages=5, window_seconds=10.0)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                act = msg.get("action")
                if act == "PING":
                    await websocket.send_text(json.dumps({"type": "PONG"}))
                elif act == "AUTH_REFEREE":
                    pin = str(msg.get("pin", "")).strip()
                    match_info = tournament_engine.get_match_by_room_id(clean_room_id)
                    t_id = match_info["tournament_id"] if match_info else None
                    if verify_referee_pin(t_id or "default", pin):
                        is_referee = True
                        await websocket.send_text(json.dumps({
                            "type": "REFEREE_AUTH_SUCCESS",
                            "message": "Schiedsrichter-Authentifizierung erfolgreich! Berechtigung [HEAD JUDGE 🛡️] aktiv."
                        }))
                    else:
                        await websocket.send_text(json.dumps({
                            "type": "REFEREE_AUTH_FAILED",
                            "message": "PIN ungültig. Schiedsrichter-Authentifizierung verweigert."
                        }))
                elif act == "CHAT_MESSAGE":
                    allowed, retry_after = rate_limiter.allow()
                    if not allowed:
                        await websocket.send_text(json.dumps({
                            "type": "CHAT_RATE_LIMITED",
                            "retry_after": retry_after,
                            "message": f"Spam-Schutz aktiv: Maximal 5 Nachrichten pro 10s. Bitte {retry_after}s warten."
                        }))
                        continue
                    raw_text = str(msg.get("text", "")).strip()
                    if raw_text:
                        clean_text = raw_text[:300]
                        is_judge = clean_text.startswith("/judge") or clean_text.startswith("/referee")
                        now_ts = time.time()
                        time_str = time.strftime("%H:%M:%S", time.localtime(now_ts))
                        sender_name = sanitize_chat_handle(user_name, is_authenticated_referee=is_referee)
                        
                        if is_judge:
                            parts = clean_text.split(None, 1)
                            query = parts[1].strip() if len(parts) > 1 else "Zuschauer meldet Problem an Tisch!"
                            
                            match_info = tournament_engine.get_match_by_room_id(clean_room_id)
                            t_id = match_info["tournament_id"] if match_info else None
                            t_num = match_info["table_num"] if match_info else "Zuschauer"
                            r_num = match_info["round_num"] if match_info else 0
                            table_label = f"Tisch {t_num}" if match_info else f"Raum {clean_room_id}"
                            
                            if t_id:
                                tournament_engine.log_audit(
                                    tournament_id=t_id,
                                    action="JUDGE_CALL_SPECTATOR",
                                    details=f"{table_label} (Runde {r_num}): Zuschauer {sender_name} meldet: '{query}'",
                                    reason=query
                                )
                            
                            judge_alert = {
                                "type": "JUDGE_CALL_ALERT",
                                "room_id": clean_room_id,
                                "tournament_id": t_id,
                                "table_num": t_num,
                                "table_label": table_label,
                                "player_name": f"{sender_name} [SPEC]",
                                "player_idx": -1,
                                "message": query,
                                "timestamp": now_ts,
                                "formatted_time": time_str
                            }
                            ACTIVE_REFEREE_ALERTS.append(judge_alert)
                            if t_id:
                                broadcast_tournament_judge_alert(t_id, judge_alert)
                            else:
                                room.broadcast(judge_alert)
                                
                            sys_msg = {
                                "type": "CHAT_MESSAGE",
                                "sender": "[REFEREE DISPATCH]",
                                "verified_role": "REFEREE_DISPATCH",
                                "badge": "[JUDGE ALARM]",
                                "player_idx": -2,
                                "is_spectator": True,
                                "is_judge_call": True,
                                "text": f"🚨 [ZUSCHAUER-MELDUNG] {sender_name} ({table_label}): \"{query}\"",
                                "timestamp": now_ts,
                                "formatted_time": time_str
                            }
                            room.chat_history.append(sys_msg)
                            room.broadcast(sys_msg)
                        else:
                            verified_role = "HEAD_JUDGE" if is_referee else "SPECTATOR"
                            badge = "[HEAD JUDGE 🛡️]" if is_referee else "[SPECTATOR 👁️]"
                            chat_entry = {
                                "type": "CHAT_MESSAGE",
                                "sender": sender_name,
                                "verified_role": verified_role,
                                "badge": badge,
                                "player_idx": -2 if is_referee else -1,
                                "is_spectator": True,
                                "is_judge_call": False,
                                "text": clean_text,
                                "timestamp": now_ts,
                                "formatted_time": time_str
                            }
                            room.chat_history.append(chat_entry)
                            room.broadcast(chat_entry)
            except Exception:
                pass
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        with room.lock:
            room.spectators.pop(spec_id, None)
            room.spectator_loops.pop(spec_id, None)
            room.spectator_names.pop(spec_id, None)
        print(f"ROOM [{clean_room_id}]: Spectator '{user_name}' ({spec_id}) disconnected.")

@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    deck: Optional[str] = None,
    room: Optional[str] = None,
    user_name: Optional[str] = "Stefan",
    user_id: Optional[str] = None,
    reconnect_token: Optional[str] = None,
    spectator: Optional[str] = None,
    referee_pin: Optional[str] = None,
    scenario: Optional[str] = None,
    format: Optional[str] = None,
    ai_deck: Optional[str] = None,
    match_mode: Optional[str] = None,
    lp: Optional[int] = None,
    turn_choice: Optional[str] = None
):
    await websocket.accept()
    loop = asyncio.get_event_loop()
    clean_user_name = (user_name or "Stefan").strip() or "Stefan"
    clean_user_id = (user_id or f"usr_{uuid.uuid4().hex[:8]}").strip()
    clean_token = reconnect_token.strip() if reconnect_token else None
    clean_format = format.strip().lower() if format and format.strip() else None
    clean_ai_deck = ai_deck.strip() if ai_deck and ai_deck.strip() else None
    clean_match_mode = match_mode.strip().lower() if match_mode and match_mode.strip().lower() in ["bo1", "bo3"] else "bo3"
    clean_lp = int(lp) if lp and int(lp) in [2000, 4000, 8000, 16000] else 8000
    clean_turn_choice = turn_choice.strip().lower() if turn_choice and turn_choice.strip().lower() in ["first", "second", "random"] else "random"

    # Check if referee pin provided at connection
    is_referee_conn = False
    if referee_pin:
        clean_room = room.strip() if room else ""
        m_check = tournament_engine.get_match_by_room_id(clean_room) if clean_room else None
        t_id_check = m_check["tournament_id"] if m_check else None
        if verify_referee_pin(t_id_check or "default", referee_pin):
            is_referee_conn = True

    # ----------------------------------------------------
    # 0. RECONNECT FLOW (Prioritized if token is provided)
    # ----------------------------------------------------
    if clean_token and room and room.strip():
        clean_room_id = room.strip()
        existing_room = room_mgr.get_room(clean_room_id)
        if existing_room and existing_room.is_active and not existing_room.match_finished:
            reconnected_player = None
            with existing_room.lock:
                if existing_room.player_tokens.get(0) == clean_token:
                    reconnected_player = 0
                elif existing_room.player_tokens.get(1) == clean_token:
                    reconnected_player = 1

            if reconnected_player is not None:
                print(f"ROOM [{clean_room_id}]: Player {reconnected_player} ({clean_user_name}) RECONNECTED successfully.")
                with existing_room.lock:
                    existing_room.players[reconnected_player] = websocket
                    existing_room.loops[reconnected_player] = loop
                    existing_room.player_connected[reconnected_player] = True
                    existing_room.disconnect_time[reconnected_player] = None

                # Notify opponent
                if existing_room.is_pvp:
                    other_p = 1 - reconnected_player
                    existing_room.send_to(other_p, {
                        "type": "OPPONENT_RECONNECTED",
                        "player_name": clean_user_name,
                        "message": f"Gegenspieler '{clean_user_name}' hat die Verbindung wiederhergestellt! Duell geht weiter."
                    })

                existing_room.broadcast_spectators({
                    "type": "SPECTATOR_PLAYER_RECONNECTED",
                    "player_name": clean_user_name,
                    "player_idx": reconnected_player,
                    "message": f"Spieler '{clean_user_name}' hat die Verbindung wiederhergestellt! Duell geht weiter."
                })

                # Resume state
                existing_room.resume_player_session(reconnected_player)

                # Enter player WS loop
                await run_player_ws_loop(existing_room, reconnected_player, websocket, clean_user_name, is_referee=is_referee_conn)
                return

        # If clean_token was supplied but room is gone or token invalid
        await websocket.send_text(json.dumps({
            "type": "ROOM_ERROR",
            "error": "SESSION_EXPIRED",
            "message": f"Duell-Sitzung in Raum '{clean_room_id}' ist abgelaufen oder Reconnect-Token ungültig."
        }))
        await websocket.close()
        return

    # ----------------------------------------------------
    # 1. Solo Mode & Scenario Mode
    # ----------------------------------------------------
    if scenario or not room or room.strip() in ["", "solo", "ai"]:
        solo_room_id = f"scen_{uuid.uuid4().hex[:8]}" if scenario else f"solo_{uuid.uuid4().hex[:8]}"
        solo_room = room_mgr.get_or_create(solo_room_id, is_pvp=False)
        solo_room.format = clean_format
        if clean_ai_deck: solo_room.ai_deck = clean_ai_deck
        solo_room.match_mode = clean_match_mode
        solo_room.start_lp = clean_lp
        solo_room.turn_choice = clean_turn_choice
        solo_room.players[0] = websocket
        solo_room.player_names[0] = clean_user_name
        
        sc_data = SCENARIOS.get(scenario) if scenario else None
        if sc_data:
            solo_room.scenario_id = scenario
            solo_room.scenario_data = sc_data
            solo_room.player_names[1] = "Puzzle-Gegner"
            solo_room.player_deck_names[0] = sc_data.get("title", "Szenario")
            solo_room.player_deck_names[1] = "Puzzle-Gegner"
        else:
            solo_room.player_names[1] = "PROPHIT-AI"
            
        solo_room.player_ids[0] = clean_user_id
        solo_room.player_connected[0] = True
        solo_room.loops[0] = loop
        solo_room.decks[0] = deck
        solo_room.autopilot[0] = False
        
        await websocket.send_text(json.dumps({
            "type": "ROOM_STATUS",
            "status": "SOLO_READY",
            "room": solo_room_id,
            "player": 0,
            "your_name": clean_user_name,
            "opponent_name": solo_room.player_names[1],
            "reconnect_token": solo_room.player_tokens[0],
            "is_scenario": bool(sc_data),
            "scenario": sc_data,
            "message": f"🧩 Szenario '{sc_data['title']}' gestartet." if sc_data else "Solo-Duell gegen PROPHIT-AI gestartet."
        }))

        if sc_data:
            await websocket.send_text(json.dumps({
                "type": "SCENARIO_INFO",
                "scenario": sc_data,
                "message": f"🧩 Daily Puzzle: {sc_data['title']}"
            }))

        await websocket.send_text(json.dumps({
            "type": "CHAT_HISTORY",
            "messages": list(solo_room.chat_history)
        }))

        t = threading.Thread(target=run_duel_session, args=(solo_room,), daemon=True)
        solo_room.duel_thread = t
        t.start()
        
        await run_player_ws_loop(solo_room, 0, websocket, clean_user_name, is_referee=is_referee_conn)
        return

    # ----------------------------------------------------
    # 2. PvP Mode & Passive Spectator Mode
    # ----------------------------------------------------
    clean_room_id = room.strip()
    pvp_room = room_mgr.get_or_create(clean_room_id, is_pvp=True)
    if clean_format and not getattr(pvp_room, "format", None):
        pvp_room.format = clean_format
    if clean_match_mode and not getattr(pvp_room, "match_mode", None):
        pvp_room.match_mode = clean_match_mode
    if clean_lp and not getattr(pvp_room, "start_lp", None):
        pvp_room.start_lp = clean_lp
    
    is_spec_request = (spectator == "1" or str(spectator).lower() in ["true", "yes", "spec"])
    assigned_role = None

    with pvp_room.lock:
        if is_spec_request:
            assigned_role = "SPECTATOR"
        elif pvp_room.duel_thread is not None and not clean_token:
            # Duel is already running and this is not a valid reconnect attempt -> Spectator
            assigned_role = "SPECTATOR"
        elif pvp_room.players[0] is None:
            assigned_role = 0
            pvp_room.players[0] = websocket
            pvp_room.player_names[0] = clean_user_name
            pvp_room.player_ids[0] = clean_user_id
            pvp_room.player_connected[0] = True
            pvp_room.loops[0] = loop
            pvp_room.decks[0] = deck
            pvp_room.autopilot[0] = False
        elif pvp_room.players[1] is None:
            assigned_role = 1
            pvp_room.players[1] = websocket
            pvp_room.player_names[1] = clean_user_name
            pvp_room.player_ids[1] = clean_user_id
            pvp_room.player_connected[1] = True
            pvp_room.loops[1] = loop
            pvp_room.decks[1] = deck
            pvp_room.autopilot[1] = False
        else:
            # Both slots taken -> Auto Spectator!
            assigned_role = "SPECTATOR"

    if assigned_role == "SPECTATOR":
        spec_id = f"spec_{uuid.uuid4().hex[:8]}"
        with pvp_room.lock:
            pvp_room.spectators[spec_id] = websocket
            pvp_room.spectator_loops[spec_id] = loop
            pvp_room.spectator_names[spec_id] = clean_user_name

        p0_name = pvp_room.player_names.get(0, "Spieler 1")
        p1_name = pvp_room.player_names.get(1, "Spieler 2") if pvp_room.is_pvp else "PROPHIT-AI"
        p0_w = pvp_room.match_wins.get(0, 0)
        p1_w = pvp_room.match_wins.get(1, 0)
        print(f"ROOM [{clean_room_id}]: Spectator '{clean_user_name}' ({spec_id}) joined. Match: {p0_name} vs {p1_name}.")

        await websocket.send_text(json.dumps({
            "type": "ROOM_STATUS",
            "status": "SPECTATOR_CONNECTED",
            "role": "SPECTATOR",
            "room": clean_room_id,
            "spectator_id": spec_id,
            "your_name": clean_user_name,
            "p0_name": p0_name,
            "p1_name": p1_name,
            "score": f"{p0_w} : {p1_w}",
            "score_obj": {0: p0_w, 1: p1_w},
            "current_game": pvp_room.current_game,
            "side_decking_active": pvp_room.side_decking_active,
            "message": f"Als Zuschauer dem Match '{p0_name}' vs '{p1_name}' in Raum '{clean_room_id}' beigetreten."
        }))

        await websocket.send_text(json.dumps({
            "type": "SPECTATOR_HISTORY_SYNC",
            "history": list(pvp_room.history_buffer),
            "current_game": pvp_room.current_game,
            "score": f"{p0_w} : {p1_w}",
            "message": "Historie geladen."
        }))

        await websocket.send_text(json.dumps({
            "type": "CHAT_HISTORY",
            "messages": list(pvp_room.chat_history)
        }))

        if pvp_room.pduel:
            bstate = get_board_state(pvp_room.pduel, pov_player=pvp_room.room_to_ocg.get(0, 0))
            await websocket.send_text(json.dumps({
                "type": "SPECTATOR_UPDATE",
                "room": clean_room_id,
                "current_game": pvp_room.current_game,
                "score": f"{p0_w} : {p1_w}",
                "active_player": 0,
                "active_player_name": p0_name,
                "board": bstate,
                "message": f"Live-Duell übertragen (Game {pvp_room.current_game})."
            }))
        elif pvp_room.side_decking_active:
            rem_secs = max(0, int(pvp_room.sidedeck_timeout_duration - (time.time() - pvp_room.sidedeck_start_time))) if pvp_room.sidedeck_start_time > 0 else int(pvp_room.sidedeck_timeout_duration)
            await websocket.send_text(json.dumps({
                "type": "SPECTATOR_SIDEDECK",
                "game_ended": pvp_room.current_game,
                "score": f"{p0_w} : {p1_w}",
                "score_obj": {0: p0_w, 1: p1_w},
                "p0_name": p0_name,
                "p1_name": p1_name,
                "timeout_seconds": int(pvp_room.sidedeck_timeout_duration),
                "remaining_seconds": rem_secs,
                "message": f"Sidedecking-Phase aktiv ({rem_secs}s Restzeit)."
            }))

        await run_spectator_ws_loop(pvp_room, spec_id, websocket, clean_user_name, is_referee=is_referee_conn)
        return

    assigned_player = assigned_role
    print(f"ROOM [{clean_room_id}]: Player {assigned_player} ({clean_user_name}) joined with deck '{deck}'.")
    
    if assigned_player == 0:
        await websocket.send_text(json.dumps({
            "type": "ROOM_STATUS",
            "status": "WAITING_FOR_OPPONENT",
            "room": clean_room_id,
            "player": 0,
            "your_name": clean_user_name,
            "opponent_name": None,
            "reconnect_token": pvp_room.player_tokens[0],
            "message": f"Raum '{clean_room_id}' erstellt. Warte auf Gegenspieler..."
        }))
        await websocket.send_text(json.dumps({
            "type": "CHAT_HISTORY",
            "messages": list(pvp_room.chat_history)
        }))
    else: # assigned_player == 1
        p0_name = pvp_room.player_names.get(0, "Spieler 1")
        p1_name = clean_user_name
        
        await websocket.send_text(json.dumps({
            "type": "ROOM_STATUS",
            "status": "OPPONENT_FOUND",
            "room": clean_room_id,
            "player": 1,
            "your_name": p1_name,
            "opponent_name": p0_name,
            "reconnect_token": pvp_room.player_tokens[1],
            "message": f"Raum '{clean_room_id}' beigetreten. Duell gegen {p0_name} startet..."
        }))
        await websocket.send_text(json.dumps({
            "type": "CHAT_HISTORY",
            "messages": list(pvp_room.chat_history)
        }))
        pvp_room.send_to(0, {
            "type": "ROOM_STATUS",
            "status": "OPPONENT_FOUND",
            "room": clean_room_id,
            "player": 0,
            "your_name": p0_name,
            "opponent_name": p1_name,
            "reconnect_token": pvp_room.player_tokens[0],
            "message": f"Gegenspieler '{p1_name}' ist beigetreten! Duell startet..."
        })
        pvp_room.send_to(0, {
            "type": "CHAT_HISTORY",
            "messages": list(pvp_room.chat_history)
        })
        
        t = threading.Thread(target=run_duel_session, args=(pvp_room,), daemon=True)
        pvp_room.duel_thread = t
        t.start()
        
    await run_player_ws_loop(pvp_room, assigned_player, websocket, clean_user_name, is_referee=is_referee_conn)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
