"""
main.py – Entry point for a blockchain node.
Usage: python3 main.py <port> <peer-list-file>
"""

import sys
import logging
import signal
import time

from utils.peer_list  import parse_peer_list
from utils.json_debug import log_startup_event
from utils.debug_flags import set_debug_enabled, debug_print
from node             import Node
from network.server   import Server
from network.peers    import PeerManager
from consensus.engine import ConsensusEngine


# Master switch: keep debug output disabled during normal runs so stdout

DEBUG_MODE = False

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)


def main():
    set_debug_enabled(DEBUG_MODE)

    if len(sys.argv) != 3:
        sys.exit("Usage: python3 main.py <port> <peer-list-file>")

    port           = int(sys.argv[1])
    peer_list_file = sys.argv[2]
    peers          = parse_peer_list(peer_list_file)
    
    log_startup_event(port, len(peers))

    debug_print(f"[DEBUG] === Node Starting ===")
    debug_print(f"[DEBUG] Port: {port}")
    debug_print(f"[DEBUG] Peers: {peers}")

    node = Node()

    # Server binds immediately in __init__ so the port is open right away
    # before any peer or the autograder tries to connect.
    server = Server("0.0.0.0", port, node)

    peer_manager = PeerManager(peers)
    engine       = ConsensusEngine(node.blockchain, peer_manager)

    node.set_consensus_engine(engine)

    debug_print(f"[DEBUG] Components initialized")

    # Start accept loop and consensus loop
    server.start()
    debug_print(f"[DEBUG] Server started (listening on {port})")
    
    engine.start()
    debug_print(f"[DEBUG] Consensus engine started")

    # Connect to peers in background — does NOT block main thread.
    # The server is already listening so incoming connections work immediately.
    debug_print(f"[DEBUG] Connecting to peers...")
    peer_manager.connect_all()
    debug_print(f"[DEBUG] Peer connections initiated")

    def _shutdown(sig, frame):
        debug_print(f"\n[DEBUG] Shutting down...")
        peer_manager.close_all()
        server.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    debug_print(f"[DEBUG] Ready for transactions")
    debug_print(f"[DEBUG] ==================")

    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()