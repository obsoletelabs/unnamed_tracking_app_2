"""Canonical effective client identity after the ASGI server's trusted-proxy boundary."""

from ipaddress import IPv6Address, ip_address

from fastapi import Request


def request_ip(request: Request) -> str | None:
    """Never independently trust forwarding headers supplied by a caller."""
    if request.client is None:
        return None
    try:
        address = ip_address(request.client.host)
    except ValueError:
        return None
    if isinstance(address, IPv6Address):
        return str(address.ipv4_mapped or IPv6Address(address.packed))
    return str(address)
