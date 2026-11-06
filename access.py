"""Normalize the exact IPv4 peers permitted in restricted mode."""
import ipaddress
import re


def allowed_addresses(values):
    if values is None:
        return None
    if isinstance(values, str):
        values = re.split(r'[\s,]+', values.strip())
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError('Enter at least one allowed IPv4 address.')
    addresses = set()
    for value in values:
        if not isinstance(value, str):
            raise ValueError('Allowed clients must be IPv4 addresses.')
        address = ipaddress.IPv4Address(value)
        if address.is_unspecified or address.is_multicast or int(address) == 0xffffffff:
            raise ValueError('Enter individual client IPv4 addresses.')
        addresses.add(address)
    return tuple(str(address) for address in sorted(addresses))
