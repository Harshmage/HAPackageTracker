"""Check-digit algorithms against known-good and known-bad vectors.

The old classifier shipped a wrong UPS checksum that nobody noticed because nothing
tested it; every algorithm here is pinned to real vectors plus mutations that must fail.
"""

import pytest

from custom_components.package_tracker.carriers import checksums

VALID = [
    ("ups_1z", "1Z999AA10123456784"),
    ("ups_1z", "1Z5R89390357567127"),
    ("ups_1z", "1Z12345E6605272234"),
    ("usps_impb", "9400109699939938223564"),
    ("usps_impb", "9205590164917312751089"),
    ("usps_impb", "9101026837331000039521"),
    ("usps_impb", "9261290113185417468510"),
    ("usps_impb", "92748931507708513018050063"),
    ("usps_impb", "03071790000523483741"),
    ("usps_impb", "71969010756003077385"),
    ("fedex_mod11", "986578788855"),
    ("fedex_mod11", "477179081230"),
    ("fedex_mod11", "776632517510"),
    ("fedex_15", "041441760228964"),
    ("fedex_96", "9611020987654312345672"),
    ("dhl_mod7", "3318810025"),
    ("dhl_mod7", "73891051146"),
    ("ontrac", "C11031500001879"),
    ("ontrac", "D10011354453707"),
    ("s10", "EA123456785US"),
    ("s10", "RR123456785GB"),
    ("s10", "CP123456785CA"),
    ("s10", "RR123456785DE"),
    ("s10", "LX123456785CN"),
]

INVALID = [
    ("ups_1z", "1Z999AA10123456780"),
    ("ups_1z", "1Z12345E0205271688"),  # UPS sandbox number: format-valid, check-invalid
    ("fedex_mod11", "996578788855"),
    ("ontrac", "C11031500001878"),
    ("dhl_mod7", "3318810010"),
    ("s10", "ID123456789IN"),
]

# 20-digit USPS numbers accept two check digits (bare serial or "91" + serial).
_DUAL_CHECK = {"03071790000523483741", "71969010756003077385"}


def _check_index(func: str, number: str) -> int:
    return 10 if func == "s10" else len(number) - 1


@pytest.mark.parametrize(("func", "number"), VALID)
def test_valid_vectors(func: str, number: str) -> None:
    assert getattr(checksums, func)(number) is True


@pytest.mark.parametrize(("func", "number"), INVALID)
def test_invalid_vectors(func: str, number: str) -> None:
    assert getattr(checksums, func)(number) is False


@pytest.mark.parametrize(("func", "number"), [v for v in VALID if v[1] not in _DUAL_CHECK])
def test_check_digit_mutations_fail(func: str, number: str) -> None:
    idx = _check_index(func, number)
    for digit in "0123456789":
        if digit == number[idx]:
            continue
        mutated = number[:idx] + digit + number[idx + 1 :]
        assert getattr(checksums, func)(mutated) is False, mutated


# FedEx's "% 11 % 10" folds remainders 0 and 10 together, so for this vector a +1 bump
# of the penultimate digit lands on the other remainder and still passes (inherent to
# the algorithm, not a bug).
_MOD11_COLLISION = {"776632517510"}


@pytest.mark.parametrize(("func", "number"), [v for v in VALID if v[1] not in _MOD11_COLLISION])
def test_body_digit_mutation_fails(func: str, number: str) -> None:
    """Bumping the digit right before the check digit by one must be detected."""
    idx = _check_index(func, number) - 1
    bumped = str((int(number[idx]) + 1) % 10)
    mutated = number[:idx] + bumped + number[idx + 1 :]
    assert getattr(checksums, func)(mutated) is False, mutated


@pytest.mark.parametrize("number", sorted(_DUAL_CHECK))
def test_usps_20_accepts_exactly_two_check_digits(number: str) -> None:
    passing = [d for d in "0123456789" if checksums.usps_impb(number[:-1] + d)]
    assert number[-1] in passing
    assert len(passing) == 2


def test_mod10_31_check_digit() -> None:
    assert checksums.mod10_31_check("940010969993993822356") == 4
    assert checksums.mod10_31_check("12A") is None
    assert checksums.mod10_31_check("") is None


@pytest.mark.parametrize(
    "func",
    [
        *("ups_1z", "usps_impb", "fedex_mod11", "fedex_15", "fedex_96"),
        *("sscc", "dhl_mod7", "s10", "ontrac", "mod10_31"),
    ],
)
@pytest.mark.parametrize(
    "junk",
    [
        "",
        "1",
        "1Z",
        "ABCDEFGHIJKLMNOP",
        "\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18\uff19\uff10",
        "12 34",
    ],
)
def test_junk_never_raises(func: str, junk: str) -> None:
    assert getattr(checksums, func)(junk) is False


def test_ups_letters_map_per_spec() -> None:
    # "A" -> 2, so swapping a digit 2 for an "A" at the same position keeps it valid.
    assert checksums.ups_1z("1Z999AA10123456784")
    assert checksums.ups_1z("1Z9992A10123456784")
