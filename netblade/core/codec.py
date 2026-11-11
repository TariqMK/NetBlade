"""Encode and decode text: Base64 (standard and URL-safe), URL percent-encoding, hex, and JWT viewing.

Decoded bytes that are not readable text are shown as a hex dump. Nothing here touches the network.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import json
import re
from dataclasses import dataclass
from enum import Enum
from urllib.parse import quote, unquote_to_bytes

from netblade.core.multiping import format_duration


class Format(Enum):
    BASE64 = "Base64"
    URL = "URL"
    HEX = "Hex"
    JWT = "JWT"


class CodecError(ValueError):
    """The input can't be decoded. The message is written for the user."""


MAX_INPUT_CHARS = 10_000_000
# Bytes shown in a hex dump. More would make the output box slow.
HEXDUMP_LIMIT = 65_536
HEXDUMP_WIDTH = 16

_B64_CHARS = re.compile(r"[A-Za-z0-9+/\-_=]")
_HEX_SEPARATORS = re.compile(r"0x|\\x|[\s:,\-]", re.IGNORECASE)
_PEM = re.compile(r"-----BEGIN ([A-Z0-9 ]+)-----(.*?)-----END \1-----", re.DOTALL)
_JWT = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*$")
_SCHEME = re.compile(r"^(?:authorization:\s*)?(basic|bearer)\s+", re.IGNORECASE)
_PERCENT = re.compile(r"%[0-9A-Fa-f]{2}")
_TIME_CLAIMS = {"iat": "issued", "nbf": "valid from", "exp": "expires"}


@dataclass
class Result:
    text: str  # What the output box shows.
    note: str  # One status line, for example "12 bytes · UTF-8 text".
    binary: bool = False  # True when text is a hex dump, not the decoded text itself.


# Bytes to readable text


def bytes_to_text(data: bytes) -> tuple[str | None, str]:
    """The bytes as text and a label for it, or None when they are binary data."""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    utf16 = None
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        utf16 = "utf-16"  # The byte order mark says which order.
    elif _looks_utf16le(data):
        utf16 = "utf-16-le"
    if utf16:
        try:
            text = data.decode(utf16)
        except UnicodeDecodeError:
            text = None
        if text is not None and _readable(text):
            return text, "UTF-16 text"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None, "binary data"
    return (text, "UTF-8 text") if _readable(text) else (None, "binary data")


def _looks_utf16le(data: bytes) -> bool:
    """PowerShell -EncodedCommand is UTF-16 LE: mostly ASCII, so every second byte is zero."""
    if len(data) < 4 or len(data) % 2:
        return False
    high = data[1::2]
    return high.count(0) >= 0.9 * len(high) and 0 not in data[0::2]


def _readable(text: str) -> bool:
    return not any(
        ord(ch) < 32 and ch not in "\t\r\n" or ord(ch) == 0x7F for ch in text
    )


def hexdump(data: bytes, limit: int = HEXDUMP_LIMIT) -> str:
    """Offset, hex bytes and printable ASCII, 16 bytes per line."""
    lines = []
    for offset in range(0, min(len(data), limit), HEXDUMP_WIDTH):
        chunk = data[offset : offset + HEXDUMP_WIDTH]
        hex_part = " ".join(f"{b:02x}" for b in chunk[:8])
        if len(chunk) > 8:
            hex_part += "  " + " ".join(f"{b:02x}" for b in chunk[8:])
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{offset:08x}  {hex_part:<49}  {ascii_part}")
    return "\n".join(lines)


def _bytes_result(data: bytes, notes: list[str]) -> Result:
    text, label = bytes_to_text(data)
    size = f"{len(data):,} byte" + ("" if len(data) == 1 else "s")
    if text is not None:
        return Result(text, " · ".join([size, label, *notes]))
    if len(data) > HEXDUMP_LIMIT:
        notes = [*notes, f"first {HEXDUMP_LIMIT:,} bytes shown"]
    return Result(
        hexdump(data),
        " · ".join([size, "binary data, shown as a hex dump", *notes]),
        binary=True,
    )


# Base64


