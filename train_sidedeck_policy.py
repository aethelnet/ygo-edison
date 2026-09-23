#!/usr/bin/env python3
"""
Offline ML Policy Trainer for PROPHIT-AI Sidedecking Engine (Phase 87)
Ingests duel telemetry (telemetry/sidedeck_episodes.jsonl) and optimizes
card scoring parameters using Anchor-Weight Regularization (lambda * ||theta - theta_0||^2)
to guarantee stability on sparse tournament datasets.
"""

import os
import sys
import json
import time
import argparse
from typing import List, Dict, Any, Tuple, Optional
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

FEATURE_NAMES = [
    "dark_ratio",
    "light_ratio",
    "machine_ratio",
    "trap_density",
    "spell_trap_density",
    "high_atk_ratio",
    "flip_ratio",
    "going_first",
    "bias"
]

CANDIDATES = [
    (2980764, "Consecrated Light", [90.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, -4.0]),
    (99735427, "Shadow-Imprisoning Mirror", [85.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 2.0, -4.0]),
    (53341729, "Light-Imprisoning Mirror", [0.0, 85.0, 0.0, 0.0, 0.0, 0.0, 0.0, 2.0, -4.0]),
    (18895832, "System Down", [0.0, 0.0, 140.0, 0.0, 0.0, 0.0, 0.0, -2.0, -8.0]),
    (5318639, "Mystical Space Typhoon", [0.0, 0.0, 0.0, 25.0, 15.0, 0.0, 0.0, 1.0, 3.0]),
    (60082869, "Dust Tornado", [0.0, 0.0, 0.0, 20.0, 12.0, 0.0, 0.0, 2.0, 2.0]),
    (59616123, "Trap Stun", [0.0, 0.0, 0.0, 35.0, 10.0, 0.0, 0.0, -1.0, 1.0]),
    (71044499, "Nobleman of Crossout", [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 80.0, -1.0, 1.0]),
    (24508238, "D.D. Crow", [20.0, 20.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 3.0]),
    (29401950, "Bottomless Trap Hole", [0.0, 0.0, 0.0, 0.0, 0.0, 15.0, 0.0, 5.0, 4.0]),
    (70342110, "Dimensional Prison", [0.0, 0.0, 0.0, 0.0, 0.0, 15.0, 0.0, 5.0, 4.0]),
    (34717238, "Pulling the Rug", [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 4.0, 3.0]),
    (70095154, "Cyber Dragon", [0.0, 0.0, 40.0, 0.0, 0.0, 0.0, 0.0, -8.0, 12.0]),
    (97169186, "Smashing Ground", [0.0, 0.0, 0.0, 0.0, 0.0, 25.0, 0.0, -4.0, 8.0]),
    (36361633, "Threatening Roar", [0.0, 0.0, 0.0, 0.0, 0.0, 15.0, 0.0, 5.0, 4.0]),
    (94192409, "Compulsory Evacuation Device", [0.0, 0.0, 0.0, 0.0, 0.0, 10.0, 0.0, 3.0, 4.0]),
    (14087893, "Book of Moon", [0.0, 0.0, 0.0, 0.0, 0.0, 10.0, 0.0, 2.0, 6.0]),
    (51452091, "Royal Decree", [0.0, 0.0, 0.0, 45.0, 15.0, 0.0, 0.0, 3.0, -2.0]),
]

CID_TO_INDEX = {c[0]: i for i, c in enumerate(CANDIDATES)}
INDEX_TO_CID = {i: c[0] for i, c in enumerate(CANDIDATES)}
INDEX_TO_NAME = {i: c[1] for i, c in enumerate(CANDIDATES)}

def build_prior_weights() -> np.ndarray:
    """Constructs baseline theta_0 from Edison 2010 heuristic specifications."""
    w = np.zeros((len(CANDIDATES), len(FEATURE_NAMES)), dtype=np.float32)
    for i, c in enumerate(CANDIDATES):
        w[i] = np.array(c[2], dtype=np.float32)
    return w

