"""
Consensus engine.

Implements the synchronous crash-fault-tolerant protocol described in the spec:

1. Wait until the pool is non-empty OR a peer triggers the round.
2. Build a block proposal.
3. Exchange proposals with every non-crashed peer (2-second timeout each).
4. Decide: prefer non-empty blocks; among those, pick the one with the
   lexicographically smallest current_hash.
5. Commit the decided block and loop.
"""

import threading
import logging

from core.block      import make_block
from core.blockchain import Blockchain
from network.peers   import PeerManager
from utils.json_debug import log_consensus_event, log_block_event
from utils.debug_flags import debug_print

logger = logging.getLogger(__name__)


class ConsensusEngine(threading.Thread):
    """
    Runs as a daemon thread, driving one consensus round at a time.
    """

    def __init__(self, blockchain: Blockchain, peer_manager: PeerManager):
        super().__init__(daemon=True)
        self._bc      = blockchain
        self._pm      = peer_manager
        # Event set either when pool becomes non-empty or a peer triggers a round
        self._trigger = threading.Event()
        # Lock to prevent concurrent rounds
        self._round_lock = threading.Lock()
        self._in_round = False

    # ── Public API ────────────────────────────────────────────────────────────

    def notify_pool_non_empty(self) -> None:
        """Called by the node whenever a transaction is added to the pool."""
        self._trigger.set()

    def trigger_round(self) -> None:
        """Called when a peer sends a values request (they started a round)."""
        self._trigger.set()

    # ── Thread main loop ──────────────────────────────────────────────────────

    def run(self):
        debug_print("[DEBUG] Consensus engine started")
        while True:
            # Wait for something to propose
            debug_print("[DEBUG] Consensus: waiting for trigger...")
            self._trigger.wait()
            self._trigger.clear()
            debug_print("[DEBUG] Consensus: trigger fired!")

            with self._round_lock:
                self._in_round = True
                try:
                    self._run_round()
                finally:
                    self._in_round = False

            # Re-trigger if pool still has transactions after committing
            if not self._bc.pool_is_empty():
                debug_print("[DEBUG] Consensus: pool not empty, re-triggering")
                self._trigger.set()

    # ── Single consensus round ────────────────────────────────────────────────

    def _run_round(self) -> None:
        # ── Step 1: Build our proposal ────────────────────────────────────────
        txs           = self._bc.pool_snapshot()
        prev_hash     = self._bc.last_hash()
        index         = self._bc.next_index()
        my_block      = make_block(index, txs, prev_hash)
        
        debug_print(f"[DEBUG] ROUND START: index={index}, txs_in_pool={len(txs)}")
        debug_print(f"[DEBUG]   My block hash: {my_block.get('current_hash', '?')[:16]}...")
        log_consensus_event(index, "STARTED", txs_in_pool=len(txs), my_hash=my_block.get('current_hash', '')[:16])

        # ── Step 2: Exchange proposals ────────────────────────────────────────
        proposals = [my_block]
        active    = self._pm.active_peers()
        debug_print(f"[DEBUG]   Exchanging with {len(active)} peers...")

        # Collect peer proposals concurrently for speed
        results  = [None] * len(active)
        threads  = []
        for i, peer in enumerate(active):
            t = threading.Thread(
                target=self._fetch_proposal,
                args=(peer, my_block, results, i),
                daemon=True,
            )
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        for r in results:
            if r is not None:
                proposals.append(r)
        
        debug_print(f"[DEBUG]   Received {len(proposals) - 1} peer responses, total {len(proposals)} proposals")


        # ── Step 3: Decide ────────────────────────────────────────────────────
        decided = self._decide(proposals)
        if decided is None:
            debug_print(f"[DEBUG] ✗ No decision possible (no proposals)")
            log_consensus_event(index, "NO_DECISION", proposals_received=len(proposals)-1)
            logger.warning("Consensus produced no decision this round (no proposals)")
            return

        decided_hash = decided.get("current_hash", "?")[:16]
        decided_idx = decided.get("index", "?")
        decided_txs = len(decided.get("transactions", []))
        debug_print(f"[DEBUG] ✓ DECIDED: index={decided_idx}, hash={decided_hash}..., txs={decided_txs}")
        log_consensus_event(index, "DECIDED", decided_index=decided_idx, decided_hash=decided_hash, decided_txs=decided_txs, proposals_count=len(proposals))

        # ── Step 4: Commit ────────────────────────────────────────────────────
        # Guard: only commit if the index still matches (another thread may have
        # committed first in degenerate multi-trigger scenarios)
        if decided["index"] == self._bc.next_index():
            debug_print(f"[DEBUG] → Committing block index {decided['index']}")
            log_block_event(decided['index'], "COMMITTING")
            self._bc.commit_block(decided)
            # Broadcast committed block to all peers so they can also commit
            self._pm.broadcast_commit(decided)
            log_block_event(decided['index'], "COMMITTED")
            debug_print(f"[DEBUG] ROUND COMPLETE")
        else:
            debug_print(f"[DEBUG] ✗ Skipping commit: decided index {decided['index']} != next expected {self._bc.next_index()}")
            log_consensus_event(index, "COMMIT_SKIPPED", reason=f"index_mismatch_{decided['index']}_vs_{self._bc.next_index()}")
            logger.debug(
                "Skipping commit: decided index %d but next expected %d",
                decided["index"], self._bc.next_index(),
            )

    def _fetch_proposal(
        self,
        peer,
        my_block: dict,
        results: list,
        idx: int,
    ) -> None:
        block = peer.exchange_proposal(my_block)
        results[idx] = block

    # ── Decision rule ─────────────────────────────────────────────────────────

    @staticmethod
    def _decide(proposals: list[dict]) -> dict | None:
        """
        Among gathered proposals:
          1. If any non-empty block exists, discard all empty-transaction blocks.
          2. Return the block with the lexicographically smallest current_hash.
        """
        if not proposals:
            return None

        non_empty = [b for b in proposals if b.get("transactions")]
        candidates = non_empty if non_empty else proposals

        return min(candidates, key=lambda b: b.get("current_hash", ""))
