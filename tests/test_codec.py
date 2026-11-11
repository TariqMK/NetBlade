"""Checks Encode and decode: Base64, URL, hex and JWT conversions, errors, format detection and the page."""

import base64
import datetime as dt
import json
import random

import pytest

from netblade.app import MainWindow
from netblade.core import codec
from netblade.core.codec import HEXDUMP_LIMIT, MAX_INPUT_CHARS, CodecError, Format
from netblade.ui.codec_page import CodecPage

NOW = dt.datetime(2026, 11, 11, 12, 0, tzinfo=dt.UTC)


def b64url(value) -> str:
    raw = json.dumps(value).encode() if not isinstance(value, bytes) else value
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def make_jwt(
    payload: dict, header: dict | None = None, signature: str = "c2lnbmF0dXJl"
) -> str:
    return ".".join(
        [b64url(header or {"alg": "HS256", "typ": "JWT"}), b64url(payload), signature]
    )


# Base64


def test_base64_round_trip_for_any_text():
    rng = random.Random(3)
    for _ in range(200):
        text = "".join(
            chr(rng.choice([rng.randrange(32, 127), rng.randrange(160, 0x2FFF)]))
            for _ in range(rng.randrange(1, 40))
        )
        for url_safe in (False, True):
            encoded = codec.base64_encode(text, url_safe)
            assert codec.base64_decode(encoded).text == text


def test_base64_decode_adds_missing_padding_and_says_so():
    result = codec.base64_decode("SGVsbG8")
    assert result.text == "Hello"
    assert "missing padding added" in result.note


def test_base64_decode_reports_url_safe_alphabet():
    result = codec.base64_decode("PDw_Pz8-Pg")
    assert result.text == "<<???>>"
    assert "URL-safe alphabet" in result.note


def test_base64_url_safe_encode_has_no_padding_or_plus_slash():
    assert codec.base64_encode("<<???>>", url_safe=True) == "PDw_Pz8-Pg"
    assert codec.base64_encode("<<???>>") == "PDw/Pz8+Pg=="


def test_base64_decode_ignores_line_breaks_and_spaces():
    assert codec.base64_decode("SGVs\r\nbG8g\n V29y bGQ=").text == "Hello World"


def test_basic_auth_header_is_stripped():
    for header in (
        "Basic dXNlcjpwYXNz",
        "Authorization: Basic dXNlcjpwYXNz",
        "basic dXNlcjpwYXNz",
    ):
        result = codec.base64_decode(header)
        assert result.text == "user:pass"
        assert "Basic auth" in result.note


def test_pem_block_is_unwrapped_and_binary_shows_as_hex_dump():
    der = bytes(range(40))
    body = base64.b64encode(der).decode()
    pem = f"-----BEGIN CERTIFICATE-----\n{body[:32]}\n{body[32:]}\n-----END CERTIFICATE-----\n"
    result = codec.base64_decode(pem)
    assert result.binary
    assert "PEM certificate armour removed" in result.note
    assert result.text.splitlines()[0].startswith(
        "00000000  00 01 02 03 04 05 06 07  08 09"
    )


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("SGV#", '"#" at position 4'),
        ("SGVsb", "incomplete"),
        ("SG=V", "Padding (=) appears inside"),
        ("ab+c_d", "mixes standard"),
        ("   ", "no Base64 data"),
    ],
)
def test_base64_errors_are_clear(text, message):
    with pytest.raises(
        CodecError, match=message.replace("(", r"\(").replace(")", r"\)")
    ):
        codec.base64_decode(text)


def test_powershell_encoded_command_decodes_as_utf16():
    encoded = base64.b64encode("Get-Process | Sort CPU".encode("utf-16-le")).decode()
    result = codec.base64_decode(encoded)
    assert result.text == "Get-Process | Sort CPU"
    assert "UTF-16" in result.note


def test_utf8_bom_is_dropped():
    assert (
        codec.base64_decode(base64.b64encode(b"\xef\xbb\xbfhi").decode()).text == "hi"
    )


# Bytes and hex dumps


def test_control_characters_count_as_binary_but_tabs_and_newlines_do_not():
    assert codec.bytes_to_text(b"a\tb\r\nc")[0] == "a\tb\r\nc"
    assert codec.bytes_to_text(b"ab\x00c")[0] is None
    assert codec.bytes_to_text(b"\xff\xfe\xfd")[0] is None


