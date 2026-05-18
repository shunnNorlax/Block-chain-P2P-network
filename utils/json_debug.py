"""
json_debug.py - Structured JSON debug logging for test environments
"""

import json
import sys
from datetime import datetime

from utils.debug_flags import is_debug_enabled

def log_debug(event_type, message="", **kwargs):
    """
    Log a debug event in JSON format to stderr (for test capture).
    
    Args:
        event_type: Event category (STARTUP, TRANSACTION, CONSENSUS, etc.)
        message: Human-readable message
        **kwargs: Additional structured data
    """
    if not is_debug_enabled():
        return

    debug_obj = {
        "timestamp": datetime.now().isoformat(),
        "event": event_type,
        "message": message,
        **kwargs
    }
    
    # Send to stderr so it doesn't interfere with stdout JSON output
    print(json.dumps(debug_obj, separators=(',', ':')), file=sys.stderr, flush=True)

def log_transaction_event(status, reason="", **tx_info):
    """Log transaction-related event"""
    # Handle 'message' field specially since it conflicts with log_debug's message param
    tx_message = tx_info.pop('message', '')
    log_debug(
        "TRANSACTION",
        f"Transaction {status}",
        status=status,
        reason=reason,
        tx_message=tx_message,  # Rename to avoid conflict
        **tx_info
    )

def log_consensus_event(round_num, status, **info):
    """Log consensus-related event"""
    log_debug(
        "CONSENSUS",
        f"Consensus Round {round_num}: {status}",
        round=round_num,
        status=status,
        **info
    )

def log_block_event(block_index, status, **info):
    """Log block-related event"""
    log_debug(
        "BLOCK",
        f"Block {block_index} {status}",
        block_index=block_index,
        status=status,
        **info
    )

def log_network_event(peer_host, peer_port, status, **info):
    """Log network-related event"""
    log_debug(
        "NETWORK",
        f"Peer {peer_host}:{peer_port} {status}",
        peer=f"{peer_host}:{peer_port}",
        status=status,
        **info
    )

def log_startup_event(node_port, peers_count):
    """Log node startup"""
    log_debug(
        "STARTUP",
        f"Node starting on port {node_port}",
        port=node_port,
        peers=peers_count
    )

def log_pool_event(action, tx_count, reason=""):
    """Log pool state changes"""
    log_debug(
        "POOL",
        f"Pool {action}",
        action=action,
        tx_count=tx_count,
        reason=reason
    )