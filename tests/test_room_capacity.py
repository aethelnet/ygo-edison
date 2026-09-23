import asyncio
import json
import websockets
import sys

SERVER_WS = "wss://play.aethelburg.network/ws"

async def test_room_capacity():
    print("\n==========================================")
    print(">>> TEST: 1-CLICK LOBBY ROOM CAPACITY (3 PLAYERS)")
    print("==========================================")
    
    room_id = "test_capacity_room_82"
    deck = "Edison_Blackwing"
    
    url0 = f"{SERVER_WS}?mode=pvp&room={room_id}&deck={deck}&user_name=Host_Stefan&user_id=usr_host"
    url1 = f"{SERVER_WS}?mode=pvp&room={room_id}&deck={deck}&user_name=Challenger_Kollege&user_id=usr_challenger"
    url2 = f"{SERVER_WS}?mode=pvp&room={room_id}&deck={deck}&user_name=Third_Spectator&user_id=usr_third"
    
    print(f"1. Host connecting to {room_id}...")
    async with websockets.connect(url0) as ws0:
        m0 = await ws0.recv()
        d0 = json.loads(m0)
        assert d0.get("status") == "WAITING_FOR_OPPONENT"
        print(f"✓ Host is waiting in room {room_id} (Player 0)")
        
        print(f"2. Challenger connecting to {room_id}...")
        async with websockets.connect(url1) as ws1:
            m1 = await ws1.recv()
            d1 = json.loads(m1)
            assert d1.get("status") == "OPPONENT_FOUND"
            print(f"✓ Challenger joined room {room_id} (Player 1)")
            
            # Host receives OPPONENT_FOUND
            m0_found = await ws0.recv()
            d0_found = json.loads(m0_found)
            assert d0_found.get("status") == "OPPONENT_FOUND"
            print(f"✓ Host notified that Challenger joined!")
            
            print(f"3. Third player attempting to join full room {room_id}...")
            async with websockets.connect(url2) as ws2:
                m2 = await ws2.recv()
                d2 = json.loads(m2)
                print(f"✓ Third player received: {d2}")
                assert d2.get("type") == "ROOM_ERROR", f"Expected ROOM_ERROR, got {d2}"
                assert d2.get("error") in ["ROOM_FULL", "DUEL_IN_PROGRESS"], f"Unexpected error: {d2.get('error')}"
                print(f"✓ Verified: Room capacity correctly enforced! Error: {d2.get('error')} - {d2.get('message')}")
                
            # Clean up: Host forfeits
            print("4. Cleaning up duel room...")
            await ws0.send(json.dumps({"action": "SURRENDER"}))
            await asyncio.sleep(0.5)

    print("\n✓ ROOM CAPACITY & 3RD PLAYER INTERCEPTION 100% VERIFIED!")

if __name__ == "__main__":
    asyncio.run(test_room_capacity())