def test_hexdump_layout():
    dump = codec.hexdump(b"NetBlade\x00\x01 rocks!!")
    first, second = dump.splitlines()
    assert (
        first
        == "00000000  4e 65 74 42 6c 61 64 65  00 01 20 72 6f 63 6b 73   NetBlade.. rocks"
    )
    assert second.startswith("00000010  21 21")
    assert second.endswith("  !!")


def test_large_binary_output_is_cut_and_says_so():
    data = bytes(range(256)) * ((HEXDUMP_LIMIT // 256) + 4)
    result = codec.base64_decode(base64.b64encode(data).decode())
    assert len(result.text.splitlines()) == HEXDUMP_LIMIT // 16
    assert f"first {HEXDUMP_LIMIT:,} bytes shown" in result.note


# URL and hex


def test_url_encode_and_decode():
    text = "a b/c?d=é&e+f"
    encoded = codec.url_encode(text)
    assert encoded == "a%20b%2Fc%3Fd%3D%C3%A9%26e%2Bf"
    assert codec.url_decode(encoded).text == text


def test_url_plus_as_space_is_an_option():
    assert codec.url_decode("a+b%2B").text == "a b+"
    assert codec.url_decode("a+b%2B", plus_as_space=False).text == "a+b+"


def test_url_decode_keeps_a_stray_percent_and_notes_it():
    result = codec.url_decode("100%25 and 50%")
    assert result.text == "100% and 50%"
    assert "position 14" in result.note


@pytest.mark.parametrize(
    "text",
    [
        "48656c6c6f",
        "48 65 6C 6C 6F",
        "48:65:6c:6c:6f",
        "0x48 0x65 0x6c 0x6c 0x6f",
        r"\x48\x65\x6c\x6c\x6f",
        "48-65-6c\n6c-6f",
    ],
)
def test_hex_decode_accepts_common_layouts(text):
    assert codec.hex_decode(text).text == "Hello"


def test_hex_errors():
    with pytest.raises(CodecError, match="odd number"):
        codec.hex_decode("abc")
    with pytest.raises(CodecError, match='"g" is not a hex digit'):
        codec.hex_decode("ag")


def test_hex_encode():
    assert codec.hex_encode("Hi é") == "486920c3a9"


# JWT


def test_jwt_shows_header_payload_times_and_signature():
    token = make_jwt(
        {
            "sub": "alice",
            "iat": int(NOW.timestamp()) - 7200,
            "exp": int(NOW.timestamp()) + 3600,
        }
    )
    result = codec.jwt_decode("Bearer " + token, NOW)
    sections = result.text.split("\n\n")
    assert sections[0].startswith("Header\n")
    assert json.loads(sections[0].split("\n", 1)[1]) == {"alg": "HS256", "typ": "JWT"}
    assert json.loads(sections[1].split("\n", 1)[1])["sub"] == "alice"
    assert "iat  2026-11-11 10:00:00  issued 2 h 0 min ago" in sections[2]
    assert "exp  2026-11-11 13:00:00  expires in 1 h 0 min" in sections[2]
    assert sections[3] == "Signature (not checked)\nc2lnbmF0dXJl"
    assert result.note == "JWT · HS256 · expires in 1 h 0 min · signature not checked"


def test_expired_and_unsigned_jwt():
    token = make_jwt({"exp": int(NOW.timestamp()) - 90}, {"alg": "none"}, signature="")
    note = codec.jwt_decode(token, NOW).note
    assert "unsigned" in note
    assert "expired 1 min 30 s ago" in note


def test_jwt_errors():
    with pytest.raises(CodecError, match="three parts"):
        codec.jwt_decode("abc.def")
    with pytest.raises(CodecError, match="encrypted token"):
        codec.jwt_decode("a.b.c.d.e")
    with pytest.raises(CodecError, match="payload is not Base64url-encoded JSON"):
        codec.jwt_decode(b64url({"alg": "HS256"}) + ".bm90IGpzb24.sig")
    with pytest.raises(CodecError, match="header is not a JSON object"):
        codec.jwt_decode(b64url([1, 2]) + "." + b64url({}) + ".sig")


def test_jwt_cannot_be_encoded():
    with pytest.raises(CodecError, match="signing key"):
        codec.encode("x", Format.JWT)


def test_odd_time_claims_are_left_out():
    token = make_jwt({"exp": "tomorrow", "iat": True, "nbf": 10**20})
    assert "Times" not in codec.jwt_decode(token, NOW).text


# Size limit and detection


def test_input_size_limit():
    with pytest.raises(CodecError, match="over 10,000,000 characters"):
        codec.decode("A" * (MAX_INPUT_CHARS + 1), Format.BASE64)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (make_jwt({"sub": "x"}), Format.JWT),
        ("Bearer " + make_jwt({"sub": "x"}), Format.JWT),
        ("SGVsbG8gV29ybGQ=", Format.BASE64),
        ("Basic dXNlcjpwYXNz", Format.BASE64),
        ("-----BEGIN PUBLIC KEY-----\nAAEC\n-----END PUBLIC KEY-----", Format.BASE64),
        ("name%3Dvalue", Format.URL),
        ("de:ad:be:ef", Format.HEX),
        ("0x48 0x69", Format.HEX),
        ("hello world", None),
        ("server01", None),
        ("cafe", None),
        ("", None),
    ],
)
def test_detect(text, expected):
    assert codec.detect(text) is expected