def _clean_base64(text: str, notes: list[str]) -> str:
    match = _PEM.search(text)
    if match:
        notes.append(f"PEM {match.group(1).lower()} armour removed")
        text = match.group(2)
        # Encrypted PEM keys can have header lines such as "Proc-Type: 4,ENCRYPTED" before the data.
        text = "\n".join(line for line in text.splitlines() if ":" not in line)
    else:
        scheme = _SCHEME.match(text.strip())
        if scheme and scheme.group(1).lower() == "basic":
            notes.append("Basic auth header, shown as user:password")
            text = text.strip()[scheme.end() :]
    return "".join(text.split())


def base64_decode(text: str) -> Result:
    notes: list[str] = []
    data = _clean_base64(text, notes)
    if not data:
        raise CodecError("There is no Base64 data to decode.")
    for position, ch in enumerate(data, start=1):
        if not _B64_CHARS.fullmatch(ch):
            raise CodecError(
                f'"{ch}" at position {position:,} is not a Base64 character.'
            )
    body = data.rstrip("=")
    if "=" in body:
        raise CodecError(
            f"Padding (=) appears inside the data, at position {body.index('=') + 1:,}."
        )
    standard, url_safe = bool(re.search(r"[+/]", body)), bool(re.search(r"[-_]", body))
    if standard and url_safe:
        raise CodecError(
            "The text mixes standard (+ /) and URL-safe (- _) Base64 characters."
        )
    if len(body) % 4 == 1:
        raise CodecError(
            "The Base64 data is incomplete: it has one character too many or is cut short."
        )
    if url_safe:
        notes.append("URL-safe alphabet")
    padding = -len(body) % 4
    if len(data) - len(body) != padding:
        notes.append(
            "missing padding added" if len(data) == len(body) else "padding corrected"
        )
    altchars = b"-_" if url_safe else None
    try:
        decoded = base64.b64decode(
            body + "=" * padding, altchars=altchars, validate=True
        )
    except binascii.Error as error:
        # Defensive: the checks above catch the known cases.
        raise CodecError(f"The Base64 data is not valid: {error}.") from None
    return _bytes_result(decoded, notes)


def base64_encode(text: str, url_safe: bool = False) -> str:
    data = text.encode("utf-8")
    if url_safe:
        return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")
    return base64.b64encode(data).decode("ascii")


# URL percent-encoding


def url_decode(text: str, plus_as_space: bool = True) -> Result:
    notes: list[str] = []
    if plus_as_space:
        text = text.replace("+", " ")
    bad = re.search(r"%(?![0-9A-Fa-f]{2})", text)
    if bad:
        notes.append(
            f"% at position {bad.start() + 1:,} is not followed by two hex digits and is kept"
        )
    return _bytes_result(unquote_to_bytes(text), notes)


def url_encode(text: str) -> str:
    """Encodes every character except letters, digits and - . _ ~, so the result is safe in any URL part."""
    return quote(text, safe="")


# Hex


def hex_decode(text: str) -> Result:
    digits = _HEX_SEPARATORS.sub("", text)
    if not digits:
        raise CodecError("There are no hex digits to decode.")
    bad = re.search(r"[^0-9A-Fa-f]", digits)
    if bad:
        raise CodecError(f'"{bad.group()}" is not a hex digit.')
    if len(digits) % 2:
        raise CodecError(
            f"The hex has an odd number of digits ({len(digits):,}). Each byte needs two."
        )
    return _bytes_result(bytes.fromhex(digits), [])


def hex_encode(text: str) -> str:
    return text.encode("utf-8").hex()


# JWT


def _b64url_json(part: str, name: str) -> dict:
    try:
        raw = base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
        value = json.loads(raw)
    except (binascii.Error, ValueError):
        raise CodecError(f"The {name} is not Base64url-encoded JSON.") from None
    if not isinstance(value, dict):
        raise CodecError(f"The {name} is not a JSON object.")
    return value


def _claim_time(key: str, seconds: object, now: dt.datetime) -> str | None:
    """One line, for example "exp  2026-11-11 13:00:00  expires in 1 h 0 min"."""
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
        return None
    try:
        moment = dt.datetime.fromtimestamp(seconds, dt.UTC)
    except (OverflowError, OSError, ValueError):
        return None
    delta = (moment - now).total_seconds()
    word = _TIME_CLAIMS[key]
    relative = (
        f"{word} in {format_duration(delta)}"
        if delta >= 0
        else f"{word} {format_duration(-delta)} ago"
    )
    return f"{key}  {moment:%Y-%m-%d %H:%M:%S}  {relative}"


