# Design Document — Blockchain Node

## Overview

This project implements a distributed blockchain node in Python. A cluster of `N` peer nodes collectively maintain a single agreed-upon chain of transaction blocks using a synchronous crash-fault-tolerant (CFT) consensus protocol. Nodes communicate over persistent TCP connections using a lightweight length-prefixed JSON wire protocol.

---

## Goals & Constraints

| Goal              | Detail                                                                |
| ----------------- | --------------------------------------------------------------------- |
| Correctness       | All non-crashed nodes must commit the same block at each chain index  |
| Crash tolerance   | The system makes progress as long as at least one node is live        |
| Sequential nonces | Each sender's transactions must be committed in strict nonce order    |
| Ed25519 auth      | Every transaction must carry a valid signature from the sender's key  |
| Stdout format     | Accepted transactions and committed blocks are printed as JSON format |

---

## Component Map

```
main.py
  └─► Node                  (callback hub, stateless coordinator)
        ├─► Blockchain       (chain + mempool + nonces; all shared mutable state)
        ├─► Server           (TCP accept loop; spawns PeerHandler per connection)
        │     └─► PeerHandler (one thread per inbound peer; dispatches to Node)
        ├─► PeerManager      (one PeerConnection per outbound peer)
        │     └─► PeerConnection (retrying connector + proposal exchange)
        └─► ConsensusEngine  (daemon thread; one CFT round at a time)
```

---

## Data Structures

### Transaction

```json
{
  "sender": "<64 hex chars — Ed25519 public key>",
  "message": "<≤70 printable ASCII chars>",
  "nonce": 0,
  "signature": "<128 hex chars — Ed25519 signature>"
}
```

The signed payload is `json.dumps({"message":…,"nonce":…,"sender":…}, sort_keys=True)`.

### Block

```json
{
  "index":         2,
  "previous_hash": "<64 hex chars>",
  "transactions":  [ … ],
  "current_hash":  "<64 hex chars>"
}
```

`current_hash = SHA-256(canonical_json({index, previous_hash, transactions}))`.
The canonical form uses `sort_keys=True, indent=2, separators=(',', ': ')` — matching the reference hashing script exactly.

### Chain

- Starts with a genesis block: `index=1`, empty transactions, `previous_hash = "0"*64`.
- All subsequent blocks reference the `current_hash` of the prior block.

### Mempool

An in-memory `dict` keyed by `(sender_hex, nonce)` → `tx_dict`. This key uniquely identifies a transaction slot and prevents duplicates at insert time.

### Confirmed Nonces

`dict[sender_hex → int]` — the count of committed transactions per sender. A new transaction is valid only if its `nonce == confirmed_nonces.get(sender, 0)`.

---

## Wire Protocol

Every message is framed as:

```
[ 2-byte big-endian length ][ JSON body (UTF-8) ]
```

Maximum message size: 65 535 bytes (fits in the 16-bit length field).

### Message Types

| `type`        | `payload`        | Direction     | Purpose                                   |
| ------------- | ---------------- | ------------- | ----------------------------------------- |
| `transaction` | transaction dict | client → node | Submit a new transaction                  |
| `values`      | `[block_dict]`   | node ↔ node   | Exchange block proposals during consensus |
| `commit`      | block dict       | node → peers  | Broadcast the decided block               |

Inbound `transaction` messages receive a raw `true` / `false` byte response (no length prefix) to indicate acceptance. This is the only asymmetric response in the protocol.

---

## Transaction Lifecycle

```
Client  ──TCP──►  PeerHandler.run()
                      │
                  node.handle_incoming_transaction(payload)
                      │
                  1. validate_transaction(tx, confirmed_nonces)
                     - required fields present and correct types
                     - sender: 64 hex chars
                     - message: ≤70 printable ASCII chars
                     - nonce == confirmed_nonces.get(sender, 0)
                     - signature valid (PyNaCl Ed25519)
                  2. dedup check against current mempool keys
                  3. blockchain.pool_add(tx)
                  4. print accepted tx to stdout
                  5. consensus_engine.notify_pool_non_empty()
                      │
                  ◄── send_raw("true")
```

Rejections at any step return `"false"` and nothing is printed to stdout.

---

## Consensus Protocol

The engine runs as a single daemon thread, processing one round at a time. Rounds are serialised by `_round_lock` to prevent concurrent proposals.

