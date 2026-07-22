import hashlib
import hmac
import ipaddress
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher(time_cost=3, memory_cost=65_536, parallelism=4)


def hash_password(value: str) -> str:
    return _hasher.hash(value)


def verify_password(encoded: str, value: str) -> bool:
    try:
        return _hasher.verify(encoded, value)
    except VerifyMismatchError:
        return False


def issue_api_key() -> tuple[str, str, str]:
    prefix = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    return f"gpc_{prefix}_{secret}", prefix, secret


def hash_api_secret(secret: str, pepper: str) -> str:
    return hmac.new(pepper.encode(), secret.encode(), hashlib.sha256).hexdigest()


def verify_api_key(encoded_hash: str, secret: str, pepper: str) -> bool:
    return hmac.compare_digest(encoded_hash, hash_api_secret(secret, pepper))


def derive_callback_secret(callback_id: str, master_secret: str) -> str:
    """Derive a stable per-callback secret without persisting plaintext."""
    return hmac.new(
        master_secret.encode(), f"callback:{callback_id}".encode(), hashlib.sha256
    ).hexdigest()


def sign_callback_payload(body: bytes, timestamp: str, secret: str) -> str:
    message = timestamp.encode() + b"." + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def create_access_token(subject: str, role: str, secret: str, ttl_seconds: int = 900) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": subject, "role": role, "iat": now, "exp": now + ttl_seconds},
        secret,
        algorithm="HS256",
    )


@dataclass(frozen=True)
class SignedRequest:
    timestamp: str
    nonce: str
    signature: str


def sign_agent_request(
    method: str, path: str, body: bytes, timestamp: str, nonce: str, secret: str
) -> str:
    digest = hashlib.sha256(body).hexdigest()
    message = "\n".join((method.upper(), path, timestamp, nonce, digest))
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def validate_callback_url(url: str, allowed_hosts: set[str]) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return False
    host = parsed.hostname.lower().rstrip(".")
    if host not in allowed_hosts:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (
        address.is_private or address.is_loopback or address.is_link_local or address.is_reserved
    )
