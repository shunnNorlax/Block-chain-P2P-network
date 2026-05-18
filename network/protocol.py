"""
Wire protocol helpers.

Every message on the wire is:
    [2-byte big-endian unsigned length][JSON bytes]

Maximum message size: 65535 bytes (fits in 16-bit length field).
"""

import json
import struct

HEADER_FORMAT  = ">H"          # big-endian unsigned short
HEADER_SIZE    = struct.calcsize(HEADER_FORMAT)   # 2 bytes
MAX_MSG_BYTES  = 0xFFFF


def send_message(sock, msg_type: str, payload) -> None:
    """
    Encode and send a typed message over *sock*.

    :param sock:     Connected TCP socket.
    :param msg_type: "transaction" or "values".
    :param payload:  Python object to embed as the JSON payload.
    """
    body = json.dumps(
        {"type": msg_type, "payload": payload},
        sort_keys=True,
        separators=(',', ':'),
    ).encode("utf-8")

    if len(body) > MAX_MSG_BYTES:
        raise ValueError(f"Message too large: {len(body)} bytes")

    header = struct.pack(HEADER_FORMAT, len(body))
    sock.sendall(header + body)


def recv_message(sock) -> dict | None:
    """
    Read one length-prefixed message from *sock*.

    :return: Parsed dict, or None if the connection was closed cleanly.
    :raises: socket errors propagate to caller; json.JSONDecodeError on bad data.
    """
    header = _recv_exact(sock, HEADER_SIZE)
    if header is None:
        return None

    (length,) = struct.unpack(HEADER_FORMAT, header)
    body = _recv_exact(sock, length)
    if body is None:
        return None

    return json.loads(body.decode("utf-8"))


def send_raw(sock, data: bytes) -> None:
    """Send raw bytes (used for boolean responses)."""
    sock.sendall(data)


def recv_raw_line(sock, bufsize: int = 16) -> bytes:
    """Read a short boolean response (b'true' or b'false')."""
    return sock.recv(bufsize)


# ── Internal ──────────────────────────────────────────────────────────────────

def _recv_exact(sock, n: int) -> bytes | None:
    """
    Read exactly *n* bytes from *sock*.
    Returns None if the peer closed the connection before sending anything.
    Raises ConnectionError if the connection drops mid-read.
    """
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            if len(buf) == 0:
                return None
            raise ConnectionError("Connection closed mid-message")
        buf.extend(chunk)
    return bytes(buf)