"""Carrier detection, deep links and tracking-number extraction.

Pure standard library on purpose: no Home Assistant imports anywhere in this package,
so it can be unit-tested (and reasoned about) without an event loop, and so the
integration needs no pip requirements.
"""

from .classifier import Candidate, Policy, canonicalize, classify, normalize
from .extract import ExtractResult, extract
from .registry import CARRIERS, Carrier, Links, build_links, carrier_name, get_carrier

__all__ = [
    "CARRIERS",
    "Candidate",
    "Carrier",
    "ExtractResult",
    "Links",
    "Policy",
    "build_links",
    "canonicalize",
    "carrier_name",
    "classify",
    "extract",
    "get_carrier",
    "normalize",
]