def extract_features(opp_profile: Dict[str, Any], going_first: bool) -> np.ndarray:
    """Transforms raw deck analysis profile into normalized 9-dim feature vector."""
    total = max(float(opp_profile.get("total_cards", 40)), 1.0)
    dark_r = float(opp_profile.get("dark_count", 0)) / total
    light_r = float(opp_profile.get("light_count", 0)) / total
    mach_r = float(opp_profile.get("machine_count", 0)) / total
    trap_d = float(opp_profile.get("trap_count", 0)) / total
    st_d = float(opp_profile.get("spell_trap_count", opp_profile.get("trap_count", 0) + opp_profile.get("spell_count", 0))) / total
    high_atk_r = float(opp_profile.get("high_atk_count", 0)) / total
    flip_r = float(opp_profile.get("flip_count", 0)) / total
    gf = 1.0 if going_first else 0.0
    bias = 1.0

    return np.array([
        dark_r, light_r, mach_r, trap_d, st_d, high_atk_r, flip_r, gf, bias
    ], dtype=np.float32)

def generate_synthetic_archetype_episodes(count: int = 40) -> List[Dict[str, Any]]:
    """
    Generates synthetic canonical Edison archetype episodes to stabilize
    gradient updates when empirical tournament dataset is sparse (<50 episodes).
    """
    archetypes = [
        {
            "name": "Blackwing",
            "base_profile": {"total_cards": 40, "dark_count": 14, "light_count": 0, "machine_count": 0, "trap_count": 10, "spell_trap_count": 20, "high_atk_count": 2, "flip_count": 0},
            "favored_in": [2980764, 99735427, 59616123], # Consecrated Light, Shadow Mirror, Trap Stun
        },
        {
            "name": "Machina Gadget",
            "base_profile": {"total_cards": 40, "dark_count": 0, "light_count": 0, "machine_count": 12, "trap_count": 14, "spell_trap_count": 24, "high_atk_count": 4, "flip_count": 0},
            "favored_in": [18895832, 70095154, 5318639], # System Down, Cyber Dragon, MST
        },
        {
            "name": "Lightsworn",
            "base_profile": {"total_cards": 40, "dark_count": 2, "light_count": 18, "machine_count": 0, "trap_count": 2, "spell_trap_count": 15, "high_atk_count": 6, "flip_count": 3},
            "favored_in": [53341729, 24508238, 71044499], # Light Mirror, D.D. Crow, Nobleman
        },
        {
            "name": "Gladiator Beast Trap-Heavy",
            "base_profile": {"total_cards": 40, "dark_count": 2, "light_count": 2, "machine_count": 0, "trap_count": 16, "spell_trap_count": 26, "high_atk_count": 2, "flip_count": 0},
            "favored_in": [51452091, 59616123, 5318639, 60082869], # Royal Decree, Trap Stun, MST, Dust Tornado
        },
        {
            "name": "Debris Plant",
            "base_profile": {"total_cards": 40, "dark_count": 2, "light_count": 4, "machine_count": 2, "trap_count": 6, "spell_trap_count": 18, "high_atk_count": 3, "flip_count": 4},
            "favored_in": [71044499, 24508238, 29401950], # Nobleman, D.D. Crow, Bottomless
        }
    ]

    episodes = []
    rng = np.random.RandomState(42)

    for i in range(count):
        arch = archetypes[i % len(archetypes)]
        prof = dict(arch["base_profile"])
        # Add slight stochastic noise to avoid overfitting on single discrete points
        noise_dark = max(0, prof["dark_count"] + rng.randint(-1, 2))
        noise_light = max(0, prof["light_count"] + rng.randint(-1, 2))
        noise_mach = max(0, prof["machine_count"] + rng.randint(-1, 2))
        noise_trap = max(0, prof["trap_count"] + rng.randint(-1, 2))
        noise_flip = max(0, prof["flip_count"] + rng.randint(-1, 2))
        prof["dark_count"] = noise_dark
        prof["light_count"] = noise_light
        prof["machine_count"] = noise_mach
        prof["trap_count"] = noise_trap
        prof["flip_count"] = noise_flip
        prof["spell_trap_count"] = prof["trap_count"] + 10

        going_first = bool(rng.choice([True, False]))
        in_cids = list(arch["favored_in"])
        # Cyber dragon especially favored going second
        if not going_first and 70095154 not in in_cids:
            in_cids.append(70095154)

        episodes.append({
            "opp_profile": prof,
            "going_first": going_first,
            "in_cids": in_cids,
            "target": +1.0, # Target positive reinforcement for canonical counter choices
            "is_synthetic": True
        })

    return episodes