# Page


@pytest.fixture
def page(qapp):
    return CodecPage()


def type_text(page, text):
    page.input.setPlainText(text)
    page.convert()  # Skip the typing delay.


def test_page_decodes_base64_by_default(page):
    type_text(page, "SGVsbG8gV29ybGQ=")
    assert page.output.toPlainText() == "Hello World"
    assert "11 bytes" in page.status.text()
    assert page.error.isHidden()
    assert page.copy_button.isEnabled()


def test_page_shows_errors_and_clears_output(page):
    type_text(page, "SGV#")
    assert page.output.toPlainText() == ""
    assert not page.error.isHidden()
    assert '"#"' in page.error.text()
    assert not page.copy_button.isEnabled()


def test_page_encode_options_follow_the_format(page):
    page.set_mode(Format.BASE64, decoding=False)
    assert not page.url_safe.isHidden()
    assert page.plus_as_space.isHidden()
    type_text(page, "<<???>>")
    assert page.output.toPlainText() == "PDw/Pz8+Pg=="
    page.url_safe.setChecked(True)
    assert page.output.toPlainText() == "PDw_Pz8-Pg"
    page.set_mode(Format.URL, decoding=True)
    assert page.url_safe.isHidden()
    assert not page.plus_as_space.isHidden()


def test_page_jwt_turns_off_encode(page):
    page.set_mode(Format.HEX, decoding=False)
    page.set_mode(Format.JWT, decoding=False)
    assert page.decoding
    assert not page.direction_group.button(1).isEnabled()
    page.set_mode(Format.BASE64)
    assert page.direction_group.button(1).isEnabled()


def test_page_suggests_a_better_format(page):
    type_text(page, make_jwt({"sub": "x"}))
    assert not page.hint_button.isHidden()
    assert page.hint_button.text() == "Use JWT"
    page.hint_button.click()
    assert page.fmt is Format.JWT
    assert page.hint_button.isHidden()
    assert page.output.toPlainText().startswith("Header")


def test_page_swap_checks_a_round_trip(page):
    page.set_mode(Format.URL, decoding=False)
    type_text(page, "a b&c")
    assert page.output.toPlainText() == "a%20b%26c"
    page.swap()
    assert page.decoding
    assert page.input.toPlainText() == "a%20b%26c"
    assert page.output.toPlainText() == "a b&c"


def test_page_binary_output_does_not_wrap_and_cannot_swap(page):
    type_text(page, "AAECA/8=")
    assert page.result.binary
    assert page.output.lineWrapMode() == page.output.LineWrapMode.NoWrap
    assert not page.swap_button.isEnabled()


def test_page_is_in_the_sidebar(qapp, tmp_path):
    from PySide6.QtCore import QSettings

    window = MainWindow(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    names = window.tool_names()
    assert names[-1] == "Encode and decode"
    window.nav.setCurrentRow(names.index("Encode and decode"))
    assert window.pages.currentWidget() is window.codec_page
    window.close()
