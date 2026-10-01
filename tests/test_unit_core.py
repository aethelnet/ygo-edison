import unittest
import os
import sys
import ctypes
import struct

# Ensure apps/ygo_rl is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ygo_sqlite_bridge import YGOSqliteBridge
from ygo_byte_parser import YGOByteParser
from ygo_self_play import action_to_response_code


class TestYGOSqliteBridge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = YGOSqliteBridge()

    def test_database_loaded(self):
        """Verifies that the SQLite cards database is indexed with cards."""
        self.assertGreater(len(self.db.card_metadata), 1000)
        self.assertGreater(len(self.db.system_strings), 100)

    def test_card_lookup_dandylion(self):
        """Verifies specific Edison card metadata lookup (Dandylion)."""
        card_id = 15341821  # Dandylion
        info = self.db.card_metadata.get(card_id)
        self.assertIsNotNone(info)
        self.assertEqual(info["name"], "Dandylion")
        self.assertEqual(info["atk"], 300)
        self.assertEqual(info["def"], 300)
        self.assertEqual(info["level"], 3)

    def test_card_lookup_heavy_storm(self):
        """Verifies spell card metadata lookup (Heavy Storm)."""
        card_id = 19613556  # Heavy Storm
        info = self.db.card_metadata.get(card_id)
        self.assertIsNotNone(info)
        self.assertEqual(info["name"], "Heavy Storm")
        # Spell type flag
        self.assertTrue(info["type"] & 0x2)


class TestYGOByteParser(unittest.TestCase):
    def setUp(self):
        self.parser = YGOByteParser()

    def test_primitive_readers(self):
        """Tests low-level binary buffer reading methods."""
        # 1 byte (0x42), 2 bytes LE (0x1234), 4 bytes LE (0x01020304)
        buf = struct.pack("<BhI", 0x42, 0x1234, 0x01020304)
        self.parser.buffer = buf
        self.parser.offset = 0

        self.assertEqual(self.parser.read_byte(), 0x42)
        self.assertEqual(self.parser.read_int16(), 0x1234)
        self.assertEqual(self.parser.read_int32(), 0x01020304)
        self.assertEqual(self.parser.read_byte(), 0)  # EOF returns 0

    def test_parse_idlecmd_synthetic_buffer(self):
        """Tests synthetic IDLECMD parsing for player summon and end-phase actions."""
        # MSG_SELECT_IDLECMD format:
        # byte player
        # byte summon_count: 1 summon (int32 code=15341821, byte con=0, byte loc=2, byte seq=0)
        # byte spsummon_count: 0
        # byte repos_count: 0
        # byte mset_count: 0
        # byte sset_count: 0
        # byte activate_count: 0
        # byte to_bp: 0
        # byte to_ep: 1
        # byte shuffle: 0
        buf = bytearray()
        buf.append(0)  # player 0
        # 1. summon
        buf.append(1)  # 1 summon
        buf.extend(struct.pack("<i", 15341821))
        buf.extend([0, 2, 0])
        # 2. spsummon
        buf.append(0)
        # 3. repos
        buf.append(0)
        # 4. mset
        buf.append(0)
        # 5. sset
        buf.append(0)
        # 6. activate
        buf.append(0)
        # 7. to_bp
        buf.append(0)
        # 8. to_ep
        buf.append(1)
        # 9. shuffle
        buf.append(0)

        self.parser.buffer = bytes(buf)
        self.parser.offset = 0
        parsed = self.parser.parse_idlecmd()

        self.assertEqual(parsed["player"], 0)
        actions = parsed["legal_actions"]
        self.assertEqual(len(actions), 2)
        self.assertEqual(actions[0]["type"], "SUMMON")
        self.assertEqual(actions[0]["card_id"], 15341821)
        self.assertEqual(actions[1]["type"], "TO_END_PHASE")


class TestActionEncoder(unittest.TestCase):
    def test_response_code_encoding(self):
        """Verifies action encoding to 32-bit response codes for OCGCore."""
        act_summon = {"type": "SUMMON", "sub_idx": 2}
        resp = action_to_response_code(act_summon, 2, 11)
        self.assertEqual(resp, (2 << 16) | 0)

        act_ep = {"type": "TO_END_PHASE", "sub_idx": 0}
        resp_ep = action_to_response_code(act_ep, 0, 11)
        self.assertEqual(resp_ep, 7)  # IDLECMD TO_END_PHASE index is 7


