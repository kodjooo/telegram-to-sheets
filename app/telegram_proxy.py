import os
from typing import Any

try:
    import socks
except ImportError:  # pragma: no cover
    socks = None


def _parse_proxy_value(proxy_value: str | None):
    if not proxy_value:
        return None

    value = proxy_value.strip()
    if not value:
        return None

    username = None
    password = None

    # Форматы:
    #   host:port
    #   host:port@user:pass
    #   socks5://user:pass@host:port
    if "://" in value:
        scheme, rest = value.split("://", 1)
        if scheme.lower() not in {"socks5", "socks4", "http"}:
            raise ValueError(f"Unsupported proxy scheme: {scheme}")
        mode = {
            "socks5": socks.SOCKS5 if socks else 2,
            "socks4": socks.SOCKS4 if socks else 1,
            "http": socks.HTTP if socks else 3,
        }[scheme.lower()]
        # URI form: scheme://user:pass@host:port
        if "@" in rest:
            auth_part, host_part = rest.split("@", 1)
            if ":" not in auth_part:
                raise ValueError("Proxy auth must be in user:pass format")
            username, password = auth_part.split(":", 1)
        else:
            host_part = rest
    else:
        mode = socks.HTTP if socks else 3
        # Legacy form: host:port@user:pass
        if "@" in value:
            host_part, auth_part = value.split("@", 1)
            if ":" not in auth_part:
                raise ValueError("Proxy auth must be in user:pass format")
            username, password = auth_part.split(":", 1)
        else:
            host_part = value

    if ":" not in host_part:
        raise ValueError("Proxy host must be in host:port format")

    host, port_str = host_part.rsplit(":", 1)
    if username or password:
        return (mode, host, int(port_str), True, username, password)
    return (mode, host, int(port_str), True)


def get_telegram_proxy(config: dict[str, Any] | None = None):
    candidates = [
        os.getenv("TELEGRAM_PROXY"),
        os.getenv("TELEGRAM_PROXY_URL"),
    ]
    if config:
        candidates.extend(
            [
                config.get("telegram_proxy"),
                config.get("proxy"),
                config.get("telegram_proxy_url"),
            ]
        )

    for candidate in candidates:
        if candidate:
            return _parse_proxy_value(candidate)
    return None
