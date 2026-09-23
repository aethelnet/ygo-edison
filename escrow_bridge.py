"""
Phase 95: EdisonStoreEscrow Python Bridge & EIP-712 Attestation Engine
========================================================================
Architectural Invariant:
This module is strictly decoupled from the core duel and SQLite engine.
By default, ESCROW_MODE is "OFF_CHAIN_SQLITE" (100% offline & wallet-free).
Opt-in mode "BASE_SEPOLIA_ESCROW" provides cryptographic referee signatures
and on-chain settlement for Base Sepolia prize pools.
"""

import os
import time
import secrets
from typing import List, Dict, Any, Optional, Tuple
from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_abi import encode
from web3 import Web3


ESCROW_MODE = os.getenv("YGO_ESCROW_MODE", "OFF_CHAIN_SQLITE")
DEFAULT_BASE_SEPOLIA_CHAIN_ID = 84532


class EscrowBridge:
    def __init__(
        self,
        contract_address: Optional[str] = None,
        referee_private_key: Optional[str] = None,
        chain_id: int = DEFAULT_BASE_SEPOLIA_CHAIN_ID,
        rpc_url: Optional[str] = None
    ):
        self.mode = os.getenv("YGO_ESCROW_MODE", "OFF_CHAIN_SQLITE")
        self.contract_address = contract_address or os.getenv("YGO_ESCROW_CONTRACT_ADDRESS", "0x0000000000000000000000000000000000000000")
        self.chain_id = chain_id
        self.rpc_url = rpc_url or os.getenv("BASE_SEPOLIA_RPC_URL", "https://sepolia.base.org")
        
        # Load referee signing key if available
        priv_key = referee_private_key or os.getenv("ESCROW_REFEREE_PRIVATE_KEY")
        if priv_key:
            if not priv_key.startswith("0x"):
                priv_key = "0x" + priv_key
            self.referee_account = Account.from_key(priv_key)
            self.referee_address = self.referee_account.address
        else:
            self.referee_account = None
            self.referee_address = None

    def is_onchain_enabled(self) -> bool:
        """Returns True only if explicitly switched to BASE_SEPOLIA_ESCROW."""
        return self.mode == "BASE_SEPOLIA_ESCROW"

    def compute_standings_hash(
        self,
        tournament_id: bytes,
        winners: List[str],
        shares_bps: List[int]
    ) -> bytes:
        """
        Computes keccak256(abi.encode(tournamentId, winners, sharesBps))
        matching the Solidity contract's computation.
        """
        checksummed_winners = [Web3.to_checksum_address(w) for w in winners]
        encoded = encode(
            ["bytes32", "address[]", "uint256[]"],
            [tournament_id, checksummed_winners, shares_bps]
        )
        return Web3.keccak(encoded)

    def sign_tournament_resolution(
        self,
        tournament_id: bytes,
        winners: List[str],
        shares_bps: List[int],
        nonce: Optional[int] = None
    ) -> Tuple[bytes, bytes, int]:
        """
        Produces an EIP-712 signature from the referee for tournament prize distribution.
        Returns: (standings_hash, signature_bytes, nonce)
        """
        if not self.referee_account:
            raise ValueError("Referee private key not configured for EscrowBridge.")

        if nonce is None:
            nonce = int(time.time() * 1000) + secrets.randbelow(1000)

        standings_hash = self.compute_standings_hash(tournament_id, winners, shares_bps)
        checksum_contract = Web3.to_checksum_address(self.contract_address)

        typed_data = {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"}
                ],
                "TournamentResolution": [
                    {"name": "tournamentId", "type": "bytes32"},
                    {"name": "standingsHash", "type": "bytes32"},
                    {"name": "nonce", "type": "uint256"}
                ]
            },
            "primaryType": "TournamentResolution",
            "domain": {
                "name": "EdisonStoreEscrow",
                "version": "1.0",
                "chainId": self.chain_id,
                "verifyingContract": checksum_contract
            },
            "message": {
                "tournamentId": tournament_id,
                "standingsHash": standings_hash,
                "nonce": nonce
            }
        }

        signable = encode_typed_data(full_message=typed_data)
        signed = self.referee_account.sign_message(signable)
        return standings_hash, signed.signature, nonce

    def sign_wager_resolution(
        self,
        match_id: bytes,
        winner_address: str,
        nonce: Optional[int] = None
    ) -> Tuple[bytes, int]:
        """
        Produces an EIP-712 signature from the referee for 1v1 wager settlement.
        Returns: (signature_bytes, nonce)
        """
        if not self.referee_account:
            raise ValueError("Referee private key not configured for EscrowBridge.")

        if nonce is None:
            nonce = int(time.time() * 1000) + secrets.randbelow(1000)

        checksum_winner = Web3.to_checksum_address(winner_address)
        checksum_contract = Web3.to_checksum_address(self.contract_address)

        typed_data = {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"}
                ],
                "WagerResolution": [
                    {"name": "matchId", "type": "bytes32"},
                    {"name": "winner", "type": "address"},
                    {"name": "nonce", "type": "uint256"}
                ]
            },
            "primaryType": "WagerResolution",
            "domain": {
                "name": "EdisonStoreEscrow",
                "version": "1.0",
                "chainId": self.chain_id,
                "verifyingContract": checksum_contract
            },
            "message": {
                "matchId": match_id,
                "winner": checksum_winner,
                "nonce": nonce
            }
        }

        signable = encode_typed_data(full_message=typed_data)
        signed = self.referee_account.sign_message(signable)
        return signed.signature, nonce
