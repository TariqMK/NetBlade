"""TLS certificate checker: fetches a server's certificate chain, checks trust with the operating system,
and tests which TLS versions the server accepts. Needs Python 3.13 or later for the full chain.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import re
import socket
import ssl
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import truststore
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import dsa, ec, ed448, ed25519, rsa
from cryptography.x509.oid import ExtensionOID, NameOID

DEFAULT_PORT = 443
TIMEOUT_S = 10.0
WARN_DAYS = 30  # Certificates closer than this to expiry show as a warning.

# Old versions first are tested last; each entry is (label, ssl.TLSVersion).
PROTOCOLS = [
    ("TLS 1.3", ssl.TLSVersion.TLSv1_3),
    ("TLS 1.2", ssl.TLSVersion.TLSv1_2),
    ("TLS 1.1", ssl.TLSVersion.TLSv1_1),
    ("TLS 1.0", ssl.TLSVersion.TLSv1),
]
OLD_PROTOCOLS = {"TLS 1.1", "TLS 1.0"}


class TlsCheckError(Exception):
    """The check could not run. The message is written for the user."""


@dataclass
class CertInfo:
    subject: str
    subject_cn: str
    issuer: str
    issuer_cn: str
    not_before: dt.datetime
    not_after: dt.datetime
    names: list[str]
    serial: str
    signature: str
    key: str
    sha256: str
    is_ca: bool
    self_signed: bool

    def days_left(self, now: dt.datetime | None = None) -> int:
        now = now or dt.datetime.now(dt.UTC)
        return (self.not_after - now).days

    def expired(self, now: dt.datetime | None = None) -> bool:
        return (now or dt.datetime.now(dt.UTC)) > self.not_after

    def not_yet_valid(self, now: dt.datetime | None = None) -> bool:
        return (now or dt.datetime.now(dt.UTC)) < self.not_before


@dataclass
class TlsResult:
    host: str
    port: int
    address: str
    tls_version: str
    cipher: str
    chain: list[CertInfo]
    trusted: bool
    trust_error: str | None
    name_matches: bool
    elapsed_ms: float
    protocols: dict[str, bool | None] = field(
        default_factory=dict
    )  # None means NetBlade can't test it.

    @property
    def leaf(self) -> CertInfo:
        return self.chain[0]


def parse_target(text: str) -> tuple[str, int]:
    """Read "example.com", "example.com:8443", "https://example.com/path", "[2001:db8::1]:443" or an IP."""
    text = text.strip()
    if not text:
        raise TlsCheckError(
            "Enter a host name, for example example.com or example.com:8443."
        )
    if "://" in text:
        parts = urlsplit(text)
        if not parts.hostname:
            raise TlsCheckError(f"{text} has no host name.")
        return parts.hostname, parts.port or DEFAULT_PORT
    match = re.fullmatch(r"\[([0-9a-fA-F:.]+)\](?::(\d+))?", text)
    if match:
        return match.group(1), int(match.group(2) or DEFAULT_PORT)
    if text.count(":") > 1:  # A bare IPv6 address.
        return text, DEFAULT_PORT
    host, _, port = text.partition(":")
    if port and not port.isdigit():
        raise TlsCheckError(f"{port} is not a port number.")
    port_number = int(port) if port else DEFAULT_PORT
    if not 1 <= port_number <= 65535:
        raise TlsCheckError("The port must be between 1 and 65535.")
    return host.strip().rstrip("."), port_number


def _name_attr(name: x509.Name, oid) -> str:
    values = name.get_attributes_for_oid(oid)
    return values[0].value if values else ""


def _key_text(public_key) -> str:
    if isinstance(public_key, rsa.RSAPublicKey):
        return f"RSA {public_key.key_size} bit"
    if isinstance(public_key, ec.EllipticCurvePublicKey):
        return f"ECDSA {public_key.curve.name}"
    if isinstance(public_key, ed25519.Ed25519PublicKey):
        return "Ed25519"
    if isinstance(public_key, ed448.Ed448PublicKey):
        return "Ed448"
    if isinstance(public_key, dsa.DSAPublicKey):
        return f"DSA {public_key.key_size} bit"
    return type(public_key).__name__


def parse_certificate(der: bytes) -> CertInfo:
    cert = x509.load_der_x509_certificate(der)
    names: list[str] = []
    try:
        san = cert.extensions.get_extension_for_oid(
            ExtensionOID.SUBJECT_ALTERNATIVE_NAME
        ).value
        names = san.get_values_for_type(x509.DNSName) + [
            str(ip) for ip in san.get_values_for_type(x509.IPAddress)
        ]
    except x509.ExtensionNotFound:
        pass
    try:
        is_ca = cert.extensions.get_extension_for_oid(
            ExtensionOID.BASIC_CONSTRAINTS
        ).value.ca
    except x509.ExtensionNotFound:
        is_ca = False
    signature = (
        cert.signature_hash_algorithm.name.upper()
        if cert.signature_hash_algorithm
        else ""
    )
    algorithm = (
        cert.signature_algorithm_oid._name
    )  # For example "sha256WithRSAEncryption".
    return CertInfo(
        subject=cert.subject.rfc4514_string(),
        subject_cn=_name_attr(cert.subject, NameOID.COMMON_NAME)
        or _name_attr(cert.subject, NameOID.ORGANIZATION_NAME),
        issuer=cert.issuer.rfc4514_string(),
        issuer_cn=_name_attr(cert.issuer, NameOID.COMMON_NAME)
        or _name_attr(cert.issuer, NameOID.ORGANIZATION_NAME),
        not_before=cert.not_valid_before_utc,
        not_after=cert.not_valid_after_utc,
        names=names,
        serial=format(cert.serial_number, "X"),
        signature=algorithm if algorithm else signature,
        key=_key_text(cert.public_key()),
        sha256=cert.fingerprint(hashes.SHA256()).hex(":").upper(),
        is_ca=is_ca,
        self_signed=cert.subject == cert.issuer,
    )


def name_matches(host: str, names: list[str]) -> bool:
    """True when the host matches one of the certificate names. A wildcard covers exactly one label."""
    host = host.lower().rstrip(".")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    for name in names:
        name = name.lower().rstrip(".")
        if address is not None:
            try:
                if ipaddress.ip_address(name) == address:
                    return True
            except ValueError:
                continue
            continue
        if name == host:
            return True
        if name.startswith("*.") and "." in host:
            first, rest = host.split(".", 1)
            if rest == name[2:] and first:
                return True
    return False


def _connect(host: str, port: int, timeout_s: float) -> socket.socket:
    try:
        return socket.create_connection((host, port), timeout=timeout_s)
    except socket.gaierror as error:
        raise TlsCheckError(f"Can't find {host}. Check the name.") from error
    except TimeoutError as error:
        raise TlsCheckError(
            f"{host}:{port} didn't answer within {timeout_s:g} s."
        ) from error
    except ConnectionRefusedError as error:
        raise TlsCheckError(
            f"{host} refused the connection on port {port}. Nothing is listening there."
        ) from error
    except OSError as error:
        raise TlsCheckError(
            f"Can't connect to {host}:{port}: {error.strerror or error}."
        ) from error


def _server_name(host: str) -> str | None:
    """The name sent in SNI. IP addresses are not sent."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host
    return None


