"""Checks that every HTTPS request NetBlade makes trusts certificates through the operating system (truststore)."""

import io
import json
import urllib.request

import pytest
import truststore

from netblade.core import oui, rdap, update_check


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture
def contexts(monkeypatch):
    seen = []

    def fake_urlopen(request, timeout=None, context=None):
        seen.append(context)
        url = request.full_url
        if "ipify" in url:
            return FakeResponse(b"203.0.113.7")
        if "ieee" in url:
            return FakeResponse(
                b"Registry,Assignment,Organization Name,Organization Address\n"
            )
        if "github" in url:
            return FakeResponse(
                json.dumps({"tag_name": "v1.0.0", "html_url": "u", "body": ""}).encode()
            )
        return FakeResponse(b"{}")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return seen


def test_rdap_uses_system_trust(contexts):
    rdap._get_json("https://rdap.example/ip/192.0.2.1")
    rdap.public_ip()
    assert contexts and all(isinstance(c, truststore.SSLContext) for c in contexts)


def test_vendor_download_uses_system_trust(contexts, tmp_path):
    oui.download(tmp_path)
    assert contexts and all(isinstance(c, truststore.SSLContext) for c in contexts)


def test_update_check_uses_system_trust(contexts):
    update_check.check("1.0.0")
    assert contexts and all(isinstance(c, truststore.SSLContext) for c in contexts)
