import os
import json
import time
import queue
import threading
from datetime import datetime
from loguru import logger

class DuelTelemetryRecorder:
    def __init__(self, output_dir=None):
        if output_dir is None:
            output_dir = os.path.join(os.path.dirname(__file__), "telemetry")
        self.output_dir = os.path.abspath(output_dir)
        os.makedirs(self.output_dir, exist_ok=True)
        self.log_file = os.path.join(self.output_dir, "telemetry_duels.jsonl")
        
        self.queue = queue.Queue(maxsize=20000)
        self._running = True
        self._worker = threading.Thread(target=self._process_queue, daemon=True)
        self._worker.start()
        
        self.active_duels = {} # session_id -> metadata
        logger.info(f"📊 DuelTelemetryRecorder initialized: {self.log_file}")

    def _process_queue(self):
        while self._running:
            try:
                item = self.queue.get(timeout=1.0)
                if item is None:
                    break
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
                    f.flush()
                self.queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error writing telemetry: {e}")

    def start_duel(self, session_id, user_deck, ai_deck, mode="SOLO"):
        meta = {
            "session_id": session_id,
            "mode": mode,
            "user_deck": user_deck,
            "ai_deck": ai_deck,
            "start_time": datetime.utcnow().isoformat(),
            "step_count": 0
        }
        self.active_duels[session_id] = meta
        self.log_event({
            "type": "DUEL_START",
            "session_id": session_id,
            "mode": mode,
            "user_deck": user_deck,
            "ai_deck": ai_deck,
            "timestamp": datetime.utcnow().isoformat()
        })

    def record_step(self, session_id, turn_player, msg_type, board_state, legal_actions, chosen_action):
        meta = self.active_duels.get(session_id, {})
        step_idx = meta.get("step_count", 0) + 1
        meta["step_count"] = step_idx
        
        p0 = board_state.get(0) or board_state.get("0") or {}
        p1 = board_state.get(1) or board_state.get("1") or {}
        
        # Serialize trajectory event
        entry = {
            "type": "TRAJECTORY_STEP",
            "session_id": session_id,
            "mode": meta.get("mode", "SOLO"),
            "step_index": step_idx,
            "user_deck": meta.get("user_deck", "unknown"),
            "timestamp": datetime.utcnow().isoformat(),
            "turn_player": turn_player,
            "msg_type": msg_type,
            "board": {
                "lp_user": p0.get("lp", 8000),
                "lp_ai": p1.get("lp", 8000),
                "hand_user": [c.get("code") for c in p0.get("hand", []) if c],
                "hand_ai_count": len(p1.get("hand", [])),
                "mzone_user": [c.get("code") if c else 0 for c in p0.get("mzone", [])],
                "szone_user": [c.get("code") if c else 0 for c in p0.get("szone", [])],
                "mzone_ai": [c.get("code") if c else 0 for c in p1.get("mzone", [])],
                "szone_ai": [c.get("code") if c else 0 for c in p1.get("szone", [])],
                "grave_user_count": len(p0.get("grave", [])),
                "grave_ai_count": len(p1.get("grave", [])),
            },
            "legal_actions": [
                {
                    "type": a.get("type"),
                    "card_id": a.get("card_id"),
                    "name": a.get("name"),
                    "sub_idx": a.get("sub_idx"),
                    "loc": a.get("loc"),
                    "seq": a.get("seq")
                }
                for a in legal_actions
            ],
            "chosen_action": chosen_action
        }
        self.log_event(entry)

    def end_duel(self, session_id, winner, reason="NORMAL"):
        meta = self.active_duels.pop(session_id, None)
        if meta is None:
            return
        entry = {
            "type": "DUEL_END",
            "session_id": session_id,
            "mode": meta.get("mode", "SOLO"),
            "user_deck": meta.get("user_deck", "unknown"),
            "total_steps": meta.get("step_count", 0),
            "winner": winner, # 0 = user, 1 = ai, 2 = draw
            "reason": reason,
            "timestamp": datetime.utcnow().isoformat()
        }
        self.log_event(entry)

    def log_event(self, entry):
        try:
            self.queue.put_nowait(entry)
        except queue.Full:
            logger.warning("Telemetry queue full! Dropping entry.")