def _unverified_context(allow_old: bool = False) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    if allow_old:
        # Reading the certificate must work even on servers that only speak TLS 1.0 or 1.1,
        # so they can be reported. Trust is checked separately with the system's own rules.
        try:
            with (
                warnings.catch_warnings()
            ):  # TLS 1.0 is deprecated; allowing it here is deliberate.
                warnings.simplefilter("ignore", DeprecationWarning)
                context.minimum_version = ssl.TLSVersion.TLSv1
            context.set_ciphers("DEFAULT:@SECLEVEL=0")
        except (ValueError, ssl.SSLError):
            pass
    return context


def _trust_check(host: str, port: int, timeout_s: float) -> str | None:
    """None when the operating system trusts the server for this host name, otherwise the reason."""
    context = truststore.SSLContext(
        ssl.PROTOCOL_TLS_CLIENT
    )  # The OS decides, as a browser would.
    with _connect(host, port, timeout_s) as raw:
        try:
            with context.wrap_socket(raw, server_hostname=host):
                return None
        except ssl.SSLCertVerificationError as error:
            return error.verify_message or str(error)
        except ssl.SSLError as error:
            return (
                "Can't check trust: this system refused the TLS handshake "
                f"({error.reason or error}). The server may only offer old TLS versions or ciphers."
            )