def parse_telemetry_episodes(log_path: str) -> List[Dict[str, Any]]:
    """
    Parses telemetry/sidedeck_episodes.jsonl, correlating SIDEDECK_EPISODE
    events with subsequent MATCH_OUTCOME to establish outcome targets (+1/-1).
    """
    if not os.path.exists(log_path):
        return []

    episodes_by_key: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    outcomes_by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}

    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                rtype = rec.get("type")
                room_id = rec.get("room_id", "")
                session_id = rec.get("session_id", "")
                key = (room_id, session_id)

                if rtype == "SIDEDECK_EPISODE":
                    if key not in episodes_by_key:
                        episodes_by_key[key] = []
                    episodes_by_key[key].append(rec)
                elif rtype == "MATCH_OUTCOME":
                    outcomes_by_key[key] = rec
            except Exception:
                continue

    parsed_data = []
    for key, ep_list in episodes_by_key.items():
        outcome = outcomes_by_key.get(key)
        for ep in ep_list:
            swaps = ep.get("swaps", [])
            in_cids = [s["in_id"] for s in swaps if "in_id" in s]
            if not in_cids:
                continue

            target = +1.0
            if outcome:
                # If human player recorded in PvP
                player_idx = ep.get("player_idx")
                if player_idx is not None:
                    target = +1.0 if outcome.get("winner") == player_idx else -1.0
                else:
                    # AI duel outcome
                    target = +1.0 if outcome.get("ai_won", True) else -1.0

            parsed_data.append({
                "opp_profile": ep.get("opp_profile", {}),
                "going_first": ep.get("going_first", False),
                "in_cids": in_cids,
                "target": target,
                "is_synthetic": False
            })

    return parsed_data

