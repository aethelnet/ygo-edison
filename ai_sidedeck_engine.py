"""
Edison-format AI Sidedecking Heuristic Engine for PROPHIT-AI
Autonomous sideboard swapping and strategic turn-order decision logic.
Evaluates opponent deck telemetry (attributes, races, backrow density)
and substitutes tactical counters while preserving strict Edison 2010 legality.
"""

import os
import time
import json
from typing import List, Dict, Any, Tuple, Optional
from loguru import logger

from edison_banlist import (
    check_edison_legality,
    get_card_limit,
    FORBIDDEN_IDS,
    LIMITED_IDS,
    SEMI_LIMITED_IDS
)

# Standard Edison 2010 Tournament Sideboard Staple Pool
# Priority tuple: (Card ID, Name, Category, Default Priority)
EDISON_SIDE_CANDIDATES = [
    (2980764, "Consecrated Light", "ANTI_DARK_FLOODGATE", 10),
    (99735427, "Shadow-Imprisoning Mirror", "ANTI_DARK_TRAP", 9),
    (53341729, "Light-Imprisoning Mirror", "ANTI_LIGHT_TRAP", 9),
    (18895832, "System Down", "ANTI_MACHINE_SPELL", 9),
    (5318639, "Mystical Space Typhoon", "BACKROW_REMOVAL_SPELL", 8),
    (60082869, "Dust Tornado", "BACKROW_REMOVAL_TRAP", 7),
    (59616123, "Trap Stun", "ANTI_TRAP", 7),
    (71044499, "Nobleman of Crossout", "ANTI_FLIP_SPELL", 6),
    (24508238, "D.D. Crow", "GY_BANISH_MONSTER", 7),
    (29401950, "Bottomless Trap Hole", "SUMMON_REMOVAL_TRAP", 6),
    (70342110, "Dimensional Prison", "ATTACK_BANISH_TRAP", 6),
    (34717238, "Pulling the Rug", "COUNTER_SUMMON_TRAP", 5),
    (70095154, "Cyber Dragon", "GOING_SECOND_BEATER", 8),
    (97169186, "Smashing Ground", "SPOT_REMOVAL_SPELL", 7),
    (36361633, "Threatening Roar", "BATTLE_STOPPER_TRAP", 6),
    # Fallback Staples
    (94192409, "Compulsory Evacuation Device", "GENERIC_BOUNCE_TRAP", 5),
    (14087893, "Book of Moon", "QUICK_FLIP_SPELL", 6),
    (51452091, "Royal Decree", "TRAP_LOCKOUT", 6),
]


DEFAULT_CUT_WEIGHTS = {
    "staple_protection": -50.0,
    "vanilla_low_atk": 16.0,
    "vanilla_beater": 11.0,
    "weak_effect_no_disruption": 14.0,
    "mediocre_stat_filler": 9.0,
    "tribute_low_atk": 13.0,
    "equip_spell": 10.0,
    "passive_trap_going_second": 8.0,
    "boss_monster": -25.0,
    "standard_card": 3.0
}


