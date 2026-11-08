"""Checks the text comparison: alignment, inner highlights, ignore options, limits, unified output and the page."""

import random
import time

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication

from netblade.app import MainWindow
from netblade.core import text_diff
from netblade.core.text_diff import (
    MAX_INLINE_CHARS,
    MAX_LINES,
    MAX_PAIRING_CELLS,
    DiffInputError,
    Kind,
    Level,
    compare,
    split_lines,
    unified,
)
from netblade.ui import theme
from netblade.ui.diff_page import DiffPage


def kinds(diff):
    return [row.kind for row in diff.rows]


def changed_text(line):
    return [line.text[start:end] for start, end in line.spans]


# Lines


def test_split_lines_handles_all_line_endings_and_one_final_break():
    assert split_lines("") == []
    assert split_lines("a\r\nb\rc\nd") == ["a", "b", "c", "d"]
    assert split_lines("a\n") == ["a"]
    assert split_lines("a\n\n") == ["a", ""]
    assert split_lines("a\x0cb c") == ["a\x0cb c"]  # Not line breaks here.


def test_identical_texts_have_no_changes():
    diff = compare("a\nb", "a\nb")
    assert not diff.has_changes
    assert diff.exact_match
    assert kinds(diff) == [Kind.EQUAL, Kind.EQUAL]


def test_added_and_removed_lines_align_with_empty_sides():
    diff = compare("one\ntwo\nfour", "one\nthree hundred\nfour\nfive")
    assert kinds(diff) == [
        Kind.EQUAL,
        Kind.REMOVED,
        Kind.ADDED,
        Kind.EQUAL,
        Kind.ADDED,
    ]
    assert diff.rows[1].right is None
    assert diff.rows[2].left is None
    assert diff.rows[3].left.number == 3
    assert diff.rows[3].right.number == 3
    assert (diff.removed, diff.added, diff.changed) == (1, 2, 0)


def test_one_side_empty():
    diff = compare("", "a\nb")
    assert kinds(diff) == [Kind.ADDED, Kind.ADDED]
    assert diff.sections == [(0, 1)]


def test_sections_group_runs_of_changes():
    diff = compare("a\nb\nc\nd\ne", "a\nX\nc\nd\nY\nZ")
    # Rows: a, b removed, X added, c, d, e removed, Y added, Z added.
    assert diff.sections == [(1, 2), (5, 7)]


def rebuilt(diff, side):
    return [getattr(row, side).text for row in diff.rows if getattr(row, side)]


@pytest.mark.parametrize("edit_distance", [1, 2, 7, text_diff.MAX_EDIT_DISTANCE])
def test_rows_always_rebuild_both_texts(monkeypatch, edit_distance):
    """Low limits force the search to stop and continue, which must never lose or reorder a line."""
    monkeypatch.setattr(text_diff, "MAX_EDIT_DISTANCE", edit_distance)
    rng = random.Random(edit_distance)
    for _ in range(200):
        left = [rng.choice("abc!") for _ in range(rng.randrange(0, 30))]
        right = [rng.choice("abc!") for _ in range(rng.randrange(0, 30))]
        diff = compare("\n".join(left), "\n".join(right), Level.CHARACTERS)
        # An empty text and one empty line are the same text here, so only compare with content.
        assert rebuilt(diff, "left") == left
        assert rebuilt(diff, "right") == right
        numbers = [row.left.number for row in diff.rows if row.left]
        assert numbers == list(range(1, len(left) + 1))
        for row in diff.rows:
            if row.kind is Kind.EQUAL:
                assert row.left.text == row.right.text


def test_finds_the_shortest_edit_within_the_limit():
    runs = text_diff._matching_runs(list("abcabba"), list("cbabac"))
    # The longest common subsequence of these two strings, from the Myers paper, has 4 items.
    assert sum(length for _i, _j, length in runs) == 4


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (["!"] * MAX_LINES, ["!" if i % 2 else f"x{i}" for i in range(MAX_LINES)]),
        ([f"a{i}" for i in range(MAX_LINES)], [f"b{i}" for i in range(MAX_LINES)]),
    ],
)
def test_worst_inputs_finish_in_bounded_time(left, right):
    """difflib took about 15 s on 1,000 repeated lines and over 30 s on 2,000. These have 20,000."""
    start = time.monotonic()
    diff = compare("\n".join(left), "\n".join(right))
    assert time.monotonic() - start < 60
    assert rebuilt(diff, "left") == left
    assert rebuilt(diff, "right") == right


def test_common_lines_still_match():
    """A config repeats "!" often. Every other line differs here, so only the "!" lines can match."""
    left = "\n".join(f"port a{i}\n!" for i in range(150))
    right = "\n".join(f"port b{i}\n!" for i in range(150))
    diff = compare(left, right)
    assert sum(row.kind is Kind.EQUAL for row in diff.rows) == 150
    assert diff.changed == 150
    assert diff.added == diff.removed == 0


def test_only_a_final_line_break_differs():
    diff = compare("a\nb\n", "a\nb")
    assert not diff.has_changes
    assert not diff.exact_match


def test_line_endings_do_not_count_as_changes():
    diff = compare("a\r\nb", "a\nb")
    assert not diff.has_changes
    assert not diff.exact_match


# Pairing lines inside a changed block


def test_similar_lines_pair_up_and_different_lines_do_not():
    diff = compare("ntp server 192.0.2.1\nhostname alpha", "ntp server 192.0.2.2\n!!!!")
    assert diff.rows[0].kind is Kind.CHANGED
    assert diff.rows[0].left.text == "ntp server 192.0.2.1"
    assert diff.rows[0].right.text == "ntp server 192.0.2.2"
    assert [row.kind for row in diff.rows[1:]] == [Kind.REMOVED, Kind.ADDED]


def test_pairing_finds_the_matching_line_not_the_first_one():
    diff = compare(
        "ip route 192.0.2.0 255.255.255.0 eth0",
        "banner motd welcome\nip route 192.0.2.0 255.255.255.0 eth1",
    )
    assert kinds(diff) == [Kind.ADDED, Kind.CHANGED]
    assert diff.rows[1].right.number == 2


def test_block_too_large_to_pair_shows_removed_and_added():
    size = int(MAX_PAIRING_CELLS**0.5) + 1
    left = "\n".join(f"left line {i}" for i in range(size))
    right = "\n".join(f"right line {i}" for i in range(size))
    diff = compare(left, right)
    assert diff.changed == 0
    assert diff.removed == diff.added == size
    assert diff.inline_skipped


# Inner highlights


def test_lines_level_has_no_inner_highlights():
    row = compare("speed 100", "speed 1000", Level.LINES).rows[0]
    assert row.kind is Kind.CHANGED
    assert row.left.spans == row.right.spans == ()


def test_words_level_marks_the_changed_part_of_an_address():
    row = compare("ip 192.0.2.10 up", "ip 192.0.2.25 up", Level.WORDS).rows[0]
    assert changed_text(row.left) == ["10"]
    assert changed_text(row.right) == ["25"]


def test_characters_level_marks_single_characters():
    row = compare("vlan 120", "vlan 130", Level.CHARACTERS).rows[0]
    assert changed_text(row.left) == ["2"]
    assert changed_text(row.right) == ["3"]


def test_invisible_character_difference_is_marked():
    row = compare("ip address", "ip address").rows[0]
    assert row.kind is Kind.CHANGED
    assert changed_text(row.left) == [" "]
    assert changed_text(row.right) == [" "]


def test_very_long_line_skips_inner_highlights():
    long = "x" * MAX_INLINE_CHARS
    diff = compare(long + "a", long + "b")
    assert diff.rows[0].kind is Kind.CHANGED
    assert diff.rows[0].left.spans == ()
    assert diff.inline_skipped


# Ignore options


def test_ignore_whitespace():
    assert compare("  a   b ", "a b").has_changes
    diff = compare("  a   b ", "a b", ignore_whitespace=True)
    assert not diff.has_changes
    assert not diff.exact_match
    assert diff.rows[0].left.text == "  a   b "  # The original text still shows.
    assert compare("ab", "a b", ignore_whitespace=True).has_changes


def test_ignore_whitespace_does_not_mark_indentation_on_a_changed_line():
    row = compare("    speed 100", "speed 200", ignore_whitespace=True).rows[0]
    assert changed_text(row.left) == ["100"]
    assert changed_text(row.right) == ["200"]


def test_ignore_case():
    assert compare("Interface ETH0", "interface eth0").has_changes
    assert not compare("Interface ETH0", "interface eth0", ignore_case=True).has_changes
    row = compare("Speed 100", "speed 200", ignore_case=True).rows[0]
    assert changed_text(row.left) == ["100"]


def test_ignore_case_keeps_offsets_when_case_folding_changes_length():
    row = compare("Straße 1", "strasse 2", ignore_case=True).rows[0]
    assert changed_text(row.left) == ["1"]
    assert changed_text(row.right) == ["2"]


# Limits


@pytest.mark.parametrize(
    ("left", "right", "name"),
    [
        ("a\n" * (MAX_LINES + 1), "b", "Text A"),
        ("b", "a\n" * (MAX_LINES + 1), "Text B"),
    ],
)
def test_too_many_lines_is_refused_with_a_clear_message(left, right, name):
    with pytest.raises(DiffInputError, match=f"{name} has"):
        compare(left, right)


# Unified output


def test_unified_output():
    left = "\n".join(f"line {i}" for i in range(1, 11))
    right = left.replace("line 5", "line five")
    assert unified(compare(left, right), context=1) == (
        "--- Text A\n+++ Text B\n@@ -4,3 +4,3 @@\n line 4\n-line 5\n+line five\n line 6"
    )


def test_unified_groups_removed_before_added_and_merges_close_hunks():
    text = unified(compare("a\nb\nc\nd", "a\nX\nc\nY"), context=1)
    assert text.splitlines()[2:] == [
        "@@ -1,4 +1,4 @@",
        " a",
        "-b",
        "+X",
        " c",
        "-d",
        "+Y",
    ]


def test_unified_with_an_empty_side():
    assert unified(compare("", "a")).splitlines()[2:] == ["@@ -0,0 +1,1 @@", "+a"]
    assert unified(compare("a", "")).splitlines()[2:] == ["@@ -1,1 +0,0 @@", "-a"]


def test_unified_is_empty_without_changes():
    assert unified(compare("a", "a")) == ""


# Page


def wait_for_result(page, timeout=5.0):
    page._timer.stop()
    page.compare()
    deadline = time.monotonic() + timeout
    while page._running and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.005)
    assert not page._running


@pytest.fixture
def page(qapp):
    return DiffPage()


def fill(page, left, right):
    page.left_input.setPlainText(left)
    page.right_input.setPlainText(right)
    wait_for_result(page)


def test_page_waits_for_both_texts(page):
    fill(page, "only one text", "")
    assert page.body.currentWidget() is page.empty
    assert page.summary.text() == ""


def test_page_shows_aligned_rows_and_counts(page):
    fill(page, "one\ntwo lines\nthree", "one\ntwo lines changed\nthree\nfour")
    assert page.body.currentWidget() is page.results
    assert page.left_pane.blockCount() == page.right_pane.blockCount() == 4
    assert page.left_pane.toPlainText() == "one\ntwo lines\nthree\n"
    assert page.right_pane.toPlainText() == "one\ntwo lines changed\nthree\nfour"
    assert page.summary.text() == "Difference 1 of 2 · 1 different · 1 only in B"


def test_summary_counts_lines_only_in_each_text(page):
    fill(page, "same\nonly here\nend", "same\nend\nand only here too")
    assert page.summary.text().endswith("1 only in A · 1 only in B")


def test_page_reports_no_differences(page):
    fill(page, "a", "a")
    assert page.summary.text() == "No differences."
    assert not page.next_button.isEnabled()
    assert not page.copy_button.isEnabled()
    fill(page, "Line A\n", "line a")
    assert page.summary.text() == "Difference 1 of 1 · 1 different"
    page.ignore_case.setChecked(True)
    wait_for_result(page)
    assert page.summary.text() == (
        "No differences. Ignored differences or line endings exist."
    )


def test_page_level_buttons_change_the_highlights(page):
    fill(page, "vlan 120", "vlan 130")
    assert page.diff.rows[0].left.spans == ((5, 8),)
    page.level_group.button(2).click()
    wait_for_result(page)
    assert page.diff.rows[0].left.spans == ((6, 7),)
    page.level_group.button(0).click()
    wait_for_result(page)
    assert page.diff.rows[0].left.spans == ()


def test_page_next_and_previous_move_through_changes_and_wrap(page):
    lines = [f"line {i}" for i in range(200)]
    changed = list(lines)
    changed[20] = "line twenty"
    changed[150] = "line one fifty"
    fill(page, "\n".join(lines), "\n".join(changed))
    page.resize(900, 700)
    page.show()
    QCoreApplication.processEvents()
    assert page.summary.text().startswith("Difference 1 of 2")
    page.next_button.click()
    assert page.summary.text().startswith("Difference 2 of 2")
    value = page.left_pane.verticalScrollBar().value()
    assert value > 0
    assert page.right_pane.verticalScrollBar().value() == value
    page.next_button.click()
    assert page.summary.text().startswith("Difference 1 of 2")
    page.previous_button.click()
    assert page.summary.text().startswith("Difference 2 of 2")
    page.hide()


def test_panes_scroll_together(page):
    text = "\n".join(f"line {i}" for i in range(300))
    fill(page, text, text + "\nend")
    page.resize(900, 700)
    page.show()
    QCoreApplication.processEvents()
    page.left_pane.verticalScrollBar().setValue(40)
    assert page.right_pane.verticalScrollBar().value() == 40
    page.right_pane.verticalScrollBar().setValue(70)
    assert page.left_pane.verticalScrollBar().value() == 70
    page.hide()


def test_copied_selection_leaves_out_empty_rows(page):
    fill(page, "one\nthree", "one\nnew\nthree")
    pane = page.left_pane
    assert (
        pane.toPlainText() == "one\n\nthree"
    )  # The middle row has no line on this side.
    pane.selectAll()
    pane.copy()
    assert QApplication.clipboard().text() == "one\nthree"
    cursor = pane.textCursor()
    cursor.setPosition(1)
    cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
    pane.setTextCursor(cursor)
    pane.copy()
    assert QApplication.clipboard().text() == "ne\nthree"


def test_copy_diff_button_copies_unified_text(page):
    fill(page, "a", "b")
    page.copy_button.click()
    assert QApplication.clipboard().text() == unified(page.diff)
    assert "-a\n+b" in QApplication.clipboard().text()


def test_too_large_input_shows_the_error_and_no_results(page):
    fill(page, "a", "b")
    fill(page, "a\n" * (MAX_LINES + 1), "b")
    assert page.error.text().startswith("Text A has")
    assert not page.error.isHidden()
    assert page.body.currentWidget() is page.empty
    fill(page, "a", "b")
    assert page.error.isHidden()


def test_text_changed_during_a_comparison_starts_a_new_one(page):
    page.left_input.setPlainText("first")
    page.right_input.setPlainText("first")
    page._timer.stop()
    page.compare()
    assert page._running
    page.right_input.setPlainText("second")
    page._timer.stop()
    page.compare()  # Runs after the current comparison ends.
    deadline = time.monotonic() + 5
    while page._running and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.005)
    assert page.diff.rows[-1].right.text == "second"


def test_panes_paint_in_both_themes_with_every_row_kind(page):
    fill(page, "same\nspeed 100\nremoved line here\nend", "same\nspeed 200\nend\nxx")
    page.resize(900, 700)
    page.show()
    QCoreApplication.processEvents()
    assert not page.left_pane.grab().isNull()
    page.apply_theme()
    assert page.left_pane.blockCount() == len(page.diff.rows)
    assert theme.current() is not None
    page.hide()


def test_text_diff_is_in_the_sidebar_and_nothing_is_saved(settings):
    window = MainWindow(settings)
    assert "Text diff" in window.tool_names()
    window.nav.setCurrentRow(window.tool_names().index("Text diff"))
    assert window.pages.currentWidget() is window.diff_page
    window.diff_page.left_input.setPlainText("secret")
    window.close()
    settings.sync()
    assert "secret" not in repr(
        {key: settings.value(key) for key in settings.allKeys()}
    )
