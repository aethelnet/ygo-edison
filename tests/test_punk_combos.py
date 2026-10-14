import unittest
import os
import sys
import ctypes
import struct

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ygo_sqlite_bridge import YGOSqliteBridge
from ygo_byte_parser import YGOByteParser
from ygo_web_duel import solve_select_sum


class CardData(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_uint32), ("alias", ctypes.c_uint32), ("setcode", ctypes.c_uint16 * 16),
        ("type", ctypes.c_uint32), ("level", ctypes.c_uint32), ("attribute", ctypes.c_uint32),
        ("race", ctypes.c_uint32), ("attack", ctypes.c_int32), ("defense", ctypes.c_int32),
        ("lscale", ctypes.c_uint32), ("rscale", ctypes.c_uint32), ("link_marker", ctypes.c_uint32),
        ("rule_code", ctypes.c_uint32)
    ]


class TestPunkCombosAndExtraDeck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = YGOSqliteBridge()
        core_path = os.path.join(os.path.dirname(__file__), "../ygopro-core/bin/Release/libocgcore.so")
        if not os.path.exists(core_path):
            core_path = "/home/ubuntu/ygo_service/ygopro-core/libocgcore.so"
        if not os.path.exists(core_path):
            core_path = "/home/ubuntu/ygo_service/ygopro-core/bin/Release/libocgcore.so"
        cls.ocg = ctypes.cdll.LoadLibrary(core_path)

        cls.ocg.create_duel.restype = ctypes.c_void_p
        cls.ocg.create_duel.argtypes = [ctypes.c_uint32]
        cls.ocg.set_player_info.argtypes = [ctypes.c_void_p, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32]
        cls.ocg.new_card.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint8]
        cls.ocg.start_duel.argtypes = [ctypes.c_void_p, ctypes.c_int32]
        cls.ocg.process.restype = ctypes.c_int32
        cls.ocg.process.argtypes = [ctypes.c_void_p]
        cls.ocg.get_message.restype = ctypes.c_int32
        cls.ocg.get_message.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        cls.ocg.set_responsei.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        cls.ocg.set_responseb.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        cls.ocg.set_script_reader.argtypes = [ctypes.c_void_p]
        cls.ocg.set_card_reader.argtypes = [ctypes.c_void_p]
        cls.ocg.set_message_handler.argtypes = [ctypes.c_void_p]
        cls.ocg.query_field_card.argtypes = [
            ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8,
            ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int32
        ]
        cls.ocg.query_field_card.restype = ctypes.c_int32
        cls.ocg.end_duel.argtypes = [ctypes.c_void_p]
        cls.ocg.end_duel.restype = None

        def card_reader_cb(code, pdata):
            ctypes.memset(pdata, 0, ctypes.sizeof(CardData))
            pdata.contents.code = code
            info = cls.db.card_metadata.get(code)
            if info:
                pdata.contents.alias = info.get("alias", 0)
                pdata.contents.attack = max(0, info.get("atk", 0))
                pdata.contents.defense = max(0, info.get("def", 0))
                pdata.contents.type = info.get("type", 0)
                pdata.contents.level = info.get("level", 0)
                pdata.contents.race = info.get("race", 0)
                pdata.contents.attribute = info.get("attribute", 0)
                pdata.contents.link_marker = info.get("link_marker", 0)
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
            os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../backend_clean/script")),
            os.path.abspath(os.path.join(os.path.dirname(__file__), "../script")),
            "/home/ubuntu/ygo_service/script"
        ]
        cls._buffers = []

        def script_reader_cb(name_bytes, plen):
            if not name_bytes:
                plen[0] = 0
                return 0
            name_str = ctypes.string_at(name_bytes).decode("utf-8", errors="ignore")
            base_name = os.path.basename(name_str)
            for sdir in script_dirs:
                file_path = os.path.join(sdir, base_name)
                if os.path.exists(file_path):
                    with open(file_path, "rb") as f:
                        content = f.read()
                    c_buf = (ctypes.c_char * len(content)).from_buffer_copy(content)
                    cls._buffers.append(c_buf)
                    plen[0] = len(content)
                    return ctypes.cast(c_buf, ctypes.c_void_p).value
            plen[0] = 0
            return 0

        def message_handler_cb(pduel, msg_type):
            try:
                err_msg = ctypes.string_at(pduel).decode('utf-8', errors='ignore')
                if err_msg.strip():
                    print(f"[OCGCORE LOG type={msg_type}]: {err_msg.strip()}")
            except Exception as e:
                print(f"[OCGCORE LOG ERROR]: {e}")
            return 0

        cls.c_card_reader = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_uint32, ctypes.POINTER(CardData))(card_reader_cb)
        cls.c_script_reader = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int))(script_reader_cb)
        cls.c_msg_handler = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32)(message_handler_cb)

        cls.ocg.set_card_reader(cls.c_card_reader)
        cls.ocg.set_script_reader(cls.c_script_reader)
        cls.ocg.set_message_handler(cls.c_msg_handler)

    def _get_location_codes(self, pduel, player: int = 0, location: int = 4) -> list[int]:
        """Queries C++ libocgcore.so directly for card codes currently in the given location (4=MZONE, 16=GRAVE)."""
        qbuf = ctypes.create_string_buffer(65536)
        length = self.ocg.query_field_card(pduel, player, location, 0x1, ctypes.byref(qbuf), 0)
        codes = []
        offset = 0
        raw = qbuf.raw[:length]
        while offset + 4 <= length:
            block_len = struct.unpack("<I", raw[offset:offset + 4])[0]
            if block_len <= 4:
                offset += 4
                continue
            if offset + 12 <= length:
                qflag = struct.unpack("<I", raw[offset + 4:offset + 8])[0]
                if qflag & 0x1:
                    code = struct.unpack("<I", raw[offset + 8:offset + 12])[0]
                    if code > 0:
                        codes.append(code)
            offset += block_len
        return codes

    def test_synchro_dragon_drive(self):
        """Verifies full Synchro Summon of P.U.N.K. JAM Dragon Drive with Ze Amin (L3) + Deer Note (L5)."""
        pduel = self.ocg.create_duel(42001)
        try:
            self.ocg.set_player_info(pduel, 0, 8000, 0, 0)
            self.ocg.set_player_info(pduel, 1, 8000, 0, 0)

            for i in range(40):
                self.ocg.new_card(pduel, 19535693, 0, 0, 1, i, 8)
                self.ocg.new_card(pduel, 89631139, 1, 1, 1, i, 8)

            # Extra Deck: P.U.N.K. JAM Dragon Drive (28403802)
            self.ocg.new_card(pduel, 28403802, 0, 0, 0x40, 0, 8)

            # Field: Ze Amin (19535693, L3 Tuner) + Deer Note (6609736, L5 Non-Tuner)
            self.ocg.new_card(pduel, 19535693, 0, 0, 4, 0, 1)
            self.ocg.new_card(pduel, 6609736, 0, 0, 4, 1, 1)

            self.ocg.start_duel(pduel, 5 << 16)  # MR5
            parser = YGOByteParser(self.db.card_metadata)
            msg_buf = ctypes.create_string_buffer(65536)

            for _ in range(45):
                self.ocg.process(pduel)
                length = self.ocg.get_message(pduel, ctypes.byref(msg_buf))
                if length <= 0:
                    if 28403802 in self._get_location_codes(pduel, 0, 4):
                        break
                    continue
                data = msg_buf.raw[:length]
                mtype = data[0]
                self.assertNotEqual(mtype, 1, "libocgcore rejected response with MSG_RETRY")
                parser.buffer = data
                parser.offset = 1

                if 28403802 in self._get_location_codes(pduel, 0, 4):
                    break

                if mtype == 11:  # MSG_SELECT_IDLECMD
                    parsed = parser.parse_idlecmd()
                    sps = [a for a in parsed["legal_actions"] if a.get("type") == "SPSUMMON" and a.get("card_id") == 28403802]
                    self.assertGreater(len(sps), 0, "Dragon Drive must be available as SPSUMMON")
                    target_act = sps[0]
                    resp = (target_act["sub_idx"] << 16) | 1
                    self.ocg.set_responsei(pduel, resp)
                elif mtype == 26:  # MSG_SELECT_UNSELECT_CARD
                    resp_arr = bytearray(64)
                    resp_arr[0] = 1
                    resp_arr[1] = 0
                    self.ocg.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_arr), 64))
                elif mtype == 15:  # MSG_SELECT_CARD (Tuner)
                    resp_arr = bytearray(64)
                    resp_arr[0] = 1
                    resp_arr[1] = 0
                    self.ocg.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_arr), 64))
                elif mtype == 23:  # MSG_SELECT_SUM (Non-Tuner)
                    parsed = parser.parse_select_sum()
                    must_sel = parsed.get("must_select_cards", [])
                    sel_cards = parsed.get("select_cards", [])
                    chosen = solve_select_sum(must_sel, sel_cards, parsed.get("acc", 0), parsed.get("min", 1), parsed.get("max", 1), preferred_indices=[0])
                    mcount = len(must_sel)
                    resp_arr = bytearray(64)
                    resp_arr[0] = mcount + len(chosen)
                    for j in range(mcount):
                        resp_arr[1 + j] = j
                    for j, s_idx in enumerate(chosen):
                        resp_arr[mcount + 1 + j] = s_idx
                    self.ocg.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_arr), 64))
                elif mtype == 18:  # MSG_SELECT_PLACE
                    resp_arr = bytearray(256)
                    resp_arr[0] = 0
                    resp_arr[1] = 4
                    resp_arr[2] = 2  # Zone 2
                    self.ocg.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_arr), 256))
                elif mtype == 19:  # MSG_SELECT_POSITION
                    self.ocg.set_responsei(pduel, 1)  # POS_FACEUP_ATTACK
                elif mtype in (12, 13):  # MSG_SELECT_EFFECTYN / YESNO
                    self.ocg.set_responsei(pduel, 0)
                elif mtype == 16:  # MSG_SELECT_CHAIN
                    self.ocg.set_responsei(pduel, 0xFFFFFFFF)

            mzone_codes = self._get_location_codes(pduel, 0, 4)
            grave_codes = self._get_location_codes(pduel, 0, 16)
            self.assertIn(28403802, mzone_codes, f"Dragon Drive (28403802) must be on MZONE after resolution, got {mzone_codes}")
            self.assertIn(19535693, grave_codes, "Synchro material Ze Amin must be sent to Graveyard")
            self.assertIn(6609736, grave_codes, "Synchro material Deer Note must be sent to Graveyard")
        finally:
            self.ocg.end_duel(pduel)

    def test_foxy_tune_deck_special(self):
        """Verifies Noh-P.U.N.K. Foxy Tune discard from hand to Special Summon P.U.N.K. from Deck."""
        pduel = self.ocg.create_duel(42002)
        try:
            self.ocg.set_player_info(pduel, 0, 8000, 0, 0)
            self.ocg.set_player_info(pduel, 1, 8000, 0, 0)

            # Deck: Ze Amin, Deer Note, Sharakusai
            deck = [19535693, 6609736, 13258285] + [19535693] * 37
            for i, c in enumerate(deck):
                self.ocg.new_card(pduel, c, 0, 0, 1, i, 8)
                self.ocg.new_card(pduel, 89631139, 1, 1, 1, i, 8)

            # Hand: Foxy Tune (55920742) + Fodder card (42141493)
            self.ocg.new_card(pduel, 55920742, 0, 0, 2, 0, 8)
            self.ocg.new_card(pduel, 42141493, 0, 0, 2, 1, 8)

            self.ocg.start_duel(pduel, 5 << 16)  # MR5
            parser = YGOByteParser(self.db.card_metadata)
            msg_buf = ctypes.create_string_buffer(65536)

            step_15_count = 0

            for _ in range(45):
                self.ocg.process(pduel)
                length = self.ocg.get_message(pduel, ctypes.byref(msg_buf))
                if length <= 0:
                    if self._get_location_codes(pduel, 0, 4):
                        break
                    continue
                data = msg_buf.raw[:length]
                mtype = data[0]
                self.assertNotEqual(mtype, 1, "libocgcore rejected response with MSG_RETRY")
                parser.buffer = data
                parser.offset = 1

                if self._get_location_codes(pduel, 0, 4):
                    break

                if mtype == 11:  # MSG_SELECT_IDLECMD
                    parsed = parser.parse_idlecmd()
                    foxy_acts = [a for a in parsed["legal_actions"] if a.get("card_id") == 55920742 and a.get("type") == "ACTIVATE"]
                    self.assertGreater(len(foxy_acts), 0, "Foxy Tune activate effect must be present")
                    target_act = foxy_acts[0]
                    resp = (target_act["sub_idx"] << 16) | 5
                    self.ocg.set_responsei(pduel, resp)
                elif mtype == 15:  # MSG_SELECT_CARD
                    parsed = parser.parse_select_card()
                    step_15_count += 1
                    if step_15_count == 1:
                        self.assertEqual(parsed["cards"][0]["loc"], 2, "First SELECT_CARD must select from Hand (loc=2)")
                    elif step_15_count == 2:
                        self.assertEqual(parsed["cards"][0]["loc"], 1, "Second SELECT_CARD must select from Deck (loc=1)")
                    resp_arr = bytearray(64)
                    resp_arr[0] = 1
                    resp_arr[1] = 0
                    self.ocg.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_arr), 64))
                elif mtype == 18:  # MSG_SELECT_PLACE
                    resp_arr = bytearray(256)
                    resp_arr[0] = 0
                    resp_arr[1] = 4
                    resp_arr[2] = 0  # Zone 0
                    self.ocg.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_arr), 256))
                elif mtype == 19:  # MSG_SELECT_POSITION
                    self.ocg.set_responsei(pduel, 1)
                elif mtype in (12, 13):
                    self.ocg.set_responsei(pduel, 0)
                elif mtype == 16:  # MSG_SELECT_CHAIN
                    self.ocg.set_responsei(pduel, 0xFFFFFFFF)

            mzone_codes = self._get_location_codes(pduel, 0, 4)
            grave_codes = self._get_location_codes(pduel, 0, 16)
            self.assertTrue(
                any(c in (19535693, 6609736, 13258285) for c in mzone_codes),
                f"Foxy Tune must Special Summon a P.U.N.K. monster from Deck onto MZONE, got {mzone_codes}"
            )
            self.assertIn(55920742, grave_codes, "Foxy Tune must be sent to Graveyard as cost")
            self.assertIn(42141493, grave_codes, "Discarded hand card must be sent to Graveyard")
        finally:
            self.ocg.end_duel(pduel)

    def test_fusion_rising_carp(self):
        """Verifies Ukiyoe-P.U.N.K. Sharakusai Fusion Summon into Ukiyoe-P.U.N.K. Rising Carp."""
        pduel = self.ocg.create_duel(42003)
        try:
            self.ocg.set_player_info(pduel, 0, 8000, 0, 0)
            self.ocg.set_player_info(pduel, 1, 8000, 0, 0)

            for i in range(40):
                self.ocg.new_card(pduel, 19535693, 0, 0, 1, i, 8)
                self.ocg.new_card(pduel, 89631139, 1, 1, 1, i, 8)

            # Extra Deck: Rising Carp (18313046)
            self.ocg.new_card(pduel, 18313046, 0, 0, 0x40, 0, 8)
            # Field: Sharakusai (13258285)
            self.ocg.new_card(pduel, 13258285, 0, 0, 4, 0, 1)
            # Hand: Ze Amin (19535693) as fusion material
            self.ocg.new_card(pduel, 19535693, 0, 0, 2, 0, 8)

            self.ocg.start_duel(pduel, 5 << 16)
            parser = YGOByteParser(self.db.card_metadata)
            msg_buf = ctypes.create_string_buffer(65536)

            activated = False
            for _ in range(45):
                self.ocg.process(pduel)
                length = self.ocg.get_message(pduel, ctypes.byref(msg_buf))
                if length <= 0:
                    if 18313046 in self._get_location_codes(pduel, 0, 4):
                        break
                    continue
                data = msg_buf.raw[:length]
                mtype = data[0]
                self.assertNotEqual(mtype, 1, "libocgcore rejected response with MSG_RETRY")
                parser.buffer = data
                parser.offset = 1

                if 18313046 in self._get_location_codes(pduel, 0, 4):
                    break

                if mtype == 11:
                    if not activated:
                        parsed = parser.parse_idlecmd()
                        acts = [a for a in parsed["legal_actions"] if a.get("card_id") == 13258285 and a.get("type") == "ACTIVATE"]
                        self.assertGreater(len(acts), 0, "Sharakusai Fusion effect must be present")
                        resp = (acts[0]["sub_idx"] << 16) | 5
                        self.ocg.set_responsei(pduel, resp)
                        activated = True
                    else:
                        self.ocg.set_responsei(pduel, 7)
                elif mtype == 15:
                    resp_arr = bytearray([1, 0])
                    self.ocg.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_arr), 64))
                elif mtype == 26:
                    resp_arr = bytearray(64)
                    resp_arr[0] = 1
                    resp_arr[1] = 0
                    self.ocg.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_arr), 64))
                elif mtype == 18:
                    resp_arr = bytearray(256)
                    resp_arr[0] = 0
                    resp_arr[1] = 4
                    resp_arr[2] = 2
                    self.ocg.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_arr), 256))
                elif mtype == 19:
                    self.ocg.set_responsei(pduel, 1)
                elif mtype in (12, 13):
                    self.ocg.set_responsei(pduel, 0)
                elif mtype == 16:
                    self.ocg.set_responsei(pduel, 0xFFFFFFFF)

            mzone_codes = self._get_location_codes(pduel, 0, 4)
            grave_codes = self._get_location_codes(pduel, 0, 16)
            self.assertIn(18313046, mzone_codes, f"Rising Carp (18313046) must be on MZONE after Fusion Summon, got {mzone_codes}")
            self.assertIn(13258285, grave_codes, "Fusion material Sharakusai must be in Graveyard")
            self.assertIn(19535693, grave_codes, "Fusion material Ze Amin must be in Graveyard")
        finally:
            self.ocg.end_duel(pduel)

    def test_xyz_break_sword(self):
        """Verifies Rank 3 Xyz Summon of The Phantom Knights of Break Sword using Ze Amin (L3) + Madame Spider (L3)."""
        pduel = self.ocg.create_duel(42010)
        try:
            self.ocg.set_player_info(pduel, 0, 8000, 0, 0)
            self.ocg.set_player_info(pduel, 1, 8000, 0, 0)

            for i in range(40):
                self.ocg.new_card(pduel, 19535693, 0, 0, 1, i, 8)
                self.ocg.new_card(pduel, 89631139, 1, 1, 1, i, 8)

            # Extra Deck: Break Sword (62709239, Rank 3 Xyz)
            self.ocg.new_card(pduel, 62709239, 0, 0, 0x40, 0, 8)
            # Field: Ze Amin (19535693, L3) + Spider (82041999, L3)
            self.ocg.new_card(pduel, 19535693, 0, 0, 4, 0, 1)
            self.ocg.new_card(pduel, 82041999, 0, 0, 4, 1, 1)

            self.ocg.start_duel(pduel, 5 << 16)
            parser = YGOByteParser(self.db.card_metadata)
            msg_buf = ctypes.create_string_buffer(65536)

            for _ in range(40):
                self.ocg.process(pduel)
                length = self.ocg.get_message(pduel, ctypes.byref(msg_buf))
                if length <= 0:
                    if 62709239 in self._get_location_codes(pduel, 0, 4):
                        break
                    continue
                data = msg_buf.raw[:length]
                mtype = data[0]
                self.assertNotEqual(mtype, 1, "libocgcore rejected response with MSG_RETRY")
                parser.buffer = data
                parser.offset = 1

                if 62709239 in self._get_location_codes(pduel, 0, 4):
                    break

                if mtype == 11:
                    parsed = parser.parse_idlecmd()
                    sps = [a for a in parsed["legal_actions"] if a.get("type") == "SPSUMMON" and a.get("card_id") == 62709239]
                    if sps:
                        resp = (sps[0]["sub_idx"] << 16) | 1
                        self.ocg.set_responsei(pduel, resp)
                elif mtype == 15:
                    resp_arr = bytearray(64)
                    resp_arr[0] = 2
                    resp_arr[1] = 0
                    resp_arr[2] = 1
                    self.ocg.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_arr), 64))
                elif mtype == 26:
                    resp_arr = bytearray(64)
                    resp_arr[0] = 1
                    resp_arr[1] = 0
                    self.ocg.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_arr), 64))
                elif mtype == 18:
                    resp_arr = bytearray(256)
                    resp_arr[0] = 0
                    resp_arr[1] = 4
                    resp_arr[2] = 2
                    self.ocg.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_arr), 256))
                elif mtype == 19:
                    self.ocg.set_responsei(pduel, 1)
                elif mtype in (12, 13):
                    self.ocg.set_responsei(pduel, 0)
                elif mtype == 16:
                    self.ocg.set_responsei(pduel, 0xFFFFFFFF)

            mzone_codes = self._get_location_codes(pduel, 0, 4)
            self.assertEqual(mzone_codes, [62709239], f"Break Sword (62709239) must be the sole monster on MZONE (with materials overlaid), got {mzone_codes}")
        finally:
            self.ocg.end_duel(pduel)

    def test_xyz_zombie_vampire(self):
        """Verifies Rank 8 Xyz Summon of The Zombie Vampire using Foxy Tune (L8) + Ogre Dance (L8)."""
        pduel = self.ocg.create_duel(42012)
        try:
            self.ocg.set_player_info(pduel, 0, 8000, 0, 0)
            self.ocg.set_player_info(pduel, 1, 8000, 0, 0)

            for i in range(40):
                self.ocg.new_card(pduel, 19535693, 0, 0, 1, i, 8)
                self.ocg.new_card(pduel, 89631139, 1, 1, 1, i, 8)

            # Extra Deck: The Zombie Vampire (73082255, Rank 8 Xyz)
            self.ocg.new_card(pduel, 73082255, 0, 0, 0x40, 0, 8)
            # Field: Foxy Tune (55920742, L8) + Ogre Dance (81914447, L8)
            self.ocg.new_card(pduel, 55920742, 0, 0, 4, 0, 1)
            self.ocg.new_card(pduel, 81914447, 0, 0, 4, 1, 1)

            self.ocg.start_duel(pduel, 5 << 16)
            parser = YGOByteParser(self.db.card_metadata)
            msg_buf = ctypes.create_string_buffer(65536)

            for _ in range(40):
                self.ocg.process(pduel)
                length = self.ocg.get_message(pduel, ctypes.byref(msg_buf))
                if length <= 0:
                    if 73082255 in self._get_location_codes(pduel, 0, 4):
                        break
                    continue
                data = msg_buf.raw[:length]
                mtype = data[0]
                self.assertNotEqual(mtype, 1, "libocgcore rejected response with MSG_RETRY")
                parser.buffer = data
                parser.offset = 1

                if 73082255 in self._get_location_codes(pduel, 0, 4):
                    break

                if mtype == 11:
                    parsed = parser.parse_idlecmd()
                    sps = [a for a in parsed["legal_actions"] if a.get("type") == "SPSUMMON" and a.get("card_id") == 73082255]
                    if sps:
                        resp = (sps[0]["sub_idx"] << 16) | 1
                        self.ocg.set_responsei(pduel, resp)
                elif mtype == 15:
                    resp_arr = bytearray(64)
                    resp_arr[0] = 2
                    resp_arr[1] = 0
                    resp_arr[2] = 1
                    self.ocg.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_arr), 64))
                elif mtype == 26:
                    resp_arr = bytearray(64)
                    resp_arr[0] = 1
                    resp_arr[1] = 0
                    self.ocg.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_arr), 64))
                elif mtype == 18:
                    resp_arr = bytearray(256)
                    resp_arr[0] = 0
                    resp_arr[1] = 4
                    resp_arr[2] = 2
                    self.ocg.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_arr), 256))
                elif mtype == 19:
                    self.ocg.set_responsei(pduel, 1)
                elif mtype in (12, 13):
                    self.ocg.set_responsei(pduel, 0)
                elif mtype == 16:
                    self.ocg.set_responsei(pduel, 0xFFFFFFFF)

            mzone_codes = self._get_location_codes(pduel, 0, 4)
            self.assertEqual(mzone_codes, [73082255], f"The Zombie Vampire (73082255) must be on MZONE after Xyz Summon, got {mzone_codes}")
        finally:
            self.ocg.end_duel(pduel)

    def test_link_sp_little_knight(self):
        """Verifies Link 2 Summon of S:P Little Knight using Ze Amin + Sharakusai."""
        pduel = self.ocg.create_duel(42013)
        try:
            self.ocg.set_player_info(pduel, 0, 8000, 0, 0)
            self.ocg.set_player_info(pduel, 1, 8000, 0, 0)

            for i in range(40):
                self.ocg.new_card(pduel, 19535693, 0, 0, 1, i, 8)
                self.ocg.new_card(pduel, 89631139, 1, 1, 1, i, 8)

            # Extra Deck: S:P Little Knight (29301450, Link 2)
            self.ocg.new_card(pduel, 29301450, 0, 0, 0x40, 0, 8)
            # Field: Ze Amin (19535693, Effect) + Sharakusai (13258285, Effect)
            self.ocg.new_card(pduel, 19535693, 0, 0, 4, 0, 1)
            self.ocg.new_card(pduel, 13258285, 0, 0, 4, 1, 1)

            self.ocg.start_duel(pduel, 5 << 16)
            parser = YGOByteParser(self.db.card_metadata)
            msg_buf = ctypes.create_string_buffer(65536)

            for _ in range(40):
                self.ocg.process(pduel)
                length = self.ocg.get_message(pduel, ctypes.byref(msg_buf))
                if length <= 0:
                    if 29301450 in self._get_location_codes(pduel, 0, 4):
                        break
                    continue
                data = msg_buf.raw[:length]
                mtype = data[0]
                self.assertNotEqual(mtype, 1, "libocgcore rejected response with MSG_RETRY")
                parser.buffer = data
                parser.offset = 1

                if 29301450 in self._get_location_codes(pduel, 0, 4):
                    break

                if mtype == 11:
                    parsed = parser.parse_idlecmd()
                    sps = [a for a in parsed["legal_actions"] if a.get("type") == "SPSUMMON" and a.get("card_id") == 29301450]
                    if sps:
                        resp = (sps[0]["sub_idx"] << 16) | 1
                        self.ocg.set_responsei(pduel, resp)
                elif mtype == 26:
                    resp_arr = bytearray(64)
                    resp_arr[0] = 1
                    resp_arr[1] = 0
                    self.ocg.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_arr), 64))
                elif mtype == 18:
                    resp_arr = bytearray(256)
                    resp_arr[0] = 0
                    resp_arr[1] = 4
                    resp_arr[2] = 5  # EMZ
                    self.ocg.set_responseb(pduel, ctypes.create_string_buffer(bytes(resp_arr), 256))
                elif mtype == 19:
                    self.ocg.set_responsei(pduel, 1)
                elif mtype in (12, 13):
                    self.ocg.set_responsei(pduel, 0)
                elif mtype == 16:
                    self.ocg.set_responsei(pduel, 0xFFFFFFFF)

            mzone_codes = self._get_location_codes(pduel, 0, 4)
            grave_codes = self._get_location_codes(pduel, 0, 16)
            self.assertEqual(mzone_codes, [29301450], f"S:P Little Knight (29301450) must be on EMZ/MZONE after Link Summon, got {mzone_codes}")
            self.assertIn(19535693, grave_codes, "Link material Ze Amin must be in Graveyard")
            self.assertIn(13258285, grave_codes, "Link material Sharakusai must be in Graveyard")
        finally:
            self.ocg.end_duel(pduel)


if __name__ == "__main__":
    unittest.main()
