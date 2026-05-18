"""
Node – the top-level object for a blockchain peer.

Responsibilities:
  - Accept incoming transactions and values messages (via Server callbacks).
  - Drive consensus rounds (via ConsensusEngine).
  - Maintain the Blockchain state.
"""

import json
import logging

from core.blockchain            import Blockchain
from core.transaction           import (
    validate_transaction,
    TransactionValidationError,
    tx_to_dict,
    tx_key,
)
from core.block                 import make_block
from network.protocol           import send_message
from consensus.engine           import ConsensusEngine
from utils.json_debug           import log_transaction_event
from utils.debug_flags          import debug_print

logger = logging.getLogger(__name__)


class Node:
    """
    The Node is the central coordinator.

    It does NOT own the Server or PeerManager – those are created by
    main.py and wired in after construction so unit-tests can inject mocks.
    """

    def __init__(self):
        self._bc     = Blockchain()
        self._engine = None

    # ── Wiring ────────────────────────────────────────────────────────────────

    def set_consensus_engine(self, engine) -> None:
        self._engine = engine

    # ── Incoming transaction handler (called from PeerHandler threads) ────────

    def handle_incoming_transaction(self, payload) -> bool:
        """
        Validate and pool a transaction.

        Returns True if accepted, False if rejected.
        Prints the transaction JSON to stdout on acceptance.
        """
        try:
            if not isinstance(payload, dict):
                debug_print(f"[DEBUG] Transaction rejected: not a dict")
                log_transaction_event("REJECTED", "not_a_dict")
                return False

            # Build snapshot of current confirmed nonces for validation
            nonces = self._bc.get_confirmed_nonces_snapshot()

            # Extra dedup: reject if (sender, nonce) already in pool
            pooled_keys = {tx_key(tx) for tx in self._bc.pool_snapshot()}

            sender = payload.get("sender", "unknown")[:16]
            nonce = payload.get("nonce", "?")
            message = payload.get("message", "?")

            validate_transaction(payload, nonces)

            key = tx_key(payload)
            if key in pooled_keys:
                debug_print(f"[DEBUG] Transaction rejected: duplicate (sender, nonce)")
                log_transaction_event("REJECTED", "duplicate_key", sender=sender, nonce=nonce, message=message)
                return False

            added = self._bc.pool_add(payload)
            if not added:
                debug_print(f"[DEBUG] Transaction rejected: pool_add returned False")
                log_transaction_event("REJECTED", "pool_add_failed", sender=sender, nonce=nonce, message=message)
                return False

            # Print accepted transaction to stdout
            tx_clean = tx_to_dict(payload)
            msg = tx_clean.get("message", "?")
            debug_print(f"[DEBUG] ✓ Transaction accepted: {msg} (from {sender}...)")
            log_transaction_event("ACCEPTED", sender=sender, nonce=nonce, message=message)
            
            output = {
                "type":    "transaction",
                "payload": tx_clean,
            }
            print(
                json.dumps(output, sort_keys=True, indent=2, separators=(',', ': ')),
                flush=True,
            )

            # Trigger a consensus round
            if self._engine:
                debug_print(f"[DEBUG]   Notifying consensus engine")
                self._engine.notify_pool_non_empty()

            return True

        except TransactionValidationError as exc:
            reason = str(exc)
            debug_print(f"[DEBUG] Transaction rejected: validation error - {reason}")
            sender = payload.get("sender", "unknown")[:16] if isinstance(payload, dict) else "?"
            nonce = payload.get("nonce", "?") if isinstance(payload, dict) else "?"
            message = payload.get("message", "") if isinstance(payload, dict) else ""
            log_transaction_event(
                "REJECTED",
                f"validation_error: {reason}",
                sender=sender,
                nonce=nonce,
                message=message,
            )
            return False
        except Exception as exc:
            reason = str(exc)
            debug_print(f"[DEBUG] Transaction rejected: unexpected error - {reason}")
            sender = payload.get("sender", "unknown")[:16] if isinstance(payload, dict) else "?"
            nonce = payload.get("nonce", "?") if isinstance(payload, dict) else "?"
            message = payload.get("message", "") if isinstance(payload, dict) else ""
            log_transaction_event(
                "REJECTED",
                f"exception: {reason}",
                sender=sender,
                nonce=nonce,
                message=message,
            )
            return False

    # ── Incoming values handler (called from PeerHandler threads) ─────────────

    def handle_values_request(self, conn, peer_payload) -> None:
        """
        A peer sent us a "values" message (its block proposal).
        We must immediately reply with our own proposal.
        Also triggers a consensus round if we hadn't started one already.
        """
        debug_print(f"[DEBUG] Incoming values request from peer")

        txs       = self._bc.pool_snapshot()
        prev_hash = self._bc.last_hash()
        index     = self._bc.next_index()
        my_block  = make_block(index, txs, prev_hash)
        debug_print(f"[DEBUG]   Sending my proposal: index={index}, txs={len(txs)}")

        try:
            send_message(conn, "values", [my_block])
            debug_print(f"[DEBUG] ✓ Sent values response")
        except OSError as exc:
            debug_print(f"[DEBUG] ✗ Failed to send values response: {exc}")
            logger.debug("Failed to send values response: %s", exc)

    # ── Incoming commit handler ───────────────────────────────────────────────

    def handle_commit(self, block) -> None:
        """A peer broadcast the decided block. Commit it if index matches."""
        if not isinstance(block, dict):
            debug_print(f"[DEBUG] Commit rejected: not a dict")
            return
        
        block_idx = block.get("index", "?")
        next_idx = self._bc.next_index()
        
        if block_idx == next_idx:
            debug_print(f"[DEBUG] ✓ Committing block index {block_idx} from peer")
            # Remove pooled txs that are in this block before committing
            self._bc.commit_block(block)
            debug_print(f"[DEBUG]   Block committed to chain")
        else:
            debug_print(f"[DEBUG] Commit rejected: index {block_idx} != expected {next_idx}")

    # ── Accessors ─────────────────────────────────────────────────────────────

    @property
    def blockchain(self) -> Blockchain:
        return self._bc