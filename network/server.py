"""
TCP server thread.

Accepts incoming connections from peers and dispatches each one to a
PeerHandler thread.  The handler speaks the same framed-JSON protocol
and delegates processing to the shared Node object.
"""

import socket
import threading
import json
import logging

from network.protocol import recv_message, send_message, send_raw
from utils.debug_flags import debug_print

logger = logging.getLogger(__name__)


class PeerHandler(threading.Thread):
    """
    Runs in its own thread.  Reads messages from one connected peer and
    calls back into *node* for processing.
    """

    def __init__(self, conn: socket.socket, addr, node):
        super().__init__(daemon=True)
        self._conn = conn
        self._addr = addr
        self._node = node

    def run(self):
        try:
            debug_print(f"[DEBUG] PeerHandler connected from {self._addr}")
            while True:
                msg = recv_message(self._conn)
                if msg is None:
                    debug_print(f"[DEBUG] PeerHandler {self._addr} peer closed connection")
                    break   # peer closed connection

                msg_type = msg.get("type")
                payload  = msg.get("payload")

                debug_print(f"[DEBUG] PeerHandler {self._addr} received: type={msg_type}")

                if msg_type == "transaction":
                    tx_info = payload.get("message", "?") if payload else "?"
                    debug_print(f"[DEBUG]   Transaction: {tx_info}")
                    accepted = self._node.handle_incoming_transaction(payload)
                    debug_print(f"[DEBUG]   Accepted: {accepted}")
                    # Send boolean acknowledgement
                    response = b"true" if accepted else b"false"
                    send_raw(self._conn, response)

                elif msg_type == "values":
                    # During consensus: peer sends its proposal and expects ours
                    debug_print(f"[DEBUG]   Values request (consensus)")
                    self._node.handle_values_request(self._conn, payload)

                elif msg_type == "commit":
                    # Peer broadcast the decided block — commit it if valid
                    block_idx = payload.get("index", "?") if payload else "?"
                    debug_print(f"[DEBUG]   Commit block index: {block_idx}")
                    self._node.handle_commit(payload)

                else:
                    logger.debug("Unknown message type from %s: %s", self._addr, msg_type)
                    debug_print(f"[DEBUG] Unknown message type: {msg_type}")

        except (ConnectionError, OSError, json.JSONDecodeError) as exc:
            logger.debug("PeerHandler %s ended: %s", self._addr, exc)
        finally:
            try:
                self._conn.close()
            except OSError:
                pass


class Server(threading.Thread):
    """
    Listens on *port* and spawns PeerHandler threads for each accepted connection.
    Binds the socket immediately on construction (before the thread starts)
    so the port is ready as soon as the object exists.
    """

    def __init__(self, host: str, port: int, node):
        super().__init__(daemon=True)
        self._host = host
        self._port = port
        self._node = node

        # Bind immediately so the port is open before any thread starts
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self._host, self._port))
        self._sock.listen(32)
        debug_print(f"[DEBUG] Server initialized on {self._host}:{self._port}")

    def run(self):
        debug_print(f"[DEBUG] Server listening on {self._host}:{self._port}")
        logger.info("Server listening on %s:%d", self._host, self._port)

        while True:
            try:
                conn, addr = self._sock.accept()
                debug_print(f"[DEBUG] Server accepted connection from {addr}")
                handler = PeerHandler(conn, addr, self._node)
                handler.start()
            except OSError:
                break   # socket closed – shutting down

    def stop(self):
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass