import asyncio
import json
import websockets
import sys

SERVER_WS = "wss://play.aethelburg.network/ws"

async def test_solo_bo3():
    print("\n==========================================")
    print(">>> TEST 1: SOLO BEST-OF-3 vs AI")
    print("==========================================")
    
    deck = "Edison_Blackwing"
    user_name = "Stefan_Test"
    user_id = "usr_test_bo3_solo"
    
    url = f"{SERVER_WS}?mode=solo&deck={deck}&user_name={user_name}&user_id={user_id}"
    print(f"Connecting to {url}...")
    
    async with websockets.connect(url) as ws:
        # 1. Wait for ROOM_STATUS
        msg = await ws.recv()
        data = json.loads(msg)
        assert data.get("type") == "ROOM_STATUS", f"Expected ROOM_STATUS, got {data}"
        print(f"✓ ROOM_STATUS received: Room {data.get('room')}, Status: {data.get('status')}")

        # 2. Wait for NEW_TURN or ACTION_REQUIRED (Game 1 started)
        game1_started = False
        while not game1_started:
            msg = await ws.recv()
            data = json.loads(msg)
            if data.get("type") in ["NEW_TURN", "ACTION_REQUIRED", "NEW_PHASE"]:
                game1_started = True
                print(f"✓ Game 1 actively running (event: {data.get('type')})")
                break
        
        # 3. Forfeit Game 1 to test transition into Sidedecking
        print(">>> Forfeiting Game 1 to trigger Sidedecking...")
        await ws.send(json.dumps({"action": "SURRENDER"}))
        
        # 4. Wait for GAME_END_SIDEDECK
        sidedeck_data = None
        while True:
            msg = await ws.recv()
            data = json.loads(msg)
            mtype = data.get("type")
            if mtype == "GAME_END_SIDEDECK":
                sidedeck_data = data
                print(f"✓ Received GAME_END_SIDEDECK!")
                print(f"  - Game Ended: {data.get('game_ended')}")
                print(f"  - Score: {data.get('score_str')}")
                print(f"  - Next Game: {data.get('next_game')}")
                print(f"  - Can choose first: {data.get('can_choose_first')}")
                print(f"  - Main cards count: {len(data.get('main', []))}")
                print(f"  - Side cards count: {len(data.get('side', []))}")
                print(f"  - Extra cards count: {len(data.get('extra', []))}")
                break
            elif mtype == "MATCH_END":
                print(f"❌ Unexpected MATCH_END received after Game 1: {data}")
                sys.exit(1)

        assert sidedeck_data["game_ended"] == 1, "Expected game_ended == 1"
        assert sidedeck_data["next_game"] == 2, "Expected next_game == 2"
        assert sidedeck_data["can_choose_first"] == True, "Stefan lost G1, must be able to choose who goes first"
        
        main_cards = [c["id"] for c in sidedeck_data.get("main", [])]
        side_cards = [c["id"] for c in sidedeck_data.get("side", [])]
        extra_cards = [c["id"] for c in sidedeck_data.get("extra", [])]
        
        orig_main_len = len(main_cards)
        print(f"✓ Main deck length verified: {orig_main_len}")
        
        # 5. Swap 1 card between Main and Side (if side has cards)
        if len(side_cards) > 0:
            print(f"Swapping Main card {main_cards[0]} with Side card {side_cards[0]}...")
            main_cards[0], side_cards[0] = side_cards[0], main_cards[0]
        
        # 6. Choose who goes first (0 = Ich beginne)
        print("Sending CHOOSE_PLAY_OR_DRAW (first=0)...")
        await ws.send(json.dumps({
            "action": "CHOOSE_PLAY_OR_DRAW",
            "first": 0
        }))
        
        # 7. Submit Sidedeck
        print("Sending SUBMIT_SIDEDECK...")
        await ws.send(json.dumps({
            "action": "SUBMIT_SIDEDECK",
            "main": main_cards,
            "extra": extra_cards,
            "side": side_cards
        }))
        
        # 8. Wait for SIDEDECK_CONFIRMED and MATCH_GAME_START
        game2_started = False
        while not game2_started:
            msg = await ws.recv()
            data = json.loads(msg)
            mtype = data.get("type")
            if mtype == "SIDEDECK_CONFIRMED":
                print("✓ SIDEDECK_CONFIRMED received from server.")
            elif mtype == "FIRST_TURN_CHOSEN":
                print(f"✓ FIRST_TURN_CHOSEN received: {data.get('message')}")
            elif mtype == "MATCH_GAME_START":
                print(f"✓ MATCH_GAME_START received!")
                print(f"  - Current Game: {data.get('current_game')}")
                print(f"  - Score: {data.get('score_str')}")
                print(f"  - First Player: {data.get('first_player')}")
                assert data.get("current_game") == 2, "Expected current_game == 2"
                game2_started = True
                break
        
        # 9. In Game 2, forfeit again to test MATCH_END (AI reaches 2 wins)
        print(">>> Forfeiting Game 2 to test MATCH_END (AI reaching 2 wins)...")
        await ws.send(json.dumps({"action": "SURRENDER"}))
        
        match_ended = False
        while not match_ended:
            msg = await ws.recv()
            data = json.loads(msg)
            mtype = data.get("type")
            if mtype == "MATCH_END":
                print(f"✓ MATCH_END received!")
                print(f"  - Winner: {data.get('winner')}")
                print(f"  - Winner Name: {data.get('winner_name')}")
                print(f"  - Score: {data.get('score')}")
                print(f"  - Total Games: {data.get('total_games')}")
                print(f"  - Reason: {data.get('reason')}")
                match_ended = True
                break
        
        print("✓ SOLO BEST-OF-3 VERIFICATION 100% SUCCESSFUL!")

