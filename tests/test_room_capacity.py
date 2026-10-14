import asyncio
import contextlib
import json
import os
import socket
import sys
import threading
import time
import unittest
import websockets

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


@contextlib.contextmanager
def _ensure_ws_server():
    env_ws = os.environ.get("YGO_SERVER_WS")
    if env_ws:
        yield env_ws
        return
    import subprocess

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "ygo_web_duel:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "error"],
        cwd=repo_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 15.0
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.1)
    try:
        yield f"ws://127.0.0.1:{port}/ws"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            proc.kill()


async def _recv_until_status(ws, expected_status):
    while True:
        msg = await asyncio.wait_for(ws.recv(), timeout=10.0)
        data = json.loads(msg)
        if data.get("type") == "ROOM_STATUS" and data.get("status") == expected_status:
            return data


async def _run_room_capacity(server_ws: str):
    room_id = "test_capacity_room_82"
    deck = "Edison_Blackwing"

    url0 = f"{server_ws}?mode=pvp&room={room_id}&deck={deck}&user_name=Host_Stefan&user_id=usr_host"
    url1 = f"{server_ws}?mode=pvp&room={room_id}&deck={deck}&user_name=Challenger_Kollege&user_id=usr_challenger"
    url2 = f"{server_ws}?mode=pvp&room={room_id}&deck={deck}&user_name=Third_Spectator&user_id=usr_third"

    async with websockets.connect(url0) as ws0:
        await _recv_until_status(ws0, "WAITING_FOR_OPPONENT")

        async with websockets.connect(url1) as ws1:
            await _recv_until_status(ws1, "OPPONENT_FOUND")
            await _recv_until_status(ws0, "OPPONENT_FOUND")

            async with websockets.connect(url2) as ws2:
                m2 = await asyncio.wait_for(ws2.recv(), timeout=10.0)
                d2 = json.loads(m2)
                assert d2.get("status") == "SPECTATOR_CONNECTED" or d2.get("type") == "ROOM_ERROR", (
                    f"Expected SPECTATOR_CONNECTED or ROOM_ERROR, got {d2}"
                )

            await ws0.send(json.dumps({"action": "SURRENDER"}))
            await asyncio.sleep(0.2)


class TestRoomCapacity(unittest.TestCase):
    def test_room_capacity(self):
        with _ensure_ws_server() as server_ws:
            asyncio.run(_run_room_capacity(server_ws))


if __name__ == "__main__":
    unittest.main()

