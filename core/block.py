"""
Block model: creation, hashing, and validation helpers.
"""

import json
import hashlib

from core.transaction import tx_to_dict

# ── Constants ─────────────────────────────────────────────────────────────────

GENESIS_PREVIOUS_HASH = "0" * 64


# ── Hashing ───────────────────────────────────────────────────────────────────

def _canonical_json(obj: dict) -> str:
    """
    Produce the canonical JSON string used for hashing.
    Matches the reference hashing script exactly:
        json.dumps(obj, sort_keys=True, indent=2, separators=(',', ': '))
    """
    return json.dumps(obj, sort_keys=True, indent=2, separators=(',', ': '))


def compute_block_hash(index: int, transactions: list, previous_hash: str) -> str:
    """
    Compute SHA-256 of the block content (everything except current_hash).
    """
    content = {
        "index":         index,
        "previous_hash": previous_hash,
        "transactions":  transactions,
    }
    canonical = _canonical_json(content)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── Block creation ────────────────────────────────────────────────────────────

def make_genesis_block() -> dict:
    """Build and return the genesis block (index=1, empty transactions)."""
    transactions  = []
    previous_hash = GENESIS_PREVIOUS_HASH
    current_hash  = compute_block_hash(1, transactions, previous_hash)
    return {
        "index":         1,
        "transactions":  transactions,
        "previous_hash": previous_hash,
        "current_hash":  current_hash,
    }


def make_block(index: int, transactions: list, previous_hash: str) -> dict:
    """
    Create a new block dict.

    :param index:         Position in the chain.
    :param transactions:  List of validated transaction dicts.
    :param previous_hash: current_hash of the preceding block.
    :return:              Complete block dict with current_hash filled in.
    """
    clean_txs    = [tx_to_dict(tx) for tx in transactions]
    current_hash = compute_block_hash(index, clean_txs, previous_hash)
    return {
        "index":         index,
        "transactions":  clean_txs,
        "previous_hash": previous_hash,
        "current_hash":  current_hash,
    }


# ── Pretty printing ───────────────────────────────────────────────────────────

def block_to_json_str(block: dict) -> str:
    """Return the pretty-printed JSON string for stdout logging."""
    return json.dumps(block, sort_keys=True, indent=2, separators=(',', ': '))