class AISideDeckEngine:
    def __init__(self, policy_file: Optional[str] = None):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.policy_file = policy_file or os.path.join(base_dir, "sidedeck_policy.json")
        self.policy_mtime = 0.0
        self.policy_weights: Dict[int, List[float]] = {}
        self.cut_weights: Dict[str, float] = dict(DEFAULT_CUT_WEIGHTS)
        self.policy_meta: Dict[str, Any] = {}
        self._check_reload_policy()

    def _check_reload_policy(self):
        """Dynamically reloads ML policy weights when sidedeck_policy.json changes on disk."""
        try:
            if not os.path.exists(self.policy_file):
                return
            mtime = os.path.getmtime(self.policy_file)
            if mtime > self.policy_mtime:
                with open(self.policy_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                weights_map = {}
                for cid_str, info in data.get("weights", {}).items():
                    try:
                        weights_map[int(cid_str)] = info.get("weights", [])
                    except ValueError:
                        continue
                self.policy_weights = weights_map
                if "cut_weights" in data and isinstance(data["cut_weights"], dict):
                    self.cut_weights.update(data["cut_weights"])
                self.policy_meta = {
                    "version": data.get("version", "1.0.0"),
                    "updated_at": data.get("updated_at", 0),
                    "real_episodes": data.get("real_episodes_count", 0),
                    "loss": data.get("final_loss", 0.0)
                }
                self.policy_mtime = mtime
                logger.info(f"[ML POLICY HOT-RELOAD] Loaded policy from {self.policy_file} ({len(self.policy_weights)} cards, {len(self.cut_weights)} cut-weights, real_episodes={self.policy_meta['real_episodes']}, loss={self.policy_meta['loss']})")
        except Exception as e:
            logger.warning(f"[ML POLICY HOT-RELOAD] Error reloading policy: {e}")


    def log_sidedeck_episode(
        self,
        room_id: str,
        session_id: str,
        game: int,
        opp_profile: Dict[str, Any],
        swaps: List[Dict[str, Any]],
        going_first: bool,
        ai_deck_name: str = "PROPHIT_Edison_LGNN",
        player_idx: Optional[int] = None,
        log_path: Optional[str] = None
    ):
        """Persists AI or human sideboard decision episode for RL policy training pipeline."""
        try:
            if not log_path:
                base_dir = os.path.dirname(os.path.abspath(__file__))
                log_path = os.path.join(base_dir, "telemetry", "sidedeck_episodes.jsonl")
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            record = {
                "type": "SIDEDECK_EPISODE",
                "timestamp": time.time(),
                "room_id": room_id,
                "session_id": session_id,
                "game": game,
                "ai_deck": ai_deck_name,
                "player_idx": player_idx,
                "going_first": going_first,
                "opp_profile": opp_profile,
                "swaps": swaps,
                "swap_count": len(swaps),
            }
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
            logger.info(f"[ML TELEMETRY] Logged Sidedeck Episode for {room_id} (Game {game}, {len(swaps)} swaps) -> {log_path}")
        except Exception as e:
            logger.warning(f"[ML TELEMETRY] Failed to log sidedeck episode: {e}")

    def log_match_outcome(
        self,
        room_id: str,
        session_id: str,
        winner: int,
        match_wins: Dict[int, int],
        is_pvp: bool = False,
        log_path: Optional[str] = None
    ):
        """Records match resolution with AI/player win/loss label for RL training credit assignment."""
        try:
            if not log_path:
                base_dir = os.path.dirname(os.path.abspath(__file__))
                log_path = os.path.join(base_dir, "telemetry", "sidedeck_episodes.jsonl")
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            record = {
                "type": "MATCH_OUTCOME",
                "timestamp": time.time(),
                "room_id": room_id,
                "session_id": session_id,
                "winner": winner,
                "ai_won": (winner == 1) if not is_pvp else None,
                "is_pvp": is_pvp,
                "match_wins": match_wins
            }
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
            logger.info(f"[ML TELEMETRY] Logged Match Outcome for {room_id} (Winner {winner}, is_pvp={is_pvp}) -> {log_path}")
        except Exception as e:
            logger.warning(f"[ML TELEMETRY] Failed to log match outcome: {e}")

    def get_card_max_copies(self, card_id: int) -> int:
        """Returns max legal copies of a card in Edison Format."""
        if card_id in FORBIDDEN_IDS:
            return 0
        if card_id in LIMITED_IDS:
            return 1
        if card_id in SEMI_LIMITED_IDS:
            return 2
        return 3

    def build_default_sideboard(self, main_deck: List[int], extra_deck: Optional[List[int]] = None) -> List[int]:
        """
        Builds a compliant 15-card Edison Side Deck tailored to complement main_deck.
        Ensures total copies across main + extra + side never exceed legal limits.
        """
        side_deck: List[int] = []
        combined_counts: Dict[int, int] = {}

        for cid in main_deck + (extra_deck or []):
            combined_counts[cid] = combined_counts.get(cid, 0) + 1

        for cid, name, category, prio in EDISON_SIDE_CANDIDATES:
            max_copies = self.get_card_max_copies(cid)
            current = combined_counts.get(cid, 0)
            if current < max_copies:
                side_deck.append(cid)
                combined_counts[cid] = current + 1
                if len(side_deck) == 15:
                    break

        # Fallback filler if still < 15
        fallback_fillers = [94192409, 36361633, 70342110, 97169186, 29401950]
        for cid in fallback_fillers:
            if len(side_deck) >= 15:
                break
            max_copies = self.get_card_max_copies(cid)
            current = combined_counts.get(cid, 0)
            if current < max_copies:
                side_deck.append(cid)
                combined_counts[cid] = current + 1

        return side_deck[:15]

    def analyze_opponent_profile(self, opp_main: List[int], card_metadata: Dict[int, Any]) -> Dict[str, Any]:
        """
        Extracts strategic signals from opponent main deck composition.
        Detects primary attributes, races, spell/trap density, and threat levels.
        """
        dark_count = 0
        light_count = 0
        machine_count = 0
        spell_count = 0
        trap_count = 0
        high_atk_count = 0
        flip_count = 0
        monster_count = 0

        for cid in opp_main:
            meta = card_metadata.get(cid, {})
            ctype = meta.get("type", 0)
            attr = meta.get("attribute", 0)
            race = meta.get("race", 0)
            atk = meta.get("atk", 0)
            desc = (meta.get("desc") or "").lower()

            if ctype & 0x1: # Monster
                monster_count += 1
                if attr == 0x20: # DARK
                    dark_count += 1
                elif attr == 0x10: # LIGHT
                    light_count += 1

                if race == 0x20: # Machine
                    machine_count += 1

                if atk >= 2400:
                    high_atk_count += 1

                if "flip:" in desc or "flip :" in desc or (ctype & 0x200000):
                    flip_count += 1
            elif ctype & 0x2: # Spell
                spell_count += 1
            elif ctype & 0x4: # Trap
                trap_count += 1

        profile = {
            "total_cards": len(opp_main),
            "monster_count": monster_count,
            "spell_count": spell_count,
            "trap_count": trap_count,
            "spell_trap_count": spell_count + trap_count,
            "dark_count": dark_count,
            "light_count": light_count,
            "machine_count": machine_count,
            "high_atk_count": high_atk_count,
            "flip_count": flip_count,
        }
        return profile

    def score_side_card(self, card_id: int, opp_profile: Dict[str, Any], going_first: bool) -> Tuple[float, str]:
        """
        Computes the strategic value of bringing card_id into the main deck
        against opp_profile under the given turn order.
        Evaluates dynamic ML policy weights if loaded, with fallback to rule heuristics.
        Returns (score, rationale).
        """
        self._check_reload_policy()

        if card_id in self.policy_weights:
            w = self.policy_weights[card_id]
            total = max(float(opp_profile.get("total_cards", 40)), 1.0)
            x = [
                float(opp_profile.get("dark_count", 0)) / total,
                float(opp_profile.get("light_count", 0)) / total,
                float(opp_profile.get("machine_count", 0)) / total,
                float(opp_profile.get("trap_count", 0)) / total,
                float(opp_profile.get("spell_trap_count", opp_profile.get("trap_count", 0) + opp_profile.get("spell_count", 0))) / total,
                float(opp_profile.get("high_atk_count", 0)) / total,
                float(opp_profile.get("flip_count", 0)) / total,
                1.0 if going_first else 0.0,
                1.0 # bias
            ]
            score = sum(wi * xi for wi, xi in zip(w, x))
            ver = self.policy_meta.get("version", "1.0.0")
            loss = self.policy_meta.get("loss", 0.0)
            return (round(float(score), 2), f"ML Policy v{ver} (score: {score:.1f}, loss: {loss:.4f})")

        # Fallback to hand-crafted Edison heuristics
        # Consecrated Light & Shadow-Imprisoning Mirror (Anti-DARK)
        if card_id == 2980764: # Consecrated Light
            if opp_profile.get("dark_count", 0) >= 5:
                return (18.0, f"Opponent plays {opp_profile['dark_count']} DARK monsters; Consecrated Light locks summons.")
            if opp_profile.get("dark_count", 0) >= 3:
                return (8.0, "Moderate DARK presence detected.")
            return (-5.0, "Low DARK presence.")

        if card_id == 99735427: # Shadow-Imprisoning Mirror
            if opp_profile.get("dark_count", 0) >= 5:
                return (16.0, f"Opponent plays {opp_profile['dark_count']} DARK monsters; negates all activations.")
            if opp_profile.get("dark_count", 0) >= 3:
                return (7.0, "Moderate DARK presence detected.")
            return (-5.0, "Low DARK presence.")

        # Light-Imprisoning Mirror (Anti-LIGHT)
        if card_id == 53341729: # Light-Imprisoning Mirror
            if opp_profile.get("light_count", 0) >= 5:
                return (17.0, f"Opponent plays {opp_profile['light_count']} LIGHT monsters (e.g. Lightsworn/Honest).")
            if opp_profile.get("light_count", 0) >= 3:
                return (7.0, "Moderate LIGHT presence detected.")
            return (-5.0, "Low LIGHT presence.")

        # System Down (Anti-Machine)
        if card_id == 18895832: # System Down
            if opp_profile.get("machine_count", 0) >= 3:
                return (20.0, f"Opponent plays {opp_profile['machine_count']} Machine monsters (Machina/Cyber Dragon); total wipe.")
            return (-8.0, "No Machine threat.")

        # Mystical Space Typhoon / Dust Tornado / Trap Stun (Backrow Control)
        if card_id in (5318639, 60082869): # MST / Dust Tornado
            st_count = opp_profile.get("spell_trap_count", 0)
            trap_count = opp_profile.get("trap_count", 0)
            if trap_count >= 8 or st_count >= 16:
                return (12.0, f"Heavy opponent backrow ({trap_count} traps, {st_count} S/T).")
            return (4.0, "Standard S/T removal utility.")

        if card_id == 59616123: # Trap Stun
            trap_count = opp_profile.get("trap_count", 0)
            if trap_count >= 8:
                return (11.0, f"Opponent plays {trap_count} traps; Trap Stun secures OTK/push turn.")
            return (2.0, "Low trap presence.")

        # Nobleman of Crossout (Anti-Flip / Defense)
        if card_id == 71044499: # Nobleman of Crossout
            if opp_profile.get("flip_count", 0) >= 3:
                return (13.0, f"Opponent plays {opp_profile['flip_count']} flip/defense monsters (Ryko/Hamster/Spy).")
            return (1.0, "Low flip monster presence.")

        # D.D. Crow (GY Disruption)
        if card_id == 24508238: # D.D. Crow
            dark_c = opp_profile.get("dark_count", 0)
            light_c = opp_profile.get("light_count", 0)
            if dark_c >= 4 or light_c >= 4:
                return (9.0, "Graveyard disruption against Chaos / Vayu / Lumina plays.")
            return (3.0, "Generic GY banish.")

        # Cyber Dragon (Going Second Powerhouse)
        if card_id == 70095154: # Cyber Dragon
            mach_c = opp_profile.get("machine_count", 0)
            if not going_first:
                return (14.0, "Going second: Free 2100 ATK special summon to break opponent board.")
            if mach_c >= 2:
                return (12.0, "Machine matchup: Threatens Chimeratech Fortress Dragon contact fusion.")
            return (3.0, "Suboptimal when going first.")

        # Smashing Ground (Monster Removal)
        if card_id == 97169186: # Smashing Ground
            if not going_first or opp_profile.get("high_atk_count", 0) >= 4:
                return (10.0, "Spot removal against high-DEF or established boss monsters.")
            return (5.0, "Generic removal.")

        # Threatening Roar / Dimensional Prison / Bottomless (Going First Traps)
        if card_id in (36361633, 70342110, 29401950):
            if going_first:
                return (9.0, "Going first: Defensive trap to set on Turn 1 and halt battle/summons.")
            if opp_profile.get("high_atk_count", 0) >= 4:
                return (8.0, "Defensive answers to high-ATK beatdown threats.")
            return (4.0, "Standard defensive trap.")

        # Generic Default
        return (1.0, "Standard side card.")

    def score_main_card_for_removal(self, card_id: int, card_metadata: Dict[int, Any], opp_profile: Dict[str, Any], going_first: bool) -> Tuple[float, str]:
        """
        Evaluates how expendable card_id in AI current main deck is.
        Higher score = more desirable to CUT and substitute from the sideboard.
        Returns (cut_score, rationale).
        """
        meta = card_metadata.get(card_id, {})
        ctype = meta.get("type", 0)
        atk = meta.get("atk", 0)
        level = meta.get("level", 0)
        desc = (meta.get("desc") or "").lower()

        # NEVER cut core power staples
        untouchable_staples = {
            5318639: "Mystical Space Typhoon",
            79814787: "Heavy Storm",
            44095762: "Mirror Force",
            53582587: "Torrential Tribute",
            14087893: "Book of Moon",
            40605147: "Solemn Judgment",
            10443957: "Brain Control",
        }
        if card_id in untouchable_staples:
            staple_weight = self.cut_weights.get("staple_protection", -50.0)
            return (staple_weight, f"Core limited power staple ({untouchable_staples[card_id]}) must never be cut.")

        # Normal non-effect vanilla monsters with low ATK (< 1800)
        if (ctype & 0x1) and not (ctype & 0x20):
            if atk < 1800:
                score = self.cut_weights.get("vanilla_low_atk", 16.0)
                return (score, f"Vanilla normal monster with low stats ({atk} ATK).")
            score = self.cut_weights.get("vanilla_beater", 11.0)
            return (score, f"Vanilla normal beater ({atk} ATK).")

        # Weak low-impact effect monsters (Level <= 4, ATK < 1500, no destroy/negate/search)
        if (ctype & 0x1) and (ctype & 0x20) and level <= 4:
            has_utility = ("destroy" in desc or "negate" in desc or "draw" in desc or "search" in desc or "special summon" in desc)
            if atk < 1500 and not has_utility:
                score = self.cut_weights.get("weak_effect_no_disruption", 14.0)
                return (score, f"Low stat utility monster without disruption ({atk} ATK).")
            if atk < 1700 and not has_utility:
                score = self.cut_weights.get("mediocre_stat_filler", 9.0)
                return (score, f"Mediocre stat filler monster ({atk} ATK).")

        # High-level tribute monsters with low ATK (Level >= 5, ATK < 2400)
        if (ctype & 0x1) and level >= 5 and atk < 2400:
            score = self.cut_weights.get("tribute_low_atk", 13.0)
            return (score, f"Tribute monster with substandard stats (Lvl {level}, {atk} ATK).")

        # Situational spells/traps in wrong matchup
        if ctype & 0x2: # Spell
            if "equip" in desc:
                score = self.cut_weights.get("equip_spell", 10.0)
                return (score, "Equip spell prone to 2-for-1 card disadvantage.")
        if ctype & 0x4: # Trap
            if not going_first and ("counter" not in desc and "destroy" not in desc):
                score = self.cut_weights.get("passive_trap_going_second", 8.0)
                return (score, "Passive trap when going second.")

        # High-power boss monsters (ATK >= 2800)
        if (ctype & 0x1) and atk >= 2800:
            score = self.cut_weights.get("boss_monster", -25.0)
            return (score, f"Primary deck boss monster ({atk} ATK).")

        std_score = self.cut_weights.get("standard_card", 3.0)
        return (std_score, "Standard playable card.")


    def execute_sidedeck(
        self,
        ai_main: List[int],
        ai_extra: List[int],
        ai_side: List[int],
        opp_main: List[int],
        card_metadata: Dict[int, Any],
        going_first: bool,
        max_swaps: int = 3
    ) -> Tuple[List[int], List[int], List[int], List[Dict[str, Any]]]:
        """
        Executes autonomous 1-for-1 card swaps between ai_main and ai_side.
        Guarantees exact card count preservation and Edison 2010 legality.
        """
        current_side = list(ai_side)
        if len(current_side) < 15:
            current_side = self.build_default_sideboard(ai_main, ai_extra)

        opp_profile = self.analyze_opponent_profile(opp_main, card_metadata)
        logger.info(f"[AI SIDEDECK] Opponent Profile: DARK={opp_profile['dark_count']}, LIGHT={opp_profile['light_count']}, MACH={opp_profile['machine_count']}, TRAP={opp_profile['trap_count']}, HIGH_ATK={opp_profile['high_atk_count']} | AI Going First: {going_first}")

        # Score side cards for entry into main
        side_scored: List[Tuple[float, int, str]] = []
        for cid in current_side:
            score, rationale = self.score_side_card(cid, opp_profile, going_first)
            side_scored.append((score, cid, rationale))
        # Sort descending by score
        side_scored.sort(key=lambda x: x[0], reverse=True)

        # Score main cards for removal
        main_scored: List[Tuple[float, int, str]] = []
        for cid in ai_main:
            cut_score, rationale = self.score_main_card_for_removal(cid, card_metadata, opp_profile, going_first)
            main_scored.append((cut_score, cid, rationale))
        # Sort descending by cut score
        main_scored.sort(key=lambda x: x[0], reverse=True)

        working_main = list(ai_main)
        working_side = list(current_side)
        swaps_performed: List[Dict[str, Any]] = []

        # Greedily match best side additions with worst main cuts
        for side_score, side_cid, side_reason in side_scored:
            if len(swaps_performed) >= max_swaps:
                break
            if side_score < 6.0: # Minimum threshold to justify a sideboard swap
                break

            # Find best legal main card to cut
            for idx, (cut_score, main_cid, cut_reason) in enumerate(main_scored):
                if main_cid not in working_main:
                    continue
                if cut_score <= 0.0: # Never cut protected cards
                    continue

                cand_main = list(working_main)
                cand_side = list(working_side)

                cand_main.remove(main_cid)
                cand_main.append(side_cid)

                cand_side.remove(side_cid)
                cand_side.append(main_cid)

                # Verify Edison legality
                legality = check_edison_legality(cand_main, ai_extra, cand_side)
                if legality["is_legal"]:
                    working_main = cand_main
                    working_side = cand_side

                    side_meta = card_metadata.get(side_cid, {})
                    _, _, side_known = get_card_limit(side_cid)
                    side_name = side_meta.get("name") or side_known or f"Card {side_cid}"

                    cut_meta = card_metadata.get(main_cid, {})
                    _, _, cut_known = get_card_limit(main_cid)
                    cut_name = cut_meta.get("name") or cut_known or f"Card {main_cid}"

                    swap_record = {
                        "in_id": side_cid,
                        "in_name": side_name,
                        "in_score": side_score,
                        "out_id": main_cid,
                        "out_name": cut_name,
                        "out_score": cut_score,
                        "reason": f"IN: {side_reason} | OUT: {cut_reason}"
                    }
                    swaps_performed.append(swap_record)
                    logger.info(f"[AI SIDEDECK SWAP #{len(swaps_performed)}] IN: {side_name} ({side_cid}) <=> OUT: {cut_name} ({main_cid}) | {swap_record['reason']}")
                    break

        return working_main, ai_extra, working_side, swaps_performed
