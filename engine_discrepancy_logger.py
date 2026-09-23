import os
import json
import time
import threading

class EngineDiscrepancyLogger:
    """
    Blackbox forensic logger for C++ engine discrepancies, MSG_RETRY loops,
    parser exceptions, and auto-healed duel states.
    """
    def __init__(self, log_path: str = None):
        if not log_path:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            log_dir = os.path.join(base_dir, "telemetry")
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, "engine_discrepancies.jsonl")
        self.log_path = log_path
        self.lock = threading.Lock()

    def log(self, room_id: str, session_id: str, discrepancy_type: str, mtype: int, raw_bytes: bytes, context: dict = None):
        entry = {
            "timestamp": time.time(),
            "iso_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "room_id": room_id,
            "session_id": session_id,
            "discrepancy_type": discrepancy_type,
            "mtype": mtype,
            "raw_bytes_hex": raw_bytes.hex() if isinstance(raw_bytes, (bytes, bytearray)) else None,
            "raw_bytes_len": len(raw_bytes) if isinstance(raw_bytes, (bytes, bytearray)) else 0,
            "context": context or {}
        }
        with self.lock:
            try:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry) + "\n")
            except Exception as e:
                print(f"[DISCREPANCY_LOGGER_ERROR]: Failed to write discrepancy log: {e}")
