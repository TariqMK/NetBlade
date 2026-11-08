"""Compares two pasted texts line by line, then finds the changed words or characters inside each changed line."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum

# Limits keep the worst comparison to seconds. Comparisons can't be cancelled, so their time must be bounded.
MAX_LINES = 20_000  # Per text.
# After this many differences, the search keeps what it has and continues from there.
# The result can then be slightly longer than the shortest possible, and never wrong.
MAX_EDIT_DISTANCE = 500
# A longer line is marked as changed, without inner highlights.
MAX_INLINE_CHARS = 2_000
# Words or characters compared in one comparison. Later changed lines get no inner highlights.
MAX_INLINE_TOKENS = 1_000_000
# Pairs of lines compared in one comparison. A block that needs more is not paired.
MAX_PAIRING_CELLS = 100_000
# Longer lines use a fast estimate of their similarity.
MAX_RATIO_CHARS = 200
# Less similar lines show as one removed and one added line.
MIN_SIMILARITY = 0.5

_LINE_BREAK = re.compile(r"\r\n|\r|\n")
_TOKEN = re.compile(r"\w+|\s+|[^\w\s]")


class Level(Enum):
    """How closely the inside of a changed line is compared."""

    LINES = "lines"
    WORDS = "words"
    CHARACTERS = "characters"


class Kind(Enum):
    EQUAL = "equal"
    CHANGED = "changed"
    REMOVED = "removed"
    ADDED = "added"


class DiffInputError(ValueError):
    """The input can't be compared. The message is written for the user."""


@dataclass(frozen=True)
class Line:
    """One side of a row. Spans are (start, end) character offsets of the changed parts."""

    number: int
    text: str
    spans: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class Row:
    """One aligned row of the side-by-side view. A side is None when it has no line here."""

    kind: Kind
    left: Line | None
    right: Line | None


@dataclass(frozen=True)
class Diff:
    rows: list[Row]
    sections: list[tuple[int, int]]  # First and last row of each run of changed rows.
    changed: int
    added: int
    removed: int
    exact_match: bool  # False if ignored differences or line endings exist.
    inline_skipped: bool  # True if a limit left some changes without inner highlights.

    @property
    def has_changes(self) -> bool:
        return bool(self.sections)


@dataclass(frozen=True)
class _Options:
    level: Level
    ignore_whitespace: bool
    ignore_case: bool

    def line_key(self, line: str) -> str:
        if self.ignore_whitespace:
            line = " ".join(line.split())
        return line.casefold() if self.ignore_case else line

    def token_key(self, token: str) -> str:
        if self.ignore_whitespace and token.isspace():
            return " "
        return token.casefold() if self.ignore_case else token


@dataclass
class _Budget:
    """The work left in this comparison."""

    cells: int = MAX_PAIRING_CELLS
    tokens: int = MAX_INLINE_TOKENS


def _matching_runs(a: list, b: list) -> list[tuple[int, int, int]]:
    """Runs of equal items as (start in a, start in b, length), in order, found with the Myers diff.

    Used in place of difflib's matcher. That matcher took about 15 s on 1,000 repeated lines, such
    as the "!" lines of a device config, and it can't be stopped once it runs.
    """
    ids: dict = {}
    a_ids = [ids.setdefault(item, len(ids)) for item in a]
    b_ids = [ids.setdefault(item, len(ids)) for item in b]
    runs: list[tuple[int, int, int]] = []
    i = j = 0
    while i < len(a_ids) and j < len(b_ids):
        i, j = _search(a_ids, b_ids, i, j, runs)
    return runs


def _search(
    a: list[int], b: list[int], a_start: int, b_start: int, runs: list
) -> tuple[int, int]:
    """Search forward from one position, up to MAX_EDIT_DISTANCE differences. Returns where it stopped."""
    n, m = len(a) - a_start, len(b) - b_start
    furthest: dict[int, int] = {}  # Diagonal (x - y) to the furthest x reached.
    trace: list[dict[int, tuple[int, int, bool]]] = []
    end = None
    for d in range(MAX_EDIT_DISTANCE + 1):
        step: dict[int, tuple[int, int, bool]] = {}
        for k in range(-d + 2 * max(0, d - m), d - 2 * max(0, d - n) + 1, 2):
            x = 0 if d == 0 else -1
            down = False
            before = furthest.get(k + 1)
            if before is not None and before - k <= m:
                x, down = before, True
            before = furthest.get(k - 1)
            if before is not None and before < n and before + 1 > x:
                x, down = before + 1, False
            if x < 0:
                continue
            start = x
            y = x - k
            while x < n and y < m and a[a_start + x] == b[b_start + y]:
                x += 1
                y += 1
            step[k] = (x, start, down)
            if x == n and y == m:
                end = k
                break
        trace.append(step)
        if end is not None:
            break
        furthest = {k: value[0] for k, value in step.items()}

    d = len(trace) - 1
    if end is None:  # Too many differences: keep the path that got furthest.
        end = max(trace[d], key=lambda k: 2 * trace[d][k][0] - k)
    k = end
    stop_x = trace[d][k][0]
    stop_y = stop_x - k
    found = []
    while True:
        x, start, down = trace[d][k]
        if x > start:
            found.append((a_start + start, b_start + start - k, x - start))
        if d == 0:
            break
        k = k + 1 if down else k - 1
        d -= 1
    runs.extend(reversed(found))
    return a_start + stop_x, b_start + stop_y


