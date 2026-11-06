"""Checks target parsing, name matching and certificate reading, and runs a full check against a local TLS server."""

import datetime as dt
import ssl
import threading

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from netblade.core.tlscheck import (
    TlsCheckError,
    check,
    expiry_text,
    name_matches,
    parse_certificate,
    parse_target,
    version_label,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("example.com", ("example.com", 443)),
        ("example.com:8443", ("example.com", 8443)),
        ("https://example.com:9443/path?q=1", ("example.com", 9443)),
        ("https://example.com/", ("example.com", 443)),
        ("[2001:db8::1]:8443", ("2001:db8::1", 8443)),
        ("2001:db8::1", ("2001:db8::1", 443)),
        ("10.0.0.1", ("10.0.0.1", 443)),
    ],
)
def test_parse_target(text, expected):
    assert parse_target(text) == expected


@pytest.mark.parametrize("text", ["", "example.com:http", "example.com:70000"])
def test_parse_target_errors(text):
    with pytest.raises(TlsCheckError):
        parse_target(text)


def test_name_matches():
    names = ["example.com", "*.example.com", "10.0.0.1"]
    assert name_matches("example.com", names)
    assert name_matches("WWW.Example.com.", names)
    assert not name_matches(
        "a.b.example.com", names
    )  # A wildcard covers one label only.
    assert not name_matches("example.org", names)
    assert name_matches("10.0.0.1", names)
    assert not name_matches("10.0.0.2", names)


def test_version_label():
    assert version_label("TLSv1.3") == "TLS 1.3"
    assert version_label("TLSv1") == "TLS 1.0"


def make_certificate(host: str, days: int, start_days_ago: int = 1):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(0xABCDEF)
        .not_valid_before(now - dt.timedelta(days=start_days_ago))
        .not_valid_after(now + dt.timedelta(days=days))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    return key, cert


def test_parse_certificate_and_expiry_text():
    _key, cert = make_certificate("netblade.test", days=10)
    info = parse_certificate(cert.public_bytes(serialization.Encoding.DER))
    assert info.subject_cn == "netblade.test"
    assert info.names == ["netblade.test"]
    assert info.self_signed
    assert info.key == "ECDSA secp256r1"
    assert info.serial == "ABCDEF"
    assert len(info.sha256.split(":")) == 32
    assert expiry_text(info) in ("9 days left", "10 days left")
    _key, old = make_certificate("old.test", days=-3, start_days_ago=30)
    assert expiry_text(
        parse_certificate(old.public_bytes(serialization.Encoding.DER))
    ).startswith("Expired")


def test_full_check_against_local_tls_server(tmp_path):
    """A real TLS handshake on this computer: chain read, trust refused (self-signed), name checked."""
    key, cert = make_certificate("localhost", days=90)
    cert_file, key_file = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_file, key_file)

    import socket

    server = socket.create_server(("127.0.0.1", 0))
    port = server.getsockname()[1]
    stop = threading.Event()

    def serve():
        server.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = server.accept()
            except TimeoutError:
                continue
            try:
                with context.wrap_socket(conn, server_side=True):
                    pass
            except (ssl.SSLError, OSError):
                conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        result = check("localhost", port, timeout_s=5)
    finally:
        stop.set()
        thread.join(2)
        server.close()

    assert result.leaf.subject_cn == "localhost"
    assert result.name_matches
    assert not result.trusted
    assert result.trust_error
    assert result.tls_version in ("TLS 1.3", "TLS 1.2")
    assert result.protocols["TLS 1.3"] or result.protocols["TLS 1.2"]


def test_closed_port_is_explained():
    import socket

    with socket.socket() as spare:
        spare.bind(("127.0.0.1", 0))
        port = spare.getsockname()[1]
    with pytest.raises(TlsCheckError, match="refused|didn't answer|Can't connect"):
        check("127.0.0.1", port, timeout_s=2)


def test_self_check_runs():
    from netblade.core.tlscheck import self_check

    self_check()