def train_sidedeck_model(
    episodes_path: str,
    output_path: str,
    epochs: int = 50,
    lr: float = 0.01,
    lambda_anchor: float = 0.5,
    min_real_episodes_threshold: int = 50
) -> Dict[str, Any]:
    """
    Executes PyTorch training loop with Anchor-Weight Regularization.
    Exports optimized weight matrices to sidedeck_policy.json.
    """
    prior_np = build_prior_weights()
    K, D = prior_np.shape

    # 1. Parse empirical telemetry
    real_episodes = parse_telemetry_episodes(episodes_path)
    real_count = len(real_episodes)

    # 2. Synthetic Archetype Injection if real dataset is sparse
    synthetic_count = 0
    all_episodes = list(real_episodes)
    if real_count < min_real_episodes_threshold:
        needed = max(25, min_real_episodes_threshold - real_count)
        synthetic_episodes = generate_synthetic_archetype_episodes(count=needed)
        synthetic_count = len(synthetic_episodes)
        all_episodes.extend(synthetic_episodes)

    # 3. Build training dataset tensors
    train_samples = []
    for ep in all_episodes:
        feat = extract_features(ep["opp_profile"], ep["going_first"])
        in_cids = set(ep["in_cids"])
        chosen_indices = [CID_TO_INDEX[c] for c in in_cids if c in CID_TO_INDEX]
        if not chosen_indices:
            continue
        not_chosen_indices = [i for i in range(K) if i not in chosen_indices]
        train_samples.append({
            "feat": torch.tensor(feat, dtype=torch.float32),
            "chosen": chosen_indices,
            "not_chosen": not_chosen_indices,
            "target": float(ep["target"])
        })

    if not train_samples:
        raise ValueError("[ML TRAIN] No valid training samples extracted.")

    # 4. PyTorch Model Parameters initialized with theta_0
    theta_0 = torch.tensor(prior_np, dtype=torch.float32)
    theta = nn.Parameter(theta_0.clone())
    optimizer = torch.optim.AdamW([theta], lr=lr, weight_decay=1e-4)

    # 5. Training Loop
    final_loss = 0.0
    final_anchor_loss = 0.0
    for epoch in range(epochs):
        optimizer.zero_grad()
        data_losses = []

        for sample in train_samples:
            x = sample["feat"] # (D,)
            scores = torch.mv(theta, x) # (K,)
            y = sample["target"] # +1.0 or -1.0

            # Compute pairwise rank margin between chosen and not chosen candidates
            chosen_scores = scores[sample["chosen"]] # (M,)
            not_chosen_scores = scores[sample["not_chosen"]] # (K-M,)

            # Outer difference: chosen_scores[:, None] - not_chosen_scores[None, :]
            diff = chosen_scores.unsqueeze(1) - not_chosen_scores.unsqueeze(0) # (M, K-M)
            # Loss: log(1 + exp(-y * diff))
            sample_loss = F.softplus(-y * diff).mean()
            data_losses.append(sample_loss)

        data_loss = torch.stack(data_losses).mean()

        # Anchor Regularization: 0.5 * lambda * ||theta - theta_0||_F^2
        anchor_loss = 0.5 * lambda_anchor * torch.sum((theta - theta_0) ** 2)
        total_loss = data_loss + anchor_loss

        total_loss.backward()
        optimizer.step()

        final_loss = float(total_loss.item())
        final_anchor_loss = float(anchor_loss.item())

    # 6. Serialized Export
    trained_weights_np = theta.detach().cpu().numpy()
    export_dict: Dict[str, Any] = {
        "version": "1.0.0",
        "updated_at": time.time(),
        "real_episodes_count": real_count,
        "synthetic_episodes_count": synthetic_count,
        "epochs": epochs,
        "learning_rate": lr,
        "lambda_anchor": lambda_anchor,
        "final_loss": round(final_loss, 6),
        "anchor_loss": round(final_anchor_loss, 6),
        "feature_names": FEATURE_NAMES,

        "weights": {},
        "cut_weights": {
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
    }


    for i in range(K):
        cid = INDEX_TO_CID[i]
        name = INDEX_TO_NAME[i]
        w_list = [round(float(v), 4) for v in trained_weights_np[i]]
        export_dict["weights"][str(cid)] = {
            "name": name,
            "weights": w_list
        }

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(export_dict, f, indent=2)

    return export_dict

def main():
    parser = argparse.ArgumentParser(description="PROPHIT-AI Sidedeck Policy Trainer")
    parser.add_argument("--episodes-path", type=str, default="telemetry/sidedeck_episodes.jsonl",
                        help="Path to telemetry jsonl file")
    parser.add_argument("--output-path", type=str, default="sidedeck_policy.json",
                        help="Path to export policy JSON")
    parser.add_argument("--epochs", type=int, default=50, help="Number of training epochs")
    parser.add_argument("--lr", type=float, default=0.01, help="Learning rate")
    parser.add_argument("--lambda-anchor", type=float, default=0.5,
                        help="Anchor regularization coefficient (theta_0 penalty)")

    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    if os.path.isabs(args.episodes_path):
        ep_path = args.episodes_path
    elif os.path.exists(os.path.abspath(args.episodes_path)):
        ep_path = os.path.abspath(args.episodes_path)
    else:
        ep_path = os.path.join(base_dir, args.episodes_path)

    out_path = os.path.abspath(args.output_path)

    print(f"[ML TRAIN] Starting policy training...")
    print(f"[ML TRAIN] Episodes source: {ep_path}")
    print(f"[ML TRAIN] Output path: {out_path}")

    res = train_sidedeck_model(
        episodes_path=ep_path,
        output_path=out_path,
        epochs=args.epochs,
        lr=args.lr,
        lambda_anchor=args.lambda_anchor
    )

    print(f"[ML TRAIN] Training Complete!")
    print(f"  Real Episodes:      {res['real_episodes_count']}")
    print(f"  Synthetic Episodes: {res['synthetic_episodes_count']}")
    print(f"  Final Loss:         {res['final_loss']}")
    print(f"  Anchor Penalty:     {res['anchor_loss']}")
    print(f"  Exported To:        {out_path}")

if __name__ == "__main__":
    main()
