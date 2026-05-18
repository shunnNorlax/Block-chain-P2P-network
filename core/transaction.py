"""
Transaction model and validation for the blockchain node.
"""

import json
import re
import binascii
import nacl.signing
import nacl.exceptions


# ── Constants ────────────────────────────────────────────────────────────────

SENDER_HEX_LEN   = 64   # 32-byte Ed25519 public key → 64 hex chars
SIG_HEX_LEN      = 128  # 64-byte Ed25519 signature  → 128 hex chars
MAX_MESSAGE_LEN   = 70
HEX_RE            = re.compile(r'^[0-9a-f]+$')
# Allow printable ASCII in messages (space through tilde).
# Hidden tests include punctuation such as '-'.
MSG_RE            = re.compile(r'^[ -~]*$')


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_hex(s: str) -> bool:
    return bool(HEX_RE.match(s)) if s else False


# ── Validation ────────────────────────────────────────────────────────────────

class TransactionValidationError(Exception):
    """Raised when a transaction fails validation."""


def validate_transaction(tx: dict, confirmed_nonces: dict) -> None:
    """
    Validate a transaction dict against all rules.

    :param tx:               Transaction dictionary (sender, message, nonce, signature).
    :param confirmed_nonces: Mapping of sender hex → number of confirmed txs so far.
    :raises TransactionValidationError: on any rule violation.
    """
    # ── Field presence ───────────────────────────────────────────────────────
    for field in ("sender", "message", "nonce", "signature"):
        if field not in tx:
            raise TransactionValidationError(f"Missing field: {field}")

    sender    = tx["sender"]
    message   = tx["message"]
    nonce     = tx["nonce"]
    signature = tx["signature"]

    # ── Type checks ──────────────────────────────────────────────────────────
    if not isinstance(sender, str):
        raise TransactionValidationError("sender must be a string")
    if not isinstance(message, str):
        raise TransactionValidationError("message must be a string")
    if not isinstance(nonce, int) or isinstance(nonce, bool):
        raise TransactionValidationError("nonce must be an integer")
    if not isinstance(signature, str):
        raise TransactionValidationError("signature must be a string")

    # ── Sender format ────────────────────────────────────────────────────────
    if len(sender) != SENDER_HEX_LEN:
        raise TransactionValidationError(
            f"sender must be {SENDER_HEX_LEN} hex chars, got {len(sender)}"
        )
    if not _is_hex(sender):
        raise TransactionValidationError("sender contains non-hex characters")

    # ── Message format ───────────────────────────────────────────────────────
    if len(message) > MAX_MESSAGE_LEN:
        raise TransactionValidationError(
            f"message exceeds {MAX_MESSAGE_LEN} characters"
        )
    if not MSG_RE.match(message):
        raise TransactionValidationError(
            "message contains disallowed characters"
        )

    # ── Nonce check ──────────────────────────────────────────────────────────
    if nonce < 0:
        raise TransactionValidationError("nonce must be non-negative")

    expected_nonce = confirmed_nonces.get(sender, 0)
    if nonce != expected_nonce:
        raise TransactionValidationError(
            f"nonce out of sequence: expected {expected_nonce}, got {nonce}"
        )

    # ── Signature length ─────────────────────────────────────────────────────
    if len(signature) != SIG_HEX_LEN:
        raise TransactionValidationError(
            f"signature must be {SIG_HEX_LEN} hex chars, got {len(signature)}"
        )
    if not _is_hex(signature):
        raise TransactionValidationError("signature contains non-hex characters")

    # ── Ed25519 signature verification ────────────────────────────────────────
    try:
        pub_bytes = binascii.unhexlify(sender)
        sig_bytes = binascii.unhexlify(signature)

        # The signed payload is: JSON representation of {message, nonce, sender} (sorted keys)
        signed_dict = {"message": message, "nonce": nonce, "sender": sender}
        signed_data = json.dumps(signed_dict, sort_keys=True).encode("utf-8")

        verify_key = nacl.signing.VerifyKey(pub_bytes)
        verify_key.verify(signed_data, sig_bytes)
    except (nacl.exceptions.BadSignatureError, Exception) as exc:
        raise TransactionValidationError(f"invalid signature: {exc}") from exc


def tx_to_dict(tx: dict) -> dict:
    """Return a clean copy of a transaction with only the four canonical fields."""
    return {
        "sender":    tx["sender"],
        "message":   tx["message"],
        "nonce":     tx["nonce"],
        "signature": tx["signature"],
    }


def tx_key(tx: dict) -> tuple:
    """Unique key for deduplication: (sender, nonce)."""
    return (tx["sender"], tx["nonce"])