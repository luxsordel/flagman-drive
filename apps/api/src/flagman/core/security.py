import hashlib
import hmac


def keyed_hash(value: str, pepper: str) -> str:
    """HMAC-SHA256 for low-entropy values (codes, phones, IP addresses): a plain hash of them
    is brute-forced in seconds, while without the pepper a leaked hash is useless."""
    return hmac.new(pepper.encode(), value.encode(), hashlib.sha256).hexdigest()
