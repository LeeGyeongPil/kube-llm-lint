"""Parse Kubernetes resource quantities (``2Gi``, ``1G``, ``1536Mi``) into bytes."""

from __future__ import annotations

import re
from fractions import Fraction

_BINARY = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4, "Pi": 1024**5, "Ei": 1024**6}
_DECIMAL = {
    "": 1,
    "k": 10**3,
    "M": 10**6,
    "G": 10**9,
    "T": 10**12,
    "P": 10**15,
    "E": 10**18,
    "m": Fraction(1, 1000),
}
_PATTERN = re.compile(r"^([+-]?(?:\d+\.?\d*|\.\d+))([A-Za-z]*)$")


def parse_quantity(value: object) -> int:
    """Return the quantity as a whole number of bytes.

    Accepts ints, floats and strings such as ``"2Gi"``, ``"1G"``, ``"1.5Gi"``.
    Raises ``ValueError`` for anything that is not a Kubernetes quantity.
    """
    if isinstance(value, bool):
        raise ValueError(f"not a quantity: {value!r}")
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    match = _PATTERN.match(text)
    if not match:
        raise ValueError(f"not a Kubernetes quantity: {text!r}")
    number = Fraction(match.group(1))
    suffix = match.group(2)
    if suffix in _BINARY:
        factor = _BINARY[suffix]
    elif suffix in _DECIMAL:
        factor = _DECIMAL[suffix]
    else:
        raise ValueError(f"unknown quantity suffix {suffix!r} in {text!r}")
    return int(number * factor)