def split_lines(text: str) -> list[str]:
    """Split on line breaks only. One final line break does not make an extra empty line."""
    if not text:
        return []
    lines = _LINE_BREAK.split(text)
    if not lines[-1]:
        lines.pop()
    return lines


def compare(
    left: str,
    right: str,
    level: Level = Level.WORDS,
    ignore_whitespace: bool = False,
    ignore_case: bool = False,
) -> Diff:
    """Compare two texts. Raises DiffInputError if a text is over the size limit."""
    left_lines, right_lines = split_lines(left), split_lines(right)
    for name, lines in (("Text A", left_lines), ("Text B", right_lines)):
        if len(lines) > MAX_LINES:
            raise DiffInputError(
                f"{name} has {len(lines):,} lines. The limit is {MAX_LINES:,} lines."
            )

    options = _Options(level, ignore_whitespace, ignore_case)
    left_keys = [options.line_key(line) for line in left_lines]
    right_keys = [options.line_key(line) for line in right_lines]
    rows: list[Row] = []
    skipped = False

    def add_equal(left_start: int, right_start: int, count: int) -> None:
        for offset in range(count):
            rows.append(
                Row(
                    Kind.EQUAL,
                    Line(left_start + offset + 1, left_lines[left_start + offset]),
                    Line(right_start + offset + 1, right_lines[right_start + offset]),
                )
            )

    # Equal lines at the start and end need no search. This makes the usual small edit fast.
    limit = min(len(left_keys), len(right_keys))
    prefix = 0
    while prefix < limit and left_keys[prefix] == right_keys[prefix]:
        prefix += 1
    suffix = 0
    while suffix < limit - prefix and left_keys[-1 - suffix] == right_keys[-1 - suffix]:
        suffix += 1

    add_equal(0, 0, prefix)
    left_end, right_end = len(left_lines) - suffix, len(right_lines) - suffix
    budget = _Budget()
    i, j = prefix, prefix

    def add_block(to_i: int, to_j: int) -> bool:
        if to_i == i and to_j == j:
            return False
        return _add_block(
            rows,
            left_lines,
            right_lines,
            left_keys,
            right_keys,
            i,
            to_i,
            j,
            to_j,
            options,
            budget,
        )

    runs = _matching_runs(left_keys[prefix:left_end], right_keys[prefix:right_end])
    for run_i, run_j, length in runs:
        skipped |= add_block(prefix + run_i, prefix + run_j)
        add_equal(prefix + run_i, prefix + run_j, length)
        i, j = prefix + run_i + length, prefix + run_j + length
    skipped |= add_block(left_end, right_end)
    add_equal(left_end, right_end, suffix)

    return Diff(
        rows=rows,
        sections=_sections(rows),
        changed=sum(row.kind is Kind.CHANGED for row in rows),
        added=sum(row.kind is Kind.ADDED for row in rows),
        removed=sum(row.kind is Kind.REMOVED for row in rows),
        exact_match=left == right,
        inline_skipped=skipped,
    )


def _add_block(
    rows: list[Row],
    left_lines: list[str],
    right_lines: list[str],
    left_keys: list[str],
    right_keys: list[str],
    i1: int,
    i2: int,
    j1: int,
    j2: int,
    options: _Options,
    budget: _Budget,
) -> bool:
    """Add the rows of one changed block. Returns True if a limit skipped inner highlights."""
    pairs, skipped = _pair_lines(left_keys[i1:i2], right_keys[j1:j2], budget)
    i, j = i1, j1

    def flush(to_i: int, to_j: int) -> None:
        nonlocal i, j
        while i < to_i:
            rows.append(Row(Kind.REMOVED, Line(i + 1, left_lines[i]), None))
            i += 1
        while j < to_j:
            rows.append(Row(Kind.ADDED, None, Line(j + 1, right_lines[j])))
            j += 1

    for pair_i, pair_j in pairs:
        flush(i1 + pair_i, j1 + pair_j)
        left_spans, right_spans, too_long = _inline_spans(
            left_lines[i], right_lines[j], options, budget
        )
        skipped |= too_long
        rows.append(
            Row(
                Kind.CHANGED,
                Line(i + 1, left_lines[i], left_spans),
                Line(j + 1, right_lines[j], right_spans),
            )
        )
        i += 1
        j += 1
    flush(i2, j2)
    return skipped