def jwt_decode(text: str, now: dt.datetime | None = None) -> Result:
    """Show the header, payload, claim times and signature of a JWT. The signature is not checked."""
    now = now or dt.datetime.now(dt.UTC)
    token = _SCHEME.sub("", text.strip()).strip()
    parts = token.split(".")
    if len(parts) == 5:
        raise CodecError(
            "This is an encrypted token (JWE). Its content can't be read without the key."
        )
    if len(parts) != 3 or not _JWT.match(token):
        raise CodecError(
            "A JWT has three parts separated by dots: header.payload.signature."
        )
    header = _b64url_json(parts[0], "header")
    payload = _b64url_json(parts[1], "payload")
    sections = [
        "Header\n" + json.dumps(header, indent=2, ensure_ascii=False),
        "Payload\n" + json.dumps(payload, indent=2, ensure_ascii=False),
    ]
    times = [_claim_time(key, payload.get(key), now) for key in _TIME_CLAIMS]
    times = [line for line in times if line]
    if times:
        sections.append("Times (UTC)\n" + "\n".join(times))
    signature = parts[2] or "(none)"
    sections.append(f"Signature (not checked)\n{signature}")

    notes = ["JWT", str(header.get("alg", "no alg"))]
    if header.get("alg") == "none" or not parts[2]:
        notes.append("unsigned")
    exp = payload.get("exp")
    if isinstance(exp, (int, float)) and not isinstance(exp, bool):
        remaining = exp - now.timestamp()
        notes.append(
            f"expires in {format_duration(remaining)}"
            if remaining >= 0
            else f"expired {format_duration(-remaining)} ago"
        )
    notes.append("signature not checked")
    return Result("\n\n".join(sections), " · ".join(notes))


# Entry points and format detection


def _check_size(text: str) -> None:
    if len(text) > MAX_INPUT_CHARS:
        raise CodecError(
            f"The input is over {MAX_INPUT_CHARS:,} characters. Use a smaller part."
        )


def decode(text: str, fmt: Format, plus_as_space: bool = True) -> Result:
    _check_size(text)
    if fmt is Format.BASE64:
        return base64_decode(text)
    if fmt is Format.URL:
        return url_decode(text, plus_as_space)
    if fmt is Format.HEX:
        return hex_decode(text)
    return jwt_decode(text)


def encode(text: str, fmt: Format, url_safe: bool = False) -> Result:
    _check_size(text)
    size = len(text.encode("utf-8"))
    note = f"{size:,} byte" + ("" if size == 1 else "s") + " of UTF-8 text encoded"
    if fmt is Format.BASE64:
        return Result(base64_encode(text, url_safe), note)
    if fmt is Format.URL:
        return Result(url_encode(text), note)
    if fmt is Format.HEX:
        return Result(hex_encode(text), note)
    raise CodecError("JWT can only be decoded. Creating a token needs a signing key.")


def detect(text: str) -> Format | None:
    """The format the text most likely is, for a hint to the user. None when it is not clear."""
    sample = text.strip()
    if not sample or len(sample) > MAX_INPUT_CHARS:
        return None
    token = _SCHEME.sub("", sample)
    if _JWT.match(token) and token.count(".") == 2:
        try:
            if "alg" in _b64url_json(token.split(".")[0], "header"):
                return Format.JWT
        except CodecError:
            pass
    if _PEM.search(sample) or re.match(
        r"^(authorization:\s*)?basic\s", sample, re.IGNORECASE
    ):
        return Format.BASE64
    if _PERCENT.search(sample):
        return Format.URL
    digits = _HEX_SEPARATORS.sub("", sample)
    # Plain words such as "cafe" or "decade" are also hex, so ask for a separator or a digit.
    if (
        len(digits) >= 4
        and len(digits) % 2 == 0
        and re.fullmatch(r"[0-9A-Fa-f]+", digits)
        and (digits != sample or re.search(r"\d", digits))
    ):
        return Format.HEX
    # Base64 can be split over lines, but never has spaces inside a line, as words do.
    if re.search(r"[ \t]", sample):
        return None
    compact = "".join(sample.split())
    if len(compact) >= 8 and re.fullmatch(r"[A-Za-z0-9+/\-_]+={0,2}", compact):
        try:
            result = base64_decode(compact)
        except CodecError:
            return None
        # Short words often decode to noise; only call them Base64 when the result is text.
        if not result.binary or len(compact) >= 24:
            return Format.BASE64
    return None
