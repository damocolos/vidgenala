"""Secret management and key encryption for SQLite storage and log redaction."""
import os
import re
from pathlib import Path
from typing import Optional
from cryptography.fernet import Fernet

_KEY_ENV = "NALARKA_SECRET_KEY"
_DEFAULT_KEY_FILE = Path("data") / ".secret_key"
_FERNET_INSTANCE: Optional[Fernet] = None


def get_encryption_key(key_file_path: Optional[Path] = None) -> bytes:
    """Retrieves secret key from env var or key file; generates one if not found."""
    env_key = os.environ.get(_KEY_ENV)
    if env_key:
        return env_key.encode("utf-8").strip()

    target_file = key_file_path or _DEFAULT_KEY_FILE
    if target_file.exists():
        return target_file.read_bytes().strip()

    # Generate new key and save
    target_file.parent.mkdir(parents=True, exist_ok=True)
    new_key = Fernet.generate_key()
    target_file.write_bytes(new_key)
    return new_key


def get_fernet(key_file_path: Optional[Path] = None) -> Fernet:
    global _FERNET_INSTANCE
    if _FERNET_INSTANCE is None or key_file_path is not None:
        key = get_encryption_key(key_file_path)
        inst = Fernet(key)
        if key_file_path is None:
            _FERNET_INSTANCE = inst
        return inst
    return _FERNET_INSTANCE


def encrypt_secret(secret_text: Optional[str], key_file_path: Optional[Path] = None) -> Optional[bytes]:
    """Encrypts a plaintext secret into bytes."""
    if not secret_text:
        return None
    f = get_fernet(key_file_path)
    return f.encrypt(secret_text.encode("utf-8"))


def decrypt_secret(secret_bytes: Optional[bytes], key_file_path: Optional[Path] = None) -> Optional[str]:
    """Decrypts encrypted bytes back to plaintext string."""
    if not secret_bytes:
        return None
    f = get_fernet(key_file_path)
    try:
        return f.decrypt(secret_bytes).decode("utf-8")
    except Exception:
        return None


def mask_secret(secret: Optional[str]) -> str:
    """Masks secret for display: sk-...last4 or empty string."""
    if not secret:
        return ""
    if len(secret) <= 8:
        return "********"
    prefix = secret[:3]
    suffix = secret[-4:]
    return f"{prefix}...{suffix}"


_REDACT_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{20,}", re.IGNORECASE),
    re.compile(r"(api[_-]?key[\"'\s:=]+)[\"']?([A-Za-z0-9_\-]{16,})[\"']?", re.IGNORECASE),
]


def redact_sensitive_string(text: str) -> str:
    """Redacts known token patterns and API keys from logs or error traces."""
    if not text:
        return ""
    redacted = text
    for pat in _REDACT_PATTERNS:
        redacted = pat.sub(r"[REDACTED_API_KEY]", redacted)
    return redacted


def redact_secrets(text: str, known_secrets: Optional[list] = None) -> str:
    """Redacts known secrets as well as regex patterns."""
    if not text:
        return ""
    out = text
    if known_secrets:
        for s in known_secrets:
            if s and len(s) >= 4:
                out = out.replace(s, "[REDACTED]")
    return redact_sensitive_string(out)
