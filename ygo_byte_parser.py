import struct
from loguru import logger

class YGOByteParser:
    def __init__(self, card_db_lookup=None):
        self.card_db = card_db_lookup or {}
        self.buffer = b""
        self.offset = 0

    def read_byte(self):
        if self.offset < len(self.buffer):
            b = self.buffer[self.offset]
            self.offset += 1
            return b
        return 0

    def read_int16(self):
        if self.offset + 2 <= len(self.buffer):
            val = struct.unpack("<h", self.buffer[self.offset:self.offset+2])[0]
            self.offset += 2
            return val
        return 0

    def read_int32(self):
        if self.offset + 4 <= len(self.buffer):
            val = struct.unpack("<i", self.buffer[self.offset:self.offset+4])[0]
            self.offset += 4
            return val
        return 0

    def read_uint32(self):
        if self.offset + 4 <= len(self.buffer):
            val = struct.unpack("<I", self.buffer[self.offset:self.offset+4])[0]
            self.offset += 4
            return val
        return 0

    def parse_idlecmd(self):
        """MSG_SELECT_IDLECMD (11) - Main Phase Optionen"""
        player = self.read_byte()
        options = []

        # 1. Normalbeschwörungen (Hand)
        summon_count = self.read_byte()
        for i in range(summon_count):
            code = self.read_int32() & 0x7FFFFFFF
            con = self.read_byte()
            loc = self.read_byte()
            seq = self.read_byte()
            options.append({"type": "SUMMON", "card_id": code, "sub_idx": i, "con": con, "loc": loc, "seq": seq})

        # 2. Spezialbeschwörungen
        spsummon_count = self.read_byte()
        for i in range(spsummon_count):
            code = self.read_int32() & 0x7FFFFFFF
            con = self.read_byte()
            loc = self.read_byte()
            seq = self.read_byte()
            options.append({"type": "SPSUMMON", "card_id": code, "sub_idx": i, "con": con, "loc": loc, "seq": seq})

        # 3. Reposition / Positionsänderungen
        repos_count = self.read_byte()
        for i in range(repos_count):
            code = self.read_int32() & 0x7FFFFFFF
            con = self.read_byte()
            loc = self.read_byte()
            seq = self.read_byte()
            options.append({"type": "REPOS", "card_id": code, "sub_idx": i, "con": con, "loc": loc, "seq": seq})

        # 4. Monster verdeckt setzen (MSET)
        mset_count = self.read_byte()
        for i in range(mset_count):
            code = self.read_int32() & 0x7FFFFFFF
            con = self.read_byte()
            loc = self.read_byte()
            seq = self.read_byte()
            options.append({"type": "MSET", "card_id": code, "sub_idx": i, "con": con, "loc": loc, "seq": seq})

        # 5. Zauber/Fallen setzen (SSET)
        sset_count = self.read_byte()
        for i in range(sset_count):
            code = self.read_int32() & 0x7FFFFFFF
            con = self.read_byte()
            loc = self.read_byte()
            seq = self.read_byte()
            options.append({"type": "SSET", "card_id": code, "sub_idx": i, "con": con, "loc": loc, "seq": seq})

        # 6. Aktivierbare Effekte / Zauber (ACTIVATE)
        act_count = self.read_byte()
        for i in range(act_count):
            code = self.read_int32() & 0x7FFFFFFF
            con = self.read_byte()
            loc = self.read_byte()
            seq = self.read_byte()
            desc = self.read_uint32()
            options.append({
                "type": "ACTIVATE", 
                "card_id": code, 
                "desc": desc, 
                "sub_idx": i, 
                "con": con, 
                "loc": loc, 
                "seq": seq,
                "is_hand": (loc == 2)
            })

        # 7. Phasenwechsel
        can_bp = self.read_byte()
        can_ep = self.read_byte()
        can_shuffle = self.read_byte()

        if can_bp:
            options.append({"type": "TO_BATTLE_PHASE", "sub_idx": 0})
        if can_ep:
            options.append({"type": "TO_END_PHASE", "sub_idx": 0})

        return {
            "event": "MSG_SELECT_IDLECMD",
            "player": player,
            "legal_actions": options
        }

    def parse_battlecmd(self):
        """MSG_SELECT_BATTLECMD (10) - Battle Phase Optionen"""
        player = self.read_byte()
        options = []

        # 1. Aktivierbare Effekte im Battle Step
        act_count = self.read_byte()
        for i in range(act_count):
            code = self.read_int32() & 0x7FFFFFFF
            con = self.read_byte()
            loc = self.read_byte()
            seq = self.read_byte()
            desc = self.read_uint32()
            options.append({"type": "ACTIVATE", "card_id": code, "desc": desc, "sub_idx": i, "con": con, "loc": loc, "seq": seq})

        # 2. Angreifbare Monster
        atk_count = self.read_byte()
        for i in range(atk_count):
            code = self.read_int32() & 0x7FFFFFFF
            con = self.read_byte()
            loc = self.read_byte()
            seq = self.read_byte()
            direct = self.read_byte()
            options.append({"type": "ATTACK", "card_id": code, "direct": direct, "sub_idx": i, "con": con, "loc": loc, "seq": seq})

        # 3. Phasenwechsel
        can_m2 = self.read_byte()
        can_ep = self.read_byte()

        if can_m2:
            options.append({"type": "TO_MAIN_PHASE_2", "sub_idx": 0})
        if can_ep:
            options.append({"type": "TO_END_PHASE", "sub_idx": 0})

        return {
            "event": "MSG_SELECT_BATTLECMD",
            "player": player,
            "legal_actions": options
        }

    def parse_chain(self):
        """MSG_SELECT_CHAIN (16) - Ketten-Abfrage"""
        player = self.read_byte()
        count = self.read_byte()
        spe_count = self.read_byte()
        hint1 = self.read_int32()
        hint2 = self.read_int32()
        options = []
        forced_count = 0
        for i in range(count):
            flag1 = self.read_byte()
            flag2 = self.read_byte()
            code = self.read_int32() & 0x7FFFFFFF
            loc_info = self.read_int32()
            con = loc_info & 0xFF
            loc = (loc_info >> 8) & 0xFF
            seq = (loc_info >> 16) & 0xFF
            pos = (loc_info >> 24) & 0xFF
            desc = self.read_uint32()
            loc_name = "in Hand" if loc == 2 else ("auf Feld" if loc in [4, 8] else "im Friedhof" if loc == 16 else "in Zone")
            is_forced = bool(flag2 & 1)
            if is_forced:
                forced_count += 1
            options.append({
                "type": "ACTIVATE_CHAIN", 
                "card_id": code, 
                "desc": desc, 
                "sub_idx": i, 
                "con": con, 
                "loc": loc, 
                "seq": seq,
                "pos": pos,
                "loc_name": loc_name,
                "forced": is_forced,
                "edesc": flag1
            })

        # In Yu-Gi-Oh & libocgcore: If ANY trigger is forced, passing (-1) triggers MSG_RETRY!
        can_pass = (forced_count == 0)
        if can_pass:
            options.append({"type": "CANCEL_CHAIN", "name": "Kette nicht fortsetzen (Passen)", "sub_idx": -1})
        return {
            "event": "MSG_SELECT_CHAIN",
            "player": player,
            "count": count,
            "spe_count": spe_count,
            "forced_count": forced_count,
            "can_pass": can_pass,
            "legal_actions": options
        }

    def parse_select_position(self):
        """MSG_SELECT_POSITION (19) - Kampfposition wählen"""
        player = self.read_byte()
        code = self.read_int32() & 0x7FFFFFFF
        positions = self.read_byte()
        options = []
        if positions & 0x1: options.append({"type": "POS_FACEUP_ATTACK", "name": "Offene Angriffsposition", "card_id": code, "sub_idx": 1})
        if positions & 0x4: options.append({"type": "POS_FACEUP_DEFENSE", "name": "Offene Verteidigungsposition", "card_id": code, "sub_idx": 4})
        return {"event": "MSG_SELECT_POSITION", "player": player, "card_id": code, "legal_actions": options}

    def parse_select_option(self):
        """MSG_SELECT_OPTION (14) - Effekt-Option wählen"""
        player = self.read_byte()
        count = self.read_byte()
        options = []
        for i in range(count):
            desc = self.read_uint32()
            options.append({"type": "OPTION", "desc": desc, "sub_idx": i})
        return {"event": "MSG_SELECT_OPTION", "player": player, "legal_actions": options}

    def parse_select_place(self):
        """MSG_SELECT_PLACE (18) - Spielfeld-Zone für Karte auswählen"""
        player = self.read_byte()
        count = self.read_byte()
        flag = self.read_int32()
        options = []
        # Main Monster Zones 0..4 (bits 0..4)
        for s in range(5):
            if (flag & (1 << s)) == 0:
                options.append({
                    "type": "SELECT_ZONE",
                    "name": f"Monsterzone {s + 1}",
                    "zone_player": player,
                    "loc": 4,
                    "seq": s,
                    "con": player,
                    "sub_idx": len(options)
                })
        # Extra Monster Zones (s = 5, 6, bits 5, 6)
        for s in [5, 6]:
            if (flag & (1 << s)) == 0:
                options.append({
                    "type": "SELECT_ZONE",
                    "name": f"Extra-Monsterzone {'Links' if s == 5 else 'Rechts'}",
                    "zone_player": player,
                    "loc": 4,
                    "seq": s,
                    "con": player,
                    "sub_idx": len(options)
                })
        # Spell / Trap Zones 0..4 (bits 8..12)
        for s in range(5):
            bit = 8 + s
            if (flag & (1 << bit)) == 0:
                options.append({
                    "type": "SELECT_ZONE",
                    "name": f"Zauber-/Fallenzone {s + 1}",
                    "zone_player": player,
                    "loc": 8,
                    "seq": s,
                    "con": player,
                    "sub_idx": len(options)
                })
        # Field Spell Zone (s = 5, bit 13)
        if (flag & (1 << 13)) == 0:
            options.append({
                "type": "SELECT_ZONE",
                "name": "Spielfeldzauber-Zone",
                "zone_player": player,
                "loc": 8,
                "seq": 5,
                "con": player,
                "sub_idx": len(options)
            })
            
        return {
            "event": "MSG_SELECT_PLACE",
            "player": player,
            "count": count,
            "flag": flag,
            "legal_actions": options
        }

    def parse_select_card(self):
        """MSG_SELECT_CARD (15) - Karten auswählen / Angriffsziel"""
        player = self.read_byte()
        cancelable = self.read_byte()
        min_cards = self.read_byte()
        max_cards = self.read_byte()
        count = self.read_byte()
        cards = []
        for i in range(count):
            code = self.read_int32() & 0x7FFFFFFF
            loc_info = self.read_int32()
            con = loc_info & 0xFF
            loc = (loc_info >> 8) & 0xFF
            seq = (loc_info >> 16) & 0xFF
            atype = "DIRECT_ATTACK" if code == 0 else "TARGET_CARD"
            name = "Direkter Angriff (Direkt auf Spieler)" if code == 0 else None
            cards.append({
                "type": atype,
                "card_id": code,
                "name": name,
                "sub_idx": i,
                "min": min_cards,
                "max": max_cards,
                "con": con,
                "loc": loc,
                "seq": seq
            })
        return {
            "event": "MSG_SELECT_CARD",
            "player": player,
            "cards": cards,
            "legal_actions": cards
        }

    def parse_select_effectyn(self):
        """MSG_SELECT_EFFECTYN (12) - Effektauslösung bestätigen (Hand-Trigger / Hand-Trap / Friedhof)"""
        player = self.read_byte()
        code = self.read_int32() & 0x7FFFFFFF
        loc_info = self.read_int32()
        con = loc_info & 0xFF
        loc = (loc_info >> 8) & 0xFF
        seq = (loc_info >> 16) & 0xFF
        desc = self.read_uint32()
        loc_str = "in der Hand" if loc == 2 else ("auf dem Feld" if loc in [4, 8] else "im Friedhof" if loc == 16 else "in Zone")
        return {
            "event": "MSG_SELECT_EFFECTYN",
            "player": player,
            "card_id": code,
            "desc": desc,
            "con": con,
            "loc": loc,
            "seq": seq,
            "legal_actions": [
                {
                    "type": "EFFECT_YES", 
                    "card_id": code, 
                    "name": f"Effekt aktivieren ({loc_str})", 
                    "sub_idx": 1,
                    "con": con,
                    "loc": loc,
                    "seq": seq
                },
                {
                    "type": "EFFECT_NO", 
                    "name": "Effekt nicht aktivieren (Nein)", 
                    "sub_idx": 0
                }
            ]
        }

    def parse_select_yesno(self):
        """MSG_SELECT_YESNO (13) - Ja/Nein Abfrage"""
        player = self.read_byte()
        desc = self.read_uint32()
        return {
            "event": "MSG_SELECT_YESNO",
            "player": player,
            "desc": desc,
            "legal_actions": [
                {"type": "YES", "name": "Ja", "sub_idx": 1}, 
                {"type": "NO", "name": "Nein", "sub_idx": 0}
            ]
        }

    def parse_select_sum(self):
        """MSG_SELECT_SUM (23) - Synchro / Ritual / Extra Deck Material Auswahl"""
        mode = self.read_byte()
        player = self.read_byte()
        acc = self.read_int32()
        min_cards = self.read_byte()
        max_cards = self.read_byte()

        must_select_count = self.read_byte()
        must_select_cards = []
        for i in range(must_select_count):
            code = self.read_int32() & 0x7FFFFFFF
            con = self.read_byte()
            loc = self.read_byte()
            seq = self.read_byte()
            sum_param = self.read_int32()
            op1 = sum_param & 0xFFFF
            op2 = (sum_param >> 16) & 0xFFFF
            if op2 & 0x8000:
                op1 = sum_param & 0x7FFFFFFF
                op2 = 0
            must_select_cards.append({
                "card_id": code, "con": con, "loc": loc, "seq": seq,
                "sum_param": sum_param, "op1": op1, "op2": op2, "must": True
            })

        select_count = self.read_byte()
        select_cards = []
        for i in range(select_count):
            code = self.read_int32() & 0x7FFFFFFF
            con = self.read_byte()
            loc = self.read_byte()
            seq = self.read_byte()
            sum_param = self.read_int32()
            op1 = sum_param & 0xFFFF
            op2 = (sum_param >> 16) & 0xFFFF
            if op2 & 0x8000:
                op1 = sum_param & 0x7FFFFFFF
                op2 = 0
            select_cards.append({
                "type": "SELECT_SUM_CARD",
                "card_id": code,
                "sub_idx": i,
                "con": con,
                "loc": loc,
                "seq": seq,
                "sum_param": sum_param,
                "op1": op1,
                "op2": op2,
                "min": min_cards,
                "max": max_cards,
                "acc": acc,
                "must_select_count": must_select_count
            })

        return {
            "event": "MSG_SELECT_SUM",
            "player": player,
            "mode": mode,
            "acc": acc,
            "min": min_cards,
            "max": max_cards,
            "must_select_count": must_select_count,
            "must_select_cards": must_select_cards,
            "select_cards": select_cards,
            "legal_actions": select_cards
        }

    def parse_select_tribute(self):
        """MSG_SELECT_TRIBUTE (20) - Tributbeschwörung / Material-Freisetzung"""
        player = self.read_byte()
        cancelable = self.read_byte()
        min_cards = self.read_byte()
        max_cards = self.read_byte()
        count = self.read_byte()
        cards = []
        for i in range(count):
            code = self.read_int32() & 0x7FFFFFFF
            con = self.read_byte()
            loc = self.read_byte()
            seq = self.read_byte()
            release_param = self.read_byte()
            cards.append({
                "type": "SELECT_TRIBUTE",
                "card_id": code,
                "sub_idx": i,
                "con": con,
                "loc": loc,
                "seq": seq,
                "release_param": release_param,
                "min": min_cards,
                "max": max_cards,
                "cancelable": cancelable
            })
        if cancelable:
            cards.append({"type": "CANCEL", "name": "Abbrechen", "sub_idx": -1})
        return {
            "event": "MSG_SELECT_TRIBUTE",
            "player": player,
            "min": min_cards,
            "max": max_cards,
            "cancelable": cancelable,
            "cards": cards,
            "legal_actions": cards
        }

    def parse_select_unselect_card(self):
        """MSG_SELECT_UNSELECT_CARD (26) - Flexible Materialauswahl"""
        player = self.read_byte()
        finishable = self.read_byte()
        cancelable = self.read_byte()
        min_cards = self.read_byte()
        max_cards = self.read_byte()
        select_count = self.read_byte()
        cards = []
        for i in range(select_count):
            code = self.read_int32() & 0x7FFFFFFF
            loc_info = self.read_int32()
            con = loc_info & 0xFF
            loc = (loc_info >> 8) & 0xFF
            seq = (loc_info >> 16) & 0xFF
            cards.append({
                "type": "SELECT_UNSELECT",
                "card_id": code,
                "sub_idx": i,
                "con": con,
                "loc": loc,
                "seq": seq,
                "min": min_cards,
                "max": max_cards
            })
        unselect_count = self.read_byte()
        for i in range(unselect_count):
            code = self.read_int32() & 0x7FFFFFFF
            loc_info = self.read_int32()
            con = loc_info & 0xFF
            loc = (loc_info >> 8) & 0xFF
            seq = (loc_info >> 16) & 0xFF
            cards.append({
                "type": "UNSELECT",
                "card_id": code,
                "sub_idx": select_count + i,
                "con": con,
                "loc": loc,
                "seq": seq,
                "min": min_cards,
                "max": max_cards
            })
        if finishable or cancelable:
            cards.append({"type": "FINISH_SELECT", "name": "Auswahl abschließen", "sub_idx": -1})
        return {
            "event": "MSG_SELECT_UNSELECT_CARD",
            "player": player,
            "finishable": finishable,
            "cancelable": cancelable,
            "min": min_cards,
            "max": max_cards,
            "cards": cards,
            "legal_actions": cards
        }

    def parse_sort_card(self):
        """MSG_SORT_CARD (25) - Kartenreihenfolge sortieren (z.B. Pot of Duality)"""
        player = self.read_byte()
        count = self.read_byte()
        cards = []
        for i in range(count):
            code = self.read_int32() & 0x7FFFFFFF
            con = self.read_byte()
            loc = self.read_byte()
            seq = self.read_byte()
            cards.append({
                "type": "SORT_CARD",
                "card_id": code,
                "sub_idx": i,
                "con": con,
                "loc": loc,
                "seq": seq
            })
        return {
            "event": "MSG_SORT_CARD",
            "player": player,
            "count": count,
            "cards": cards,
            "legal_actions": cards
        }

    def parse_select_disfield(self):
        """MSG_SELECT_DISFIELD (24) - Zonen sperren / deaktivieren (z.B. Kashtira, Ojama)"""
        player = self.read_byte()
        count = self.read_byte()
        flag = self.read_int32()
        options = []
        # Main Monster Zones 0..4 (bits 0..4 for player, bits 16..20 for opponent)
        for p in [player, 1 - player]:
            shift = 0 if p == player else 16
            p_label = "Deine" if p == player else "Gegnerische"
            for s in range(5):
                bit = 1 << (s + shift)
                if (flag & bit) == 0:
                    options.append({
                        "type": "DISFIELD_ZONE",
                        "name": f"{p_label} Monsterzone {s + 1}",
                        "zone_player": p,
                        "loc": 4,
                        "seq": s,
                        "sub_idx": len(options)
                    })
            for s in [5, 6]:
                bit = 1 << (s + shift)
                if (flag & bit) == 0:
                    options.append({
                        "type": "DISFIELD_ZONE",
                        "name": f"{p_label} Extra-Monsterzone {'Links' if s == 5 else 'Rechts'}",
                        "zone_player": p,
                        "loc": 4,
                        "seq": s,
                        "sub_idx": len(options)
                    })
            for s in range(5):
                bit = 1 << (8 + s + shift)
                if (flag & bit) == 0:
                    options.append({
                        "type": "DISFIELD_ZONE",
                        "name": f"{p_label} Zauber-/Fallenzone {s + 1}",
                        "zone_player": p,
                        "loc": 8,
                        "seq": s,
                        "sub_idx": len(options)
                    })
        return {
            "event": "MSG_SELECT_DISFIELD",
            "player": player,
            "count": count,
            "flag": flag,
            "legal_actions": options
        }

    def parse_select_counter(self):
        """MSG_SELECT_COUNTER (22) - Counter verteilen / entfernen"""
        player = self.read_byte()
        countertype = self.read_int16()
        count = self.read_int16()
        card_count = self.read_byte()
        cards = []
        for i in range(card_count):
            code = self.read_int32() & 0x7FFFFFFF
            con = self.read_byte()
            loc = self.read_byte()
            seq = self.read_byte()
            current_counters = self.read_int16()
            cards.append({
                "type": "SELECT_COUNTER_CARD",
                "card_id": code,
                "con": con,
                "loc": loc,
                "seq": seq,
                "current_counters": current_counters,
                "sub_idx": i
            })
        return {
            "event": "MSG_SELECT_COUNTER",
            "player": player,
            "countertype": countertype,
            "count": count,
            "card_count": card_count,
            "cards": cards,
            "legal_actions": cards
        }

    def parse_announce_race(self):
        """MSG_ANNOUNCE_RACE (140) - Monstertyp deklarieren"""
        player = self.read_byte()
        count = self.read_byte()
        available = self.read_int32()
        
        race_names = {
            0x1: "Krieger (Warrior)",
            0x2: "Hexer (Spellcaster)",
            0x4: "Fee (Fairy)",
            0x8: "Unterweltler (Fiend)",
            0x10: "Zombie",
            0x20: "Maschine (Machine)",
            0x40: "Aqua",
            0x80: "Pyro",
            0x100: "Fels (Rock)",
            0x200: "Geflügeltes Ungeheuer (Winged Beast)",
            0x400: "Pflanze (Plant)",
            0x800: "Insekt (Insect)",
            0x1000: "Donner (Thunder)",
            0x2000: "Drache (Dragon)",
            0x4000: "Ungeheuer (Beast)",
            0x8000: "Ungeheuer-Krieger (Beast-Warrior)",
            0x10000: "Dinosaurier (Dinosaur)",
            0x20000: "Fisch (Fish)",
            0x40000: "Seeschlange (Sea Serpent)",
            0x80000: "Reptil (Reptile)",
            0x100000: "Psi (Psychic)",
            0x200000: "Göttliches Ungeheuer (Divine-Beast)",
            0x400000: "Schöpfer (Creator-God)",
            0x800000: "Wyrm",
            0x1000000: "Cyberse",
            0x2000000: "Illusion"
        }
        options = []
        for bit, name in race_names.items():
            if available & bit:
                options.append({
                    "type": "ANNOUNCE_RACE",
                    "name": name,
                    "value": bit,
                    "sub_idx": bit
                })
        return {
            "event": "MSG_ANNOUNCE_RACE",
            "player": player,
            "count": count,
            "available": available,
            "legal_actions": options
        }

    def parse_announce_attrib(self):
        """MSG_ANNOUNCE_ATTRIB (141) - Eigenschaft deklarieren"""
        player = self.read_byte()
        count = self.read_byte()
        available = self.read_int32()
        
        attrib_names = {
            0x01: "ERDE (Earth)",
            0x02: "WASSER (Water)",
            0x04: "FEUER (Fire)",
            0x08: "WIND",
            0x10: "LICHT (Light)",
            0x20: "FINSTERNIS (Dark)",
            0x40: "GÖTTLICH (Divine)"
        }
        options = []
        for bit, name in attrib_names.items():
            if available & bit:
                options.append({
                    "type": "ANNOUNCE_ATTRIB",
                    "name": name,
                    "value": bit,
                    "sub_idx": bit
                })
        return {
            "event": "MSG_ANNOUNCE_ATTRIB",
            "player": player,
            "count": count,
            "available": available,
            "legal_actions": options
        }

    def parse_announce_card(self):
        """MSG_ANNOUNCE_CARD (142) - Karte deklarieren (z.B. Crossout Designator)"""
        player = self.read_byte()
        count = self.read_byte()
        opcodes = []
        for _ in range(count):
            opcodes.append(self.read_int32())
        return {
            "event": "MSG_ANNOUNCE_CARD",
            "player": player,
            "count": count,
            "opcodes": opcodes,
            "legal_actions": [{"type": "ANNOUNCE_CARD", "name": "Karte benennen", "sub_idx": 0}]
        }

    def parse_announce_number(self):
        """MSG_ANNOUNCE_NUMBER (143) - Zahl auswählen"""
        player = self.read_byte()
        count = self.read_byte()
        options = []
        for i in range(count):
            val = self.read_int32()
            options.append({
                "type": "ANNOUNCE_NUMBER",
                "name": f"Zahl: {val}",
                "value": val,
                "sub_idx": i
            })
        return {
            "event": "MSG_ANNOUNCE_NUMBER",
            "player": player,
            "count": count,
            "legal_actions": options
        }