class TestOCGCoreScenario(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "ygopro-core/bin/Release/libocgcore.so")
        )
        if not os.path.exists(cls.core_path):
            raise unittest.SkipTest("libocgcore.so not built at expected path")
        cls.ocg = ctypes.cdll.LoadLibrary(cls.core_path)
        cls.db = YGOSqliteBridge()

        cls.ocg.create_duel.restype = ctypes.c_void_p
        cls.ocg.create_duel.argtypes = [ctypes.c_uint32]
        cls.ocg.set_player_info.argtypes = [ctypes.c_void_p, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32]
        cls.ocg.new_card.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint8]
        cls.ocg.start_duel.argtypes = [ctypes.c_void_p, ctypes.c_int32]
        cls.ocg.process.restype = ctypes.c_int32
        cls.ocg.process.argtypes = [ctypes.c_void_p]
        cls.ocg.get_message.restype = ctypes.c_int32
        cls.ocg.get_message.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

        class CardData(ctypes.Structure):
            _fields_ = [
                ("code", ctypes.c_uint32), ("alias", ctypes.c_uint32), ("setcode", ctypes.c_uint16 * 16),
                ("type", ctypes.c_uint32), ("level", ctypes.c_uint32), ("attribute", ctypes.c_uint32),
                ("race", ctypes.c_uint32), ("attack", ctypes.c_int32), ("defense", ctypes.c_int32),
                ("lscale", ctypes.c_uint32), ("rscale", ctypes.c_uint32), ("link_marker", ctypes.c_uint32),
                ("rule_code", ctypes.c_uint32)
            ]

        db_ref = cls.db
        @ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_uint32, ctypes.POINTER(CardData))
        def card_reader_cb(code, pdata):
            ctypes.memset(pdata, 0, ctypes.sizeof(CardData))
            pdata.contents.code = code
            info = db_ref.card_metadata.get(code)
            if info:
                pdata.contents.alias = info.get("alias", 0)
                pdata.contents.attack = max(0, info.get("atk", 0))
                pdata.contents.defense = max(0, info.get("def", 0))
                pdata.contents.type = info.get("type", 0)
                pdata.contents.level = info.get("level", 0)
                pdata.contents.race = info.get("race", 0)
                pdata.contents.attribute = info.get("attribute", 0)
            return 1 if info else 0

        script_dirs = [
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "script")),
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "ygopro-core/script")),
            os.path.join(os.path.expanduser("~"), "auratic-systems-prime", "backend_clean", "script")
        ]
        cls.kept_buffers = []
        @ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int32))
        def script_reader_cb(name, plen):
            sname = name.decode("utf-8")
            for sdir in script_dirs:
                p = os.path.join(sdir, sname)
                if os.path.exists(p):
                    with open(p, "rb") as f:
                        content = f.read()
                        plen[0] = len(content)
                        buf = (ctypes.c_char * len(content)).from_buffer_copy(content)
                        cls.kept_buffers.append(buf)
                        return ctypes.cast(buf, ctypes.c_void_p)
            plen[0] = 0
            return 0

        cls.c_card_reader = card_reader_cb
        cls.c_script_reader = script_reader_cb
        cls.c_msg_handler = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32)(lambda p, m: 0)

        cls.ocg.set_card_reader(cls.c_card_reader)
        cls.ocg.set_script_reader(cls.c_script_reader)
        cls.ocg.set_message_handler(cls.c_msg_handler)

    def test_scenario_execution(self):
        """Runs a deterministic duel scenario against libocgcore C-API."""
        ocg = self.ocg
        pduel = ocg.create_duel(777)
        self.assertIsNotNone(pduel)

        # Set LP: 4000, 3 cards in hand
        ocg.set_player_info(pduel, 0, 4000, 3, 0)
        ocg.set_player_info(pduel, 1, 4000, 0, 0)

        # Dandylion (15341821), Heavy Storm (19613556)
        ocg.new_card(pduel, 15341821, 0, 0, 1, 0, 8)
        ocg.new_card(pduel, 19613556, 0, 0, 1, 1, 8)

        ocg.start_duel(pduel, 0x08)

        buf = ctypes.create_string_buffer(65536)
        parser = YGOByteParser(self.db.card_metadata)

        found_idlecmd = False
        for _ in range(50):
            status = ocg.process(pduel)
            msg_len = ocg.get_message(pduel, ctypes.cast(buf, ctypes.c_void_p))
            if msg_len > 0:
                mtype = buf.raw[0]
                parser.buffer = buf.raw[:msg_len]
                parser.offset = 1
                if mtype == 11:  # MSG_SELECT_IDLECMD
                    res = parser.parse_idlecmd()
                    action_types = {act["type"] for act in res["legal_actions"]}
                    self.assertIn("SUMMON", action_types)
                    self.assertIn("SSET", action_types)
                    self.assertIn("TO_END_PHASE", action_types)
                    found_idlecmd = True
                    break

        self.assertTrue(found_idlecmd, "Should reach MSG_SELECT_IDLECMD in duel simulation")

    def test_modern_master_rule_5_execution(self):
        """Verifies deterministic duel execution under Modern Master Rule 5 (5 << 16)."""
        ocg = self.ocg
        pduel = ocg.create_duel(888)
        self.assertIsNotNone(pduel)

        ocg.set_player_info(pduel, 0, 8000, 5, 0)
        ocg.set_player_info(pduel, 1, 8000, 5, 0)

        # Dandylion (15341821), Heavy Storm (19613556)
        ocg.new_card(pduel, 15341821, 0, 0, 1, 0, 8)
        ocg.new_card(pduel, 19613556, 0, 0, 1, 1, 8)

        # Start duel with Modern MR5: 5 << 16
        ocg.start_duel(pduel, 5 << 16)

        buf = ctypes.create_string_buffer(65536)
        parser = YGOByteParser(self.db.card_metadata)

        found_idlecmd = False
        for _ in range(50):
            status = ocg.process(pduel)
            msg_len = ocg.get_message(pduel, ctypes.cast(buf, ctypes.c_void_p))
            if msg_len > 0:
                mtype = buf.raw[0]
                parser.buffer = buf.raw[:msg_len]
                parser.offset = 1
                if mtype == 11:
                    res = parser.parse_idlecmd()
                    action_types = {act["type"] for act in res["legal_actions"]}
                    self.assertIn("SUMMON", action_types)
                    self.assertIn("SSET", action_types)
                    self.assertIn("TO_END_PHASE", action_types)
                    found_idlecmd = True
                    break

        self.assertTrue(found_idlecmd, "Should reach MSG_SELECT_IDLECMD in Modern MR5 duel simulation")


if __name__ == "__main__":
    unittest.main()
