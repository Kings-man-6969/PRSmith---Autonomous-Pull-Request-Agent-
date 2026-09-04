"""Security boundaries: SSRF prevention, URL validation, and secret masking."""

import ipaddress
import re
import socket
from urllib.parse import urlparse
from typing import Dict, Optional, Set

from backend.llm.errors import SSRFBlockedError

# Prohibited private / link-local / metadata network ranges
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),      # Loopback
    ipaddress.ip_network("10.0.0.0/8"),       # RFC 1918 Private
    ipaddress.ip_network("172.16.0.0/12"),    # RFC 1918 Private
    ipaddress.ip_network("192.168.0.0/16"),   # RFC 1918 Private
    ipaddress.ip_network("169.254.0.0/16"),   # Link-local / AWS / GCP metadata
    ipaddress.ip_network("::1/128"),          # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),         # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),        # IPv6 link-local
]

_SENSITIVE_HEADER_KEYS: Set[str] = {
    "authorization",
    "x-api-key",
    "api-key",
    "x-auth-token",
    "proxy-authorization",
    "cookie",
}


class SSRFValidator:
    """Validates custom endpoints to prevent Server-Side Request Forgery."""

    @staticmethod
    def validate_url(url: str, allow_local: bool = False) -> str:
        """
        Validate that the target URL uses http/https and does not resolve
        to private, loopback, or cloud metadata IP addresses.
        """
        if not url or not url.strip():
            raise SSRFBlockedError("Base URL cannot be empty.", url=url)

        parsed = urlparse(url.strip())
        if parsed.scheme not in ("http", "https"):
            raise SSRFBlockedError(
                f"Disallowed URL scheme: '{parsed.scheme}'. Only 'http' and 'https' are permitted.",
                url=url,
            )

        hostname = parsed.hostname
        if not hostname:
            raise SSRFBlockedError("URL must contain a valid hostname.", url=url)

        # In dev mode, allow localhost if explicitly enabled
        if allow_local and hostname in ("localhost", "127.0.0.1", "::1", "host.docker.internal"):
            return url.rstrip("/")

        # Check explicit hostname patterns
        if hostname.lower() in ("localhost", "169.254.169.254", "metadata.google.internal"):
            raise SSRFBlockedError(f"Prohibited hostname: '{hostname}'.", url=url)

        # Resolve IP addresses for hostname and check against blocked subnets
        try:
            addr_info = socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
            for family, socktype, proto, canonname, sockaddr in addr_info:
                ip_str = sockaddr[0]
                ip = ipaddress.ip_address(ip_str)
                for net in _BLOCKED_NETWORKS:
                    if ip in net:
                        raise SSRFBlockedError(
                            f"Host '{hostname}' resolved to prohibited IP address '{ip_str}' in range '{net}'.",
                            url=url,
                        )
        except socket.gaierror as e:
            raise SSRFBlockedError(f"DNS resolution failed for host '{hostname}': {e}", url=url)

        return url.rstrip("/")


def mask_secret(secret: Optional[str]) -> str:
    """Mask sensitive credentials for safe logging (e.g. 'sk-proj-12...99' -> 'sk-***99')."""
    if not secret:
        return ""
    s = secret.strip()
    if len(s) <= 8:
        return "***"
    return f"{s[:3]}***{s[-4:]}"


def sanitize_headers(headers: Dict[str, str]) -> Dict[str, str]:
    """Return a sanitized copy of HTTP headers with all secrets masked for logging."""
    sanitized = {}
    for k, v in headers.items():
        if k.lower() in _SENSITIVE_HEADER_KEYS:
            sanitized[k] = mask_secret(v)
        else:
            sanitized[k] = v
    return sanitized
