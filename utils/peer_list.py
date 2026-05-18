"""
Parse the node-list file.

Each non-empty line has the format:  host:port
"""

import sys


def parse_peer_list(filepath: str) -> list[tuple[str, int]]:
    """
    Read *filepath* and return a list of (host, port) tuples.
    Exits the process with an error message on any parse failure.
    """
    peers = []
    try:
        with open(filepath, "r") as fh:
            for lineno, raw in enumerate(fh, 1):
                line = raw.strip()
                if not line:
                    continue
                parts = line.rsplit(":", 1)
                if len(parts) != 2:
                    sys.exit(f"Bad peer line {lineno} in {filepath!r}: {line!r}")
                host, port_str = parts
                try:
                    port = int(port_str)
                except ValueError:
                    sys.exit(
                        f"Non-integer port on line {lineno} in {filepath!r}: {port_str!r}"
                    )
                peers.append((host.strip(), port))
    except FileNotFoundError:
        sys.exit(f"Peer list file not found: {filepath!r}")
    return peers