```
loop:
  wait on threading.Event  (set by: new tx accepted OR peer sent "values")
  clear event

  Step 1 – Build proposal
    txs       = blockchain.pool_snapshot()
    prev_hash = blockchain.last_hash()
    index     = blockchain.next_index()
    my_block  = make_block(index, txs, prev_hash)

  Step 2 – Exchange proposals concurrently
    for each active peer (not .crashed):
      thread: peer.exchange_proposal(my_block)
        send "values" message with my_block
        recv peer's "values" response (2-second socket timeout)
        on timeout/error → mark peer.crashed = True, return None

  Step 3 – Decide
    candidates = [non-empty proposals]  or  [all proposals] if none have txs
    decided    = min(candidates, key=lambda b: b["current_hash"])

  Step 4 – Commit
    if decided["index"] == blockchain.next_index():
      blockchain.commit_block(decided)
      peer_manager.broadcast_commit(decided)

  if pool still non-empty → re-set trigger (next round immediately)
```

### Decision Rule Rationale

Picking the block with the lexicographically smallest `current_hash` among non-empty candidates is a deterministic tie-breaker. Because every node runs the same rule on the same set of proposals, all nodes that complete the exchange reach the same decision without any extra coordination messages.

Preferring non-empty blocks over empty ones ensures the chain makes progress when at least one node has pending transactions.

### Crash Handling

A peer is marked `crashed = True` on any socket error or 2-second timeout during `exchange_proposal`. Crashed peers are excluded from future rounds (they are not retried). This is intentional: the spec assumes permanent crashes, and skipping crashed peers avoids blocking future rounds indefinitely.

---

## Commit & Chain Update

`blockchain.commit_block(block)`:

1. Guards against duplicate indices (another thread may have committed first).
2. Appends the block to `_chain`.
3. For each transaction in the block: increments `_nonces[sender]`.
4. Prunes the mempool: removes any tx whose `nonce < confirmed_nonces[sender]` (already committed or now stale).
5. For transactions that arrived via `commit` broadcast (not seen locally): prints them to stdout so every node's output is identical.
6. Prints the block dict to stdout as pretty JSON.

All steps under a single `threading.Lock` except the stdout prints (to avoid holding the lock during I/O).

---

## Threading Model

| Thread                 | Count                     | Role                                           |
| ---------------------- | ------------------------- | ---------------------------------------------- |
| Main                   | 1                         | Wires components, then sleeps in signal loop   |
| Server                 | 1                         | Accept loop; spawns PeerHandler per connection |
| PeerHandler            | 1 per inbound connection  | Reads messages; calls Node callbacks           |
| ConsensusEngine        | 1                         | Daemon; runs one CFT round at a time           |
| PeerConnection.connect | 1 per peer (short-lived)  | Retries until outbound socket is established   |
| `_fetch_proposal`      | N per round (short-lived) | Concurrent proposal exchanges during consensus |

All shared mutable state (`_chain`, `_nonces`, `_pool`) is protected by a single `threading.Lock` in `Blockchain`.

---

## Startup Sequence

```
1. Parse CLI args: port, peer-list file
2. Instantiate Node  (creates Blockchain with genesis block)
3. Bind TCP socket immediately (Server.__init__)  ← port is open before any thread starts
4. Create PeerManager (no connections yet)
5. Create ConsensusEngine
6. server.start()          — accept loop running
7. engine.start()          — consensus loop waiting on trigger
8. peer_manager.connect_all()  — background connect threads (non-blocking)
9. Register SIGINT/SIGTERM handler
10. Main thread sleeps
```

Binding the socket in step 3 (before starting any threads) ensures the port is open the moment the process is reachable — preventing race conditions where a peer or autograder connects before the server thread has started.

---

## Key Design Decisions

**Single `Blockchain` lock instead of fine-grained locks**
The chain, nonces, and pool are almost always accessed together (e.g., commit updates all three). A coarse lock is simpler and correct; lock contention is not a bottleneck because the consensus round serialises most write activity.

**Dedup at two levels**
Transactions are deduplicated both in `Node.handle_incoming_transaction` (mempool key check) and inside `blockchain.pool_add`. The double check prevents a race where two concurrent `PeerHandler` threads accept the same transaction before either commits to the pool.

**Proposal exchange is concurrent, consensus round is serialised**
The `_round_lock` prevents two rounds from running simultaneously (e.g., if a peer triggers a round while we are already mid-round). Within a round, all peer exchanges run in parallel threads to minimise wall-clock time.

**Re-trigger after commit**
After committing a block, if the pool is still non-empty (more transactions arrived), the engine immediately re-sets its trigger so the next round starts without waiting for an external event.

**Socket bound before threads start**
`Server` binds and listens in `__init__`, not in `run()`. This guarantees the port is available as soon as the `Server` object exists, before the accept-loop thread has even been scheduled.