async def test_pvp_bo3():
    print("\n==========================================")
    print(">>> TEST 2: PvP BEST-OF-3 (2 HUMANS)")
    print("==========================================")
    
    room_id = "test_bo3_pvp_room"
    p0_name = "Stefan"
    p1_name = "Ladenkollege"
    deck0 = "Edison_Blackwing"
    deck1 = "Edison_Vayu_Turbo"
    
    url0 = f"{SERVER_WS}?mode=pvp&room={room_id}&deck={deck0}&user_name={p0_name}&user_id=usr_stefan"
    url1 = f"{SERVER_WS}?mode=pvp&room={room_id}&deck={deck1}&user_name={p1_name}&user_id=usr_kollege"
    
    async with websockets.connect(url0) as ws0:
        msg0 = await ws0.recv()
        data0 = json.loads(msg0)
        assert data0.get("status") == "WAITING_FOR_OPPONENT"
        print(f"✓ Player 0 waiting in room {room_id}")
        
        async with websockets.connect(url1) as ws1:
            msg1 = await ws1.recv()
            data1 = json.loads(msg1)
            print(f"✓ Player 1 joined: {data1.get('message')}")
            
            # Read opponent found for p0
            msg0_found = await ws0.recv()
            data0_found = json.loads(msg0_found)
            print(f"✓ Player 0 notified: {data0_found.get('status')}")
            
            # Wait for Game 1 to be active
            while True:
                m = await ws0.recv()
                d = json.loads(m)
                if d.get("type") in ["NEW_TURN", "ACTION_REQUIRED", "NEW_PHASE"]:
                    print(f"✓ Game 1 active in PvP (Event: {d.get('type')})")
                    break
            
            # P0 forfeits Game 1
            print(">>> Player 0 forfeits Game 1...")
            await ws0.send(json.dumps({"action": "SURRENDER"}))
            
            # Both players should receive GAME_END_SIDEDECK
            g1_p0 = None
            while True:
                m = await ws0.recv()
                d = json.loads(m)
                if d.get("type") == "GAME_END_SIDEDECK":
                    g1_p0 = d
                    break
            
            g1_p1 = None
            while True:
                m = await ws1.recv()
                d = json.loads(m)
                if d.get("type") == "GAME_END_SIDEDECK":
                    g1_p1 = d
                    break
            
            print(f"✓ P0 Sidedeck: Score {g1_p0['score_str']}, can_choose_first={g1_p0['can_choose_first']}")
            print(f"✓ P1 Sidedeck: Score {g1_p1['score_str']}, can_choose_first={g1_p1['can_choose_first']}")
            
            assert g1_p0['can_choose_first'] == True, "P0 lost, must choose turn order"
            assert g1_p1['can_choose_first'] == False, "P1 won, must not choose turn order"
            
            # P0 chooses who begins Game 2
            await ws0.send(json.dumps({"action": "CHOOSE_PLAY_OR_DRAW", "first": 0}))
            
            # Both submit sidedeck
            await ws0.send(json.dumps({
                "action": "SUBMIT_SIDEDECK",
                "main": [c["id"] for c in g1_p0["main"]],
                "extra": [c["id"] for c in g1_p0["extra"]],
                "side": [c["id"] for c in g1_p0["side"]]
            }))
            
            await ws1.send(json.dumps({
                "action": "SUBMIT_SIDEDECK",
                "main": [c["id"] for c in g1_p1["main"]],
                "extra": [c["id"] for c in g1_p1["extra"]],
                "side": [c["id"] for c in g1_p1["side"]]
            }))
            
            # Both should receive MATCH_GAME_START for Game 2
            g2_p0 = False
            while not g2_p0:
                m = await ws0.recv()
                d = json.loads(m)
                if d.get("type") == "MATCH_GAME_START":
                    assert d.get("current_game") == 2
                    print(f"✓ P0 received Game 2 Start: Score {d.get('score_str')}")
                    g2_p0 = True
                    break
            
            g2_p1 = False
            while not g2_p1:
                m = await ws1.recv()
                d = json.loads(m)
                if d.get("type") == "MATCH_GAME_START":
                    assert d.get("current_game") == 2
                    print(f"✓ P1 received Game 2 Start: Score {d.get('score_str')}")
                    g2_p1 = True
                    break
            
            # P1 surrenders Game 2 -> Score becomes 1:1, triggers Sidedecking for Game 3!
            print(">>> Player 1 forfeits Game 2 -> Expecting Game 3 Sidedecking (Score 1:1)...")
            await ws1.send(json.dumps({"action": "SURRENDER"}))
            
            g3_p0 = None
            while True:
                m = await ws0.recv()
                d = json.loads(m)
                if d.get("type") == "GAME_END_SIDEDECK":
                    g3_p0 = d
                    break
            
            print(f"✓ Game 3 Sidedecking reached! Next Game: {g3_p0.get('next_game')}, Score: {g3_p0.get('score_str')}")
            assert g3_p0.get("next_game") == 3, "Expected next_game == 3"
            
            # In Game 3 sidedeck, P0 surrenders the entire match via MATCH AUFGEBEN
            print(">>> Player 0 forfeits the entire match during Sidedecking...")
            await ws0.send(json.dumps({"action": "SURRENDER"}))
            
            end_p0 = None
            while True:
                m = await ws0.recv()
                d = json.loads(m)
                if d.get("type") == "MATCH_END":
                    end_p0 = d
                    break
            
            print(f"✓ MATCH_END received: Winner {end_p0.get('winner_name')}, Reason: {end_p0.get('reason')}")
            print("✓ PvP BEST-OF-3 VERIFICATION 100% SUCCESSFUL!")

async def main():
    await test_solo_bo3()
    await test_pvp_bo3()

if __name__ == "__main__":
    asyncio.run(main())
