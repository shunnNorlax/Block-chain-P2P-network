# COMP3221 Assignment 2 – Blockchain Node

## Language & Dependencies

- Python 3.10+
- **PyNaCl** (allowed) – Ed25519 signature verification

Install: `pip install PyNaCl`

---

## File Structure

```
blockchain/
├── Run.sh                  # Entry-point wrapper (required by spec)
├── main.py                 # Startup: wires all components
├── node.py                 # Node class: callback hub for server → consensus
│
├── core/
│   ├── transaction.py      # Validation rules + Ed25519 verification
│   ├── block.py            # Block creation, canonical hashing (SHA-256)
│   └── blockchain.py       # Thread-safe chain + mempool + nonce state
│
├── network/
│   ├── protocol.py         # Wire framing: 2-byte length prefix + JSON
│   ├── server.py           # TCP server + PeerHandler threads
│   └── peers.py            # PeerManager + PeerConnection (outbound)
│
├── consensus/
│   └── engine.py           # ConsensusEngine: synchronous CFT round loop
│
└── utils/
    └── peer_list.py        # Peer-list file parser
```

---

## Running

```bash
./Run.sh 5000 peers_5000.txt
```

Each peers file lists one `host:port` per line for every OTHER node.

---

## Consensus Protocol Summary

```
loop:
  wait for trigger (pool non-empty OR peer "values" request)
  build my_block from current pool
  concurrently: for each peer → send my_block, await their block (2 s timeout)
    on timeout → mark peer crashed
  candidates = non-empty proposals (or all if none have transactions)
  decided = min(candidates, key=current_hash)
  if decided.index == expected: commit + print block
```
