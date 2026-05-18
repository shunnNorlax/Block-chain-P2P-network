"""
PeerManager – maintains one persistent outbound TCP connection per peer.
"""

import socket
import threading
import logging
import time

from network.protocol import send_message, recv_message
from utils.json_debug import log_network_event
from utils.debug_flags import debug_print

logger = logging.getLogger(__name__)

CONSENSUS_TIMEOUT = 2.0


class PeerConnection:
    def __init__(self, host: str, port: int):
        self.host    = host
        self.port    = port
        self.crashed = False
        self._sock   = None
        self._lock   = threading.Lock()

    def connect(self) -> bool:
        """Retry connecting in background until success (spec: no startup timeout)."""
        attempt = 0
        while True:
            attempt += 1
            try:
                debug_print(f"[DEBUG] Peer {self.host}:{self.port} connect attempt #{attempt}")
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect((self.host, self.port))
                with self._lock:
                    self._sock = sock
                debug_print(f"[DEBUG] ✓ Connected to peer {self.host}:{self.port}")
                log_network_event(self.host, self.port, "CONNECTED", attempt=attempt)
                logger.info("Connected to peer %s:%d", self.host, self.port)
                return True
            except OSError as e:
                if attempt <= 3:  # Only log first few attempts
                    debug_print(f"[DEBUG]   Connection failed: {e}, retrying...")
                if attempt == 1:
                    log_network_event(self.host, self.port, "CONNECTION_ATTEMPT_FAILED", error=str(e), attempt=attempt)
                time.sleep(0.2)

    def close(self):
        with self._lock:
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None

    def _wait_for_socket(self, timeout: float):
        """Wait briefly for the background connector to establish the socket."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if self._sock is not None:
                    return self._sock
            time.sleep(0.05)
        return None

    def send_commit(self, block: dict) -> None:
        """Send the decided block to this peer (best-effort)."""
        if self.crashed:
            return
        with self._lock:
            sock = self._sock
            if sock is None:
                return
            try:
                block_idx = block.get("index", "?")
                debug_print(f"[DEBUG]   Sending commit to {self.host}:{self.port} (block #{block_idx})")
                sock.settimeout(2.0)
                send_message(sock, "commit", block)
                sock.settimeout(None)
                debug_print(f"[DEBUG]   ✓ Commit sent to {self.host}:{self.port}")
            except OSError as e:
                debug_print(f"[DEBUG]   ✗ Failed to send commit to {self.host}:{self.port}: {e}")
                sock.settimeout(None)

    def exchange_proposal(self, my_block) -> dict | None:
        """Send our proposal, wait up to 2s for peer's. Mark crashed on timeout."""
        if self.crashed:
            debug_print(f"[DEBUG] Peer {self.host}:{self.port} already marked crashed")
            return None

        sock = self._wait_for_socket(CONSENSUS_TIMEOUT)
        if sock is None:
            debug_print(f"[DEBUG] Peer {self.host}:{self.port} no socket connection before timeout")
            log_network_event(self.host, self.port, "CRASHED", reason="connect_timeout")
            self.crashed = True
            return None

        with self._lock:
            sock = self._sock
            if sock is None:
                debug_print(f"[DEBUG] Peer {self.host}:{self.port} socket disappeared before send")
                log_network_event(self.host, self.port, "CRASHED", reason="socket_lost")
                self.crashed = True
                return None

            try:
                my_idx = my_block.get("index", "?") if my_block else "?"
                debug_print(f"[DEBUG] Sending proposal to {self.host}:{self.port} (my index: {my_idx})")
                
                payload = [my_block] if my_block is not None else []
                sock.settimeout(CONSENSUS_TIMEOUT)
                send_message(sock, "values", payload)

                response = recv_message(sock)
                sock.settimeout(None)

                if response is None:
                    debug_print(f"[DEBUG] Peer {self.host}:{self.port} closed connection")
                    log_network_event(self.host, self.port, "CRASHED", reason="connection_closed")
                    raise ConnectionError("peer closed connection")

                blocks = response.get("payload", [])
                peer_block = blocks[0] if blocks else None
                peer_idx = peer_block.get("index", "?") if peer_block else "empty"
                debug_print(f"[DEBUG] ✓ Got response from {self.host}:{self.port} (peer index: {peer_idx})")
                log_network_event(self.host, self.port, "PROPOSAL_RECEIVED", peer_index=peer_idx, my_index=my_idx)
                return peer_block

            except (OSError, ConnectionError, TimeoutError) as exc:
                debug_print(f"[DEBUG] ✗ Peer {self.host}:{self.port} CRASHED: {type(exc).__name__}")
                log_network_event(self.host, self.port, "CRASHED", error_type=type(exc).__name__, error=str(exc))
                logger.debug("Peer %s:%d unresponsive: %s", self.host, self.port, exc)
                self.crashed = True
                try:
                    sock.settimeout(None)
                except OSError:
                    pass
                return None


class PeerManager:
    def __init__(self, peers: list):
        self._peers = [PeerConnection(h, p) for h, p in peers]

    def connect_all(self) -> None:
        """
        Start background threads to connect each peer — non-blocking.
        Main thread is NOT held up; server is already listening.
        """
        debug_print(f"[DEBUG] PeerManager starting connection threads for {len(self._peers)} peers")
        for p in self._peers:
            threading.Thread(target=p.connect, daemon=True).start()

    def broadcast_commit(self, block: dict) -> None:
        """Send the decided block to all peers so they can commit it."""
        active = [p for p in self._peers if not p.crashed]
        debug_print(f"[DEBUG] Broadcasting commit (block #{block.get('index', '?')}) to {len(active)}/{len(self._peers)} peers")
        for p in self._peers:
            if not p.crashed:
                p.send_commit(block)
        debug_print(f"[DEBUG] Broadcast complete")

    def active_peers(self) -> list:
        active = [p for p in self._peers if not p.crashed]
        debug_print(f"[DEBUG] Active peers: {len(active)}/{len(self._peers)}")
        return active

    def all_peers(self) -> list:
        return list(self._peers)

    def close_all(self) -> None:
        for p in self._peers:
            p.close()