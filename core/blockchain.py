"""
Blockchain: in-memory chain and mutable state (confirmed nonces, pool).
"""

import threading
import json

from core.block import make_genesis_block, block_to_json_str
from core.transaction import tx_key, tx_to_dict
from utils.json_debug import log_block_event, log_pool_event
from utils.debug_flags import debug_print


class Blockchain:
    """
    Thread-safe container for:
      - The chain of decided blocks.
      - Confirmed sender nonces (how many txs each sender has on-chain).
      - The pending transaction pool (mempool).
    """

    def __init__(self):
        self._lock       = threading.Lock()
        self._chain      = [make_genesis_block()]
        # sender_hex → count of confirmed transactions
        self._nonces: dict[str, int] = {}
        # (sender, nonce) → tx dict  — the mempool
        self._pool:   dict[tuple, dict] = {}

    # ── Read-only helpers (public, thread-safe) ───────────────────────────────

    def get_confirmed_nonce(self, sender: str) -> int:
        with self._lock:
            return self._nonces.get(sender, 0)

    def get_confirmed_nonces_snapshot(self) -> dict:
        with self._lock:
            return dict(self._nonces)

    def last_block(self) -> dict:
        with self._lock:
            return self._chain[-1]

    def chain_length(self) -> int:
        with self._lock:
            return len(self._chain)

    def next_index(self) -> int:
        with self._lock:
            return len(self._chain) + 1

    def last_hash(self) -> str:
        with self._lock:
            return self._chain[-1]["current_hash"]

    # ── Pool management ───────────────────────────────────────────────────────

    def pool_add(self, tx: dict) -> bool:
        """
        Add tx to the mempool if the (sender, nonce) slot is free.
        Returns True if added, False if duplicate.
        """
        key = tx_key(tx)
        with self._lock:
            if key in self._pool:
                debug_print(f"[DEBUG] [BLOCKCHAIN] Pool rejected duplicate tx: sender={key[0][:16]}..., nonce={key[1]}")
                log_pool_event("DUPLICATE_REJECTED", len(self._pool), f"sender={key[0][:16]}..., nonce={key[1]}")
                return False
            self._pool[key] = tx_to_dict(tx)
            debug_print(f"[DEBUG] [BLOCKCHAIN] Pool now has {len(self._pool)} transactions")
            log_pool_event("TRANSACTION_ADDED", len(self._pool), f"sender={key[0][:16]}..., nonce={key[1]}")
            return True

    def pool_snapshot(self) -> list:
        """Return a stable list of all pooled transactions."""
        with self._lock:
            return list(self._pool.values())

    def pool_is_empty(self) -> bool:
        with self._lock:
            return len(self._pool) == 0

    # ── Committing a decided block ────────────────────────────────────────────

    def commit_block(self, block: dict) -> None:
        """
        Append *block* to the chain, update nonces, and prune the pool.
        Prints the block to stdout as required.
        """
        tx_outputs = []

        with self._lock:
            # Guard: don't commit duplicate indices
            expected_index = len(self._chain) + 1
            if block["index"] != expected_index:
                debug_print(f"[DEBUG] Block commit rejected: index {block['index']} != expected {expected_index}")
                log_block_event(block['index'], "REJECTED", reason=f"index_mismatch_expected_{expected_index}")
                return

            chain_len_before = len(self._chain)
            pool_size_before = len(self._pool)
            
            self._chain.append(block)
            debug_print(f"[DEBUG] [BLOCKCHAIN] Block #{block['index']} added to chain (chain length: {chain_len_before}→{len(self._chain)})")
            log_block_event(block['index'], "ADDED_TO_CHAIN", chain_length_before=chain_len_before, chain_length_after=len(self._chain))

            # Prepare transaction stdout entries for txs this node did not directly accept.
            # This keeps follower-node output aligned with nodes that received the tx first.
            pool_keys_before = set(self._pool.keys())
            for tx in block["transactions"]:
                if tx_key(tx) not in pool_keys_before:
                    tx_outputs.append(
                        {
                            "type": "transaction",
                            "payload": tx_to_dict(tx),
                        }
                    )

            # Update confirmed nonces for every tx in the block
            for tx in block["transactions"]:
                sender = tx["sender"]
                self._nonces[sender] = self._nonces.get(sender, 0) + 1

            # Prune pool: remove confirmed txs and now-stale ones
            to_remove = []
            for key, tx in self._pool.items():
                sender        = tx["sender"]
                confirmed     = self._nonces.get(sender, 0)
                # If the tx's nonce is < confirmed, it's been committed or is stale
                if tx["nonce"] < confirmed:
                    to_remove.append(key)
            
            for key in to_remove:
                del self._pool[key]
            
            pool_size_after = len(self._pool)
            debug_print(f"[DEBUG] [BLOCKCHAIN] Pool pruned: {pool_size_before} → {pool_size_after} transactions")
            log_pool_event("PRUNED_AFTER_COMMIT", pool_size_after, f"removed_{pool_size_before - pool_size_after}_txs")

        # Print synthetic transaction entries first (outside lock to avoid deadlock).
        for tx_output in tx_outputs:
            print(json.dumps(tx_output, sort_keys=True, indent=2, separators=(',', ': ')), flush=True)

        # Print block to stdout (outside lock to avoid deadlock)
        print(block_to_json_str(block), flush=True)
        log_block_event(block['index'], "PRINTED_TO_STDOUT")