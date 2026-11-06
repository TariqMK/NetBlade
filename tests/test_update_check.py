"""Checks the "Check for updates" logic: version comparison, GitHub answers and the sidebar button. No network."""

import io
import json
import urllib.error

import pytest

from netblade.app import MainWindow, display_version, update_summary
from netblade.core import update_check
from netblade.core.update_check import (
    UpdateError,
    UpdateInfo,
    is_newer,
    parse_release,
    parse_version,
)
from netblade.ui import tasks


@pytest.mark.parametrize(
    ("latest", "current", "expected"),
    [
        ("v1.3.0", "1.2.9", True),
        (
            "v0.10.0",
            "0.9.0",
            True,
        ),  # Numbers, not text: "0.10" sorts below "0.9" as text.
        ("v2.0.0", "1.99.99", True),
        ("v1.2.3", "1.2.3", False),
        ("v1.2.2", "1.2.3", False),
        ("v1.3.0", "0.0.0.dev0", False),  # A dev build has nothing to compare.
        ("nightly", "1.2.3", False),
    ],
)
def test_is_newer(latest, current, expected):
    assert is_newer(latest, current) is expected


def test_parse_version():
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert parse_version("1.2.3") == (1, 2, 3)
    assert parse_version("0.0.0.dev0") is None
    assert parse_version("v1.2") is None


def release(tag="v1.3.0", **extra):
    return {
        "tag_name": tag,
        "html_url": f"https://github.com/TariqMK/NetBlade/releases/tag/{tag}",
        "body": "## Changes\n- Faster ping\n",
        **extra,
    }


def test_parse_release_newer():
    info = parse_release(release(), "1.2.0")
    assert info.newer and not info.dev_build
    assert info.latest == "v1.3.0"
    assert info.url.endswith("/tag/v1.3.0")
    assert info.notes == "## Changes\n- Faster ping"


def test_parse_release_rejects_unsafe_url_and_bad_tag():
    info = parse_release(release(html_url="javascript:alert(1)"), "1.2.0")
    assert info.url == update_check.RELEASES_PAGE
    with pytest.raises(UpdateError):
        parse_release(release(tag="latest"), "1.2.0")
    with pytest.raises(UpdateError):
        parse_release(["not", "a", "dict"], "1.2.0")


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_check_reads_github_answer(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["agent"] = request.get_header("User-agent")
        return FakeResponse(json.dumps(release()).encode())

    monkeypatch.setattr(update_check.urllib.request, "urlopen", fake_urlopen)
    info = update_check.check("1.0.0")
    assert info.newer
    assert seen["url"] == update_check.LATEST_URL
    assert seen["agent"].startswith("NetBlade/")


@pytest.mark.parametrize(
    ("error", "text"),
    [
        (
            urllib.error.HTTPError("u", 404, "Not Found", None, None),
            "No NetBlade releases",
        ),
        (
            urllib.error.HTTPError("u", 403, "Forbidden", None, None),
            "Try again in an hour",
        ),
        (urllib.error.URLError("no route"), "Check the internet connection"),
        (TimeoutError("timed out"), "Check the internet connection"),
    ],
)
def test_check_failures_have_user_messages(monkeypatch, error, text):
    def fake_urlopen(request, timeout):
        raise error

    monkeypatch.setattr(update_check.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(UpdateError, match=text):
        update_check.check("1.0.0")


def test_check_rejects_non_json(monkeypatch):
    monkeypatch.setattr(
        update_check.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse(b"<html>"),
    )
    with pytest.raises(UpdateError, match="can't read"):
        update_check.check("1.0.0")


def test_update_summary_texts():
    newer = UpdateInfo("1.0.0", "v1.3.0", "https://x", "")
    same = UpdateInfo("1.3.0", "v1.3.0", "https://x", "")
    dev = UpdateInfo("0.0.0.dev0", "v1.3.0", "https://x", "")
    assert "v1.3.0 is available" in update_summary(newer)[0]
    assert "up to date" in update_summary(same)[0]
    assert "development build" in update_summary(dev)[0]


def run_now(work, on_result, on_error):
    """Stand-in for tasks.run_in_thread that runs the work at once, so tests need no event loop."""
    try:
        value = work()
    except Exception as error:  # noqa: BLE001
        on_error(error)
    else:
        on_result(value)


def test_version_button_shows_newer_release(qapp, settings, monkeypatch):
    window = MainWindow(settings)
    shown = []
    monkeypatch.setattr(tasks, "run_in_thread", run_now)
    monkeypatch.setattr(
        update_check, "check", lambda: UpdateInfo("1.0.0", "v1.3.0", "https://x", "")
    )
    monkeypatch.setattr(window, "_show_update_dialog", shown.append)
    window.version_label.click()
    assert shown and shown[0].latest == "v1.3.0"
    assert (
        window.version_label.text() == display_version()
    )  # Self-test reads this text.
    assert window.version_label.isEnabled()
    assert window.version_label.property("update") is True
    assert "v1.3.0 is available" in window.version_label.toolTip()


def test_version_button_reports_errors(qapp, settings, monkeypatch):
    window = MainWindow(settings)
    errors = []
    monkeypatch.setattr(tasks, "run_in_thread", run_now)

    def fail():
        raise UpdateError("Can't reach GitHub.")

    monkeypatch.setattr(update_check, "check", fail)
    monkeypatch.setattr(window, "_show_update_error", errors.append)
    window.version_label.click()
    assert errors == ["Can't reach GitHub."]
    assert window.version_label.text() == display_version()
    assert window.version_label.isEnabled()
