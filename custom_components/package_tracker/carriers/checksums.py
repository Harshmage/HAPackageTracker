"""Check-digit validators for the tracking-number formats we recognise.

Format detection alone cannot tell a FedEx number from a random 12-digit order id; a
check digit can. Each validator takes an already-normalized string (uppercase, no
separators), returns a plain bool, and never raises: callers feed them arbitrary text
fragments during extraction, so malformed input is simply "not valid".

Every algorithm here was verified against the vectors in tests/unit/test_checksums.py.
"""


def _digits(value: str) -> list[int] | None:
    if not value or not value.isascii() or not value.isdigit():
        return None
    return [int(c) for c in value]


def mod10_31_check(serial: str) -> int | None:
    """Return the GS1-style mod-10 check digit for ``serial`` (weights 3,1,… from the right)."""
    digits = _digits(serial)
    if digits is None:
        return None
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(digits)))
    return (10 - total % 10) % 10


def mod10_31(number: str) -> bool:
    """Validate ``number`` whose last digit is a mod-10 (3,1) check digit over the rest."""
    if len(number) < 2:
        return False
    check = mod10_31_check(number[:-1])
    return check is not None and number[-1].isdigit() and check == int(number[-1])


def ups_1z(number: str) -> bool:
    """Validate a UPS ``1Z`` number (18 chars).

    The body is ``[2:17]``; letters map to ``(ord - ord('A') + 2) % 10``; values at odd
    0-based positions are doubled *without* digit-summing (unlike Luhn, which is the bug
    the old classifier had).
    """
    if len(number) != 18 or not number.startswith("1Z") or not number.isascii():
        return False
    check_char = number[17]
    if not check_char.isdigit():
        return False
    total = 0
    for i, ch in enumerate(number[2:17]):
        if ch.isdigit():
            value = int(ch)
        elif "A" <= ch <= "Z":
            value = (ord(ch) - ord("A") + 2) % 10
        else:
            return False
        total += value * 2 if i % 2 else value
    return (10 - total % 10) % 10 == int(check_char)


def usps_impb(number: str) -> bool:
    """Validate a USPS IMpb (22/26/30 digits) or a legacy 20-digit USPS number.

    20-digit numbers are printed without the leading ``91`` application identifier, so
    they are accepted if either the bare serial or ``"91" + serial`` checks out.
    """
    if _digits(number) is None:
        return False
    if mod10_31(number):
        return True
    return len(number) == 20 and mod10_31("91" + number)


def fedex_mod11(number: str) -> bool:
    """Validate a 12-digit FedEx Express number: weights 3,1,7 left to right over 11 digits."""
    digits = _digits(number)
    if digits is None or len(digits) != 12:
        return False
    weights = (3, 1, 7)
    total = sum(d * weights[i % 3] for i, d in enumerate(digits[:11]))
    return total % 11 % 10 == digits[11]


def fedex_15(number: str) -> bool:
    """Validate a 15-digit FedEx Ground number (mod-10, weights 3,1 from the right)."""
    return len(number) == 15 and mod10_31(number)


def fedex_96(number: str) -> bool:
    """Validate a 22-digit FedEx Ground ``96…`` barcode: mod-10 over serial ``[7:21]``."""
    if len(number) != 22 or _digits(number) is None:
        return False
    return mod10_31(number[7:22])


def sscc(number: str) -> bool:
    """Validate a 20-digit ``00`` + SSCC-18 barcode (mod-10 over the SSCC body)."""
    if len(number) != 20 or not number.startswith("00"):
        return False
    return mod10_31(number[2:])


def dhl_mod7(number: str) -> bool:
    """Validate a 10- or 11-digit DHL Express waybill: ``int(serial) % 7 == check``."""
    digits = _digits(number)
    if digits is None or len(digits) not in (10, 11):
        return False
    return int(number[:-1]) % 7 == digits[-1]


_S10_WEIGHTS = (8, 6, 4, 2, 3, 5, 9, 7)


def s10(number: str) -> bool:
    """Validate the check digit of a UPU S10 item id (``AA123456785US``).

    ``11 - sum % 11`` with 10 mapping to 0 and 11 mapping to 5.
    """
    if len(number) != 13:
        return False
    digits = _digits(number[2:11])
    if digits is None:
        return False
    total = sum(d * w for d, w in zip(digits[:8], _S10_WEIGHTS, strict=True))
    check = 11 - total % 11
    if check == 10:
        check = 0
    elif check == 11:
        check = 5
    return check == digits[8]


def ontrac(number: str) -> bool:
    """Validate a 15-char OnTrac ``C``/``D`` number.

    The leading letter becomes a digit (C→4, D→5); values at odd 0-based positions are
    doubled without digit-summing, then ``(10 - sum % 10) % 10``.
    """
    if len(number) != 15 or number[0] not in "CD":
        return False
    body = ("4" if number[0] == "C" else "5") + number[1:14]
    digits = _digits(body)
    if digits is None or not number[14].isdigit():
        return False
    total = sum(d * 2 if i % 2 else d for i, d in enumerate(digits))
    return (10 - total % 10) % 10 == int(number[14])