def _pair_lines(
    lefts: list[str], rights: list[str], budget: _Budget
) -> tuple[list[tuple[int, int]], bool]:
    """Choose which lines of a changed block are edits of each other. Pairs never cross.

    Returns the pairs, and True if the block was too large to pair.
    """
    n, m = len(lefts), len(rights)
    if not n or not m:
        return [], False
    if n * m > budget.cells:
        return [], True
    budget.cells -= n * m

    matcher = SequenceMatcher(None, autojunk=False)
    similarity = [[0.0] * m for _ in range(n)]
    for j, right in enumerate(rights):
        matcher.set_seq2(right)  # difflib caches its work on the second sequence.
        for i, left in enumerate(lefts):
            matcher.set_seq1(left)
            if matcher.real_quick_ratio() < MIN_SIMILARITY:
                continue
            quick = matcher.quick_ratio()
            if quick < MIN_SIMILARITY:
                continue
            too_long = max(len(left), len(right)) > MAX_RATIO_CHARS
            score = quick if too_long else matcher.ratio()
            if score >= MIN_SIMILARITY:
                similarity[i][j] = score

    # Best[i][j] is the highest total similarity from line i and line j to the end of the block.
    best = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            options = [best[i + 1][j], best[i][j + 1]]
            if similarity[i][j]:
                options.append(similarity[i][j] + best[i + 1][j + 1])
            best[i][j] = max(options)

    pairs = []
    i = j = 0
    while i < n and j < m:
        if similarity[i][j] and best[i][j] == similarity[i][j] + best[i + 1][j + 1]:
            pairs.append((i, j))
            i += 1
            j += 1
        elif best[i][j] == best[i + 1][j]:
            i += 1
        else:
            j += 1
    return pairs, False


def _inline_spans(
    left: str, right: str, options: _Options, budget: _Budget
) -> tuple[tuple[tuple[int, int], ...], tuple[tuple[int, int], ...], bool]:
    """The changed parts of a pair of lines. The last value is True if a limit skipped them."""
    if options.level is Level.LINES:
        return (), (), False
    if max(len(left), len(right)) > MAX_INLINE_CHARS:
        return (), (), True
    left_tokens, right_tokens = _tokens(left, options), _tokens(right, options)
    budget.tokens -= len(left_tokens) + len(right_tokens)
    if budget.tokens < 0:
        return (), (), True
    left_offsets, right_offsets = _offsets(left_tokens), _offsets(right_tokens)
    left_spans, right_spans = [], []

    def add_change(i1: int, i2: int, j1: int, j2: int) -> None:
        if i2 > i1:
            left_spans.append((left_offsets[i1], left_offsets[i2]))
        if j2 > j1:
            right_spans.append((right_offsets[j1], right_offsets[j2]))

    i = j = 0
    runs = _matching_runs(
        [options.token_key(token) for token in left_tokens],
        [options.token_key(token) for token in right_tokens],
    )
    for run_i, run_j, length in runs:
        add_change(i, run_i, j, run_j)
        i, j = run_i + length, run_j + length
    add_change(i, len(left_tokens), j, len(right_tokens))
    if options.ignore_whitespace:
        left_spans = _without_blank(left, left_spans)
        right_spans = _without_blank(right, right_spans)
    return tuple(left_spans), tuple(right_spans), False


def _tokens(line: str, options: _Options) -> list[str]:
    return list(line) if options.level is Level.CHARACTERS else _TOKEN.findall(line)


def _offsets(tokens: list[str]) -> list[int]:
    offsets = [0]
    for token in tokens:
        offsets.append(offsets[-1] + len(token))
    return offsets


def _without_blank(line: str, spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Drop spans that hold only whitespace, for example changed indentation."""
    return [(start, end) for start, end in spans if line[start:end].strip()]


def _sections(rows: list[Row]) -> list[tuple[int, int]]:
    sections: list[tuple[int, int]] = []
    start = None
    for index, row in enumerate(rows):
        if row.kind is Kind.EQUAL:
            if start is not None:
                sections.append((start, index - 1))
                start = None
        elif start is None:
            start = index
    if start is not None:
        sections.append((start, len(rows) - 1))
    return sections


def unified(diff: Diff, context: int = 3) -> str:
    """The differences in unified diff format, as `diff -u` writes them. Empty if there are none."""
    hunks: list[list[int]] = []
    for first, last in diff.sections:
        start = max(0, first - context)
        end = min(len(diff.rows), last + 1 + context)
        if hunks and start <= hunks[-1][1]:
            hunks[-1][1] = end
        else:
            hunks.append([start, end])
    if not hunks:
        return ""

    output = ["--- Text A", "+++ Text B"]
    for start, end in hunks:
        rows = diff.rows[start:end]
        left_seen = sum(row.left is not None for row in diff.rows[:start])
        right_seen = sum(row.right is not None for row in diff.rows[:start])
        left_count = sum(row.left is not None for row in rows)
        right_count = sum(row.right is not None for row in rows)
        # The format gives the line before the hunk as the start when a side has no lines.
        left_start = left_seen + 1 if left_count else left_seen
        right_start = right_seen + 1 if right_count else right_seen
        output.append(f"@@ -{left_start},{left_count} +{right_start},{right_count} @@")
        removed: list[str] = []
        added: list[str] = []
        for row in rows:
            if row.kind is Kind.EQUAL:
                output += removed + added
                removed, added = [], []
                output.append(f" {row.left.text}")
                continue
            if row.left is not None:
                removed.append(f"-{row.left.text}")
            if row.right is not None:
                added.append(f"+{row.right.text}")
        output += removed + added
    return "\n".join(output)
