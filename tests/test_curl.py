"""Checks the curl command builder, output parsing, timing phases and one real offline curl run."""

import shutil
import sys

import pytest

from netblade.core.curl import (
    CurlInputError,
    CurlRequest,
    CurlRun,
    body_text,
    build_args,
    display_command,
    split_verbose,
    status_text,
    timing_phases,
)


def test_plain_get_is_minimal():
    assert build_args(CurlRequest(url="https://example.com")) == [
        "-v",
        "-L",
        "--max-time",
        "30",
        "https://example.com",
    ]


def test_post_with_body_needs_no_x_flag():
    args = build_args(
        CurlRequest(url="https://api.example", method="POST", body='{"a":1}')
    )
    assert "-X" not in args
    assert args[args.index("--data-raw") + 1] == '{"a":1}'


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE", "OPTIONS"])
def test_other_methods_use_x_flag(method):
    args = build_args(CurlRequest(url="https://api.example", method=method))
    assert args[args.index("-X") + 1] == method


def test_head_uses_head_flag():
    args = build_args(CurlRequest(url="https://example.com", method="HEAD"))
    assert "--head" in args
    assert "-X" not in args


def test_options_map_to_flags():
    request = CurlRequest(
        url="https://example.com/path",
        follow_redirects=False,
        insecure=True,
        ip_version="IPv4 only",
        http_version="HTTP/1.1",
        timeout_s=5,
        headers=["Accept: application/json", "  ", "X-Test: 1"],
        extra_args="--compressed --proxy 'http://p:8080'",
    )
    args = build_args(request)
    assert "-L" not in args
    assert {"-k", "-4", "--http1.1", "--compressed"} <= set(args)
    assert args[args.index("--max-time") + 1] == "5"
    assert [args[i + 1] for i, a in enumerate(args) if a == "-H"] == [
        "Accept: application/json",
        "X-Test: 1",
    ]
    assert args[args.index("--proxy") + 1] == "http://p:8080"
    assert args[-1] == "https://example.com/path"


@pytest.mark.parametrize(
    ("url", "ip", "expected"),
    [
        ("https://example.com/x", "93.184.215.14", "example.com:443:93.184.215.14"),
        ("http://example.com", "10.0.0.5", "example.com:80:10.0.0.5"),
        ("example.com:8443", "10.0.0.5", "example.com:8443:10.0.0.5"),
        ("https://example.com", "2001:db8::1", "example.com:443:[2001:db8::1]"),
    ],
)
def test_connect_to_ip_builds_resolve(url, ip, expected):
    args = build_args(CurlRequest(url=url, connect_to_ip=ip))
    assert args[args.index("--resolve") + 1] == expected


@pytest.mark.parametrize(
    ("request_", "message"),
    [
        (CurlRequest(url="  "), "Enter a URL"),
        (
            CurlRequest(url="https://example.com", connect_to_ip="not-an-ip"),
            "IP address",
        ),
        (
            CurlRequest(url="https://example.com", extra_args="--user 'unclosed"),
            "quotes",
        ),
        (CurlRequest(url="https://example.com", timeout_s=0), "timeout"),
    ],
)
def test_invalid_input_is_explained(request_, message):
    with pytest.raises(CurlInputError, match=message):
        build_args(request_)


def test_display_command_quotes_for_this_system():
    command = display_command(["-H", "Accept: application/json", "https://example.com"])
    name = "curl.exe" if sys.platform == "win32" else "curl"
    quoted = (
        '"Accept: application/json"'
        if sys.platform == "win32"
        else "'Accept: application/json'"
    )
    assert command == f"{name} -H {quoted} https://example.com"


def test_split_verbose_separates_redirect_exchanges():
    verbose = """\
* Connected to example.com
> GET / HTTP/1.1
> Host: example.com
>
< HTTP/1.1 301 Moved Permanently
< Location: https://example.com/
<
> GET / HTTP/2
> Host: example.com
< HTTP/2 200
< content-type: text/html
"""
    request, response = split_verbose(verbose)
    assert request == [
        "GET / HTTP/1.1",
        "Host: example.com",
        "",
        "GET / HTTP/2",
        "Host: example.com",
    ]
    assert response == [
        "HTTP/1.1 301 Moved Permanently",
        "Location: https://example.com/",
        "",
        "HTTP/2 200",
        "content-type: text/html",
    ]


def test_timing_phases_from_cumulative_timers():
    phases = timing_phases(
        {
            "time_namelookup": 0.010,
            "time_connect": 0.030,
            "time_appconnect": 0.080,
            "time_pretransfer": 0.081,
            "time_starttransfer": 0.181,
            "time_total": 0.200,
        }
    )
    assert [(p.name, p.ms) for p in phases] == [
        ("DNS", 10.0),
        ("Connect", 20.0),
        ("TLS", 51.0),
        ("Server", 100.0),
        ("Transfer", 19.0),
    ]


def test_timing_phases_after_http_to_https_redirect():
    # Two requests: http (no TLS) then https. curl sums each timer over both requests.
    http = {
        "ns": 0.010,
        "conn": 0.020,
        "app": 0.0,
        "pre": 0.021,
        "start": 0.050,
        "end": 0.050,
    }
    https = {
        "ns": 0.005,
        "conn": 0.015,
        "app": 0.050,
        "pre": 0.051,
        "start": 0.100,
        "end": 0.120,
    }
    write_out = {
        "time_namelookup": http["ns"] + https["ns"],
        "time_connect": http["conn"] + https["conn"],
        "time_appconnect": http["app"] + https["app"],
        "time_pretransfer": http["pre"] + https["pre"],
        "time_starttransfer": http["start"] + https["start"],
        "time_total": http["end"] + https["end"],
    }
    phases = {p.name: p.ms for p in timing_phases(write_out)}
    assert (
        phases["TLS"] == 37.0
    )  # appconnect - connect gave 15 ms here, or nothing when the sums crossed.
    assert phases["Server"] == 78.0
    assert round(sum(phases.values()), 1) == 170.0


def test_timing_phases_skip_tls_on_plain_http():
    phases = timing_phases(
        {
            "time_namelookup": 0.001,
            "time_connect": 0.002,
            "time_starttransfer": 0.01,
            "time_total": 0.01,
        }
    )
    assert "TLS" not in [p.name for p in phases]


def test_status_text_and_body_text():
    assert status_text(404) == "404 Not Found"
    assert status_text(299) == "299"
    assert body_text(b'{"a":1}', "application/json; charset=utf-8") == '{\n  "a": 1\n}'
    assert body_text(b"plain", "text/plain") == "plain"
    assert body_text(b"\x00\x01\x02", None).startswith("Binary content, 3 bytes")


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl is not installed")
def test_real_curl_run_on_local_file(tmp_path):
    page = tmp_path / "page.txt"
    page.write_text("hello from netblade", encoding="utf-8")
    url = page.as_uri()
    result = CurlRun(
        build_args(CurlRequest(url=url, follow_redirects=False)), timeout_s=10
    ).run()
    assert result.exit_code == 0
    assert result.error is None
    assert result.body == b"hello from netblade"
    assert result.write_out.get("size_download") == len(b"hello from netblade")


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl is not installed")
def test_real_curl_failure_has_message():
    url = (
        "http://127.0.0.1:1/"  # Closed local port: fails without leaving this machine.
    )
    result = CurlRun(build_args(CurlRequest(url=url, timeout_s=10)), timeout_s=10).run()
    assert result.exit_code != 0
    assert result.status == 0
    assert result.error