def _protocol_check(
    host: str, port: int, version: ssl.TLSVersion, timeout_s: float
) -> bool | None:
    context = _unverified_context()
    try:
        with (
            warnings.catch_warnings()
        ):  # Testing deprecated TLS versions is the point of this check.
            warnings.simplefilter("ignore", DeprecationWarning)
            context.minimum_version = version
            context.maximum_version = version
        if version < ssl.TLSVersion.TLSv1_2:
            context.set_ciphers(
                "DEFAULT:@SECLEVEL=0"
            )  # Let OpenSSL offer the old versions at all.
    except (ValueError, ssl.SSLError):
        return None
    try:
        with (
            _connect(host, port, timeout_s) as raw,
            context.wrap_socket(raw, server_hostname=_server_name(host)),
        ):
            return True
    except ssl.SSLError as error:
        if "no protocols available" in str(error).lower():
            return None  # This system's OpenSSL can't speak the version, so it can't be tested.
        return False
    except (TlsCheckError, OSError):
        return False


def check(
    host: str, port: int = DEFAULT_PORT, timeout_s: float = TIMEOUT_S
) -> TlsResult:
    """Connect once to read the chain, once to check trust, and once per TLS version."""
    started = time.perf_counter()
    with _connect(host, port, timeout_s) as raw:
        address = raw.getpeername()[0]
        try:
            with _unverified_context(allow_old=True).wrap_socket(
                raw, server_hostname=_server_name(host)
            ) as tls:
                chain_der = tls.get_unverified_chain() or [
                    tls.getpeercert(binary_form=True)
                ]
                version = tls.version() or ""
                cipher = tls.cipher()[0] if tls.cipher() else ""
        except ssl.SSLError as error:
            raise TlsCheckError(
                f"{host}:{port} didn't complete a TLS handshake ({error.reason or error}). "
                "The port may not use TLS, or it uses STARTTLS."
            ) from error
    elapsed_ms = (time.perf_counter() - started) * 1000
    chain = [parse_certificate(der) for der in chain_der]

    with ThreadPoolExecutor(len(PROTOCOLS) + 1) as pool:
        trust_future = pool.submit(_trust_check, host, port, timeout_s)
        protocol_futures = {
            label: pool.submit(_protocol_check, host, port, version_enum, timeout_s)
            for label, version_enum in PROTOCOLS
        }
        try:
            trust_error = trust_future.result()
        except TlsCheckError as error:
            trust_error = str(error)
        protocols = {
            label: future.result() for label, future in protocol_futures.items()
        }

    return TlsResult(
        host=host,
        port=port,
        address=address,
        tls_version=version_label(version),
        cipher=cipher,
        chain=chain,
        trusted=trust_error is None,
        trust_error=trust_error,
        name_matches=name_matches(host, chain[0].names) if chain else False,
        elapsed_ms=elapsed_ms,
        protocols=protocols,
    )


def version_label(version: str) -> str:
    """ "TLSv1.3" becomes "TLS 1.3"; "TLSv1" becomes "TLS 1.0"."""
    number = version.replace("TLSv", "")
    if number == "1":
        number = "1.0"
    return f"TLS {number}" if number else ""


def chain_role(index: int, cert: CertInfo, length: int) -> str:
    if index == 0:
        return "Server certificate"
    if cert.self_signed:
        return "Root certificate"
    return (
        "Intermediate certificate"
        if index < length - 1 or cert.is_ca
        else "Certificate"
    )


def expiry_text(cert: CertInfo, now: dt.datetime | None = None) -> str:
    days = cert.days_left(now)
    if cert.not_yet_valid(now):
        return f"Not valid until {cert.not_before:%Y-%m-%d}"
    if cert.expired(now):
        ago = -days
        return (
            "Expired today"
            if ago == 0
            else f"Expired {ago} day{'s' if ago != 1 else ''} ago"
        )
    return f"{days} day{'s' if days != 1 else ''} left"


def self_check() -> None:
    """Use cryptography's compiled code and truststore once. Proves a packaged build includes them."""
    key = ec.generate_private_key(ec.SECP256R1())
    key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
