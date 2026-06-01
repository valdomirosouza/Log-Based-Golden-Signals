import ipaddress
import re

_IPV4_RE = re.compile(
    r"^(\d{1,3}\.\d{1,3}\.\d{1,3})\.\d{1,3}$"
)


def mask_ip(ip: str) -> str:
    """Mask last octet of IPv4 or last 80 bits of IPv6."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip

    if isinstance(addr, ipaddress.IPv4Address):
        m = _IPV4_RE.match(ip)
        if m:
            return f"{m.group(1)}.xxx"
        return ip

    # IPv6: zero the last 80 bits (last 5 groups of 16 bits).
    # IPv6 is 128 bits; keep top 48 bits, mask bottom 80.
    int_val = int(addr)
    mask = ((1 << 48) - 1) << 80
    masked_int = int_val & mask
    return str(ipaddress.IPv6Address(masked_int))
