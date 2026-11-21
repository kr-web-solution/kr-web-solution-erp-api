"""Indian-currency amount in words from paise (integer)."""

from __future__ import annotations

_ONES = [
    "",
    "One",
    "Two",
    "Three",
    "Four",
    "Five",
    "Six",
    "Seven",
    "Eight",
    "Nine",
    "Ten",
    "Eleven",
    "Twelve",
    "Thirteen",
    "Fourteen",
    "Fifteen",
    "Sixteen",
    "Seventeen",
    "Eighteen",
    "Nineteen",
]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _two_digits(n: int) -> str:
    n = abs(n) % 100
    if n < 20:
        return _ONES[n]
    return f"{_TENS[n // 10]}{_ONES[n % 10] and ' ' + _ONES[n % 10] or ''}".strip()


def _three_digits(n: int) -> str:
    n = abs(n) % 1000
    if n < 100:
        return _two_digits(n)
    hundred = _ONES[n // 100]
    rest = _two_digits(n % 100)
    return f"{hundred} Hundred" + (f" {rest}" if rest else "")


def amount_in_words_inr(paise: int) -> str:
    """Return e.g. 'Rupees Three Thousand Only' or with paise when non-zero."""
    sign = "Minus " if paise < 0 else ""
    total = abs(int(paise or 0))
    rupees, paise_part = divmod(total, 100)

    if rupees == 0 and paise_part == 0:
        return "Rupees Zero Only"

    parts: list[str] = []
    crore, rem = divmod(rupees, 10000000)
    lakh, rem = divmod(rem, 100000)
    thousand, rem = divmod(rem, 1000)
    if crore:
        parts.append(f"{_three_digits(crore)} Crore")
    if lakh:
        parts.append(f"{_two_digits(lakh)} Lakh")
    if thousand:
        parts.append(f"{_two_digits(thousand)} Thousand")
    if rem:
        parts.append(_three_digits(rem))

    words = " ".join(p for p in parts if p).strip() or "Zero"
    out = f"{sign}Rupees {words}"
    if paise_part:
        out += f" and {_two_digits(paise_part)} Paise"
    return out + " Only"
