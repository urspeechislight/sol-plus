"""
Phase 2: SEGMENT

Input:  Manuscript (pages populated)
Output: Manuscript (spans populated, each with .behavior and .hierarchy set)

This is the most consequential phase. The behavior label assigned here
controls what happens to a span in every subsequent phase.

Two sub-jobs run in a single sequential pass:

1. BOUNDARY DETECTION + BEHAVIOR ROUTING
   All content page texts are joined into one combined text so the boundary
   splitter decides where spans break — not arbitrary page boundaries. A
   hadith that flows across 3 pages stays as one span. For each resulting
   paragraph, run pattern detectors from config.patterns. Consult
   config.behaviors (the routing table) to assign a behavior label. There
   is no intermediate scoring layer -- patterns route directly to behaviors.

2. HIERARCHY TRACKING
   A KitabBabFasl FSM tracker runs during the same pass. Each span's
   .hierarchy is the FSM state at the moment it is seen.
"""

from __future__ import annotations

import bisect
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from src.exceptions import SegmentError
from src.models import HierarchyPath, Manuscript, Page, Pattern, Span
from src.utils.config import Config

logger = logging.getLogger(__name__)

SEGMENT__SPAN__TYPE_PARAGRAPH = "paragraph"
SEGMENT__BEHAVIOR__GENERAL_PROSE = "GENERAL_PROSE"
SEGMENT__BEHAVIOR__SECTION_HEADING = "SECTION_HEADING"
SEGMENT__PATTERN__HEADING_MARKER = "HEADING_MARKER"
SEGMENT__SPAN_ID__FORMAT = "{manifestation_id}_s{index:04d}"

@dataclass
class _BehaviorRule:
    """A pre-parsed behavior routing rule from config."""
    behavior_id: str
    requires: set[str]
    any_of: set[str]
    none_of: set[str]
    priority: int


@dataclass
class _KitabBabFaslTracker:
    """FSM tracker for hierarchical heading levels.

    Levels and their associated Arabic prefixes are read from config's
    HEADING_MARKER pattern hierarchy_levels field. When a SECTION_HEADING
    span is seen, the tracker determines which level to update based on
    the text prefix and clears all deeper levels.
    """

    _level_names: tuple[str, ...] = ()
    _prefix_to_level: dict[str, str] = field(default_factory=dict)
    _all_prefixes: tuple[str, ...] = ()
    _top_level_prefixes: frozenset[str] = field(default_factory=frozenset)
    _heading_stop_patterns: tuple[str, ...] = ()
    _attribution_re: re.Pattern[str] | None = field(default=None, repr=False)
    _heading_counter: int = field(default=0)
    _state: dict[str, tuple[str, str]] = field(default_factory=dict)

    def advance(self, span_text: str, span_id: str) -> None:
        """Update hierarchy state when a SECTION_HEADING span is encountered.

        Determines the heading level from the text prefix and updates the
        appropriate level. Deeper levels are cleared when a higher level changes.
        Headings that do not match any known prefix are treated as top-level.

        Only the heading title is stored — not the full span text. Kitab-level
        headings are single-line. Bab/fasl headings may span multiple lines,
        collected until hitting attribution verbs or a new heading prefix.
        """
        heading_line = _extract_heading_line(
            span_text, self._all_prefixes, self._top_level_prefixes, self._attribution_re,
            self._heading_stop_patterns,
        )
        self._heading_counter += 1
        heading_id = f"heading_{self._heading_counter:03d}"

        level = self._resolve_level(heading_line)
        self._state[level] = (heading_line, heading_id)

        if level in self._level_names:
            level_idx = self._level_names.index(level)
            for deeper in self._level_names[level_idx + 1:]:
                self._state.pop(deeper, None)

    def current_path(self) -> HierarchyPath:
        """Return the current hierarchy state as a HierarchyPath."""
        path: list[str] = []
        path_ids: list[str] = []

        for level_name in self._level_names:
            if level_name in self._state:
                text, hid = self._state[level_name]
                path.append(text)
                path_ids.append(hid)

        return HierarchyPath(
            path=path,
            path_ids=path_ids,
            depth=len(path),
        )

    def _resolve_level(self, heading_line: str) -> str:
        """Match a heading line to its hierarchy level via prefix lookup.

        Finds the earliest-occurring prefix in the heading text.
        This handles decorative wrappers (parentheses, asterisks) and
        leading digits without needing a separate stripping step — the
        HEADING_MARKER regex in config is the single source of truth
        for what characters can precede the keyword.
        """
        best_level = self._level_names[0] if self._level_names else "kitab"
        best_pos = len(heading_line)
        for prefix in self._all_prefixes:
            pos = heading_line.find(prefix)
            if pos != -1 and pos < best_pos:
                best_pos = pos
                best_level = self._prefix_to_level[prefix]
        return best_level


def segment(manuscript: Manuscript, config: Config) -> Manuscript:
    """Detect span boundaries, assign behavior labels, and track hierarchy.

    Joins all content page texts into one combined string so the boundary
    splitter operates across page boundaries. A hadith flowing across
    multiple pages stays as one span — page breaks are not boundaries.

    Populates manuscript.spans. Every span exits this function with a
    non-None .behavior and a non-None .hierarchy.

    Args:
        manuscript: A Manuscript with pages populated from Phase 1.
        config: The pipeline configuration with patterns and behaviors.

    Returns:
        The same Manuscript object with spans populated.

    Raises:
        SegmentError: If pattern compilation fails or the FSM enters
                      an invalid state during hierarchy tracking.
    """
    _validate_required_behaviors(config.behaviors)
    compiled_patterns = _compile_patterns(config.patterns)
    behavior_rules = _parse_behavior_rules(config.behaviors)
    boundary_re = _compile_boundary_splitter(config.patterns)
    start_thresholds = _parse_start_thresholds(config.patterns)
    heading_disqualifiers = _parse_heading_disqualifiers(config.patterns)
    min_span_chars = config.thresholds["min_span_chars"]
    attribution_re = compiled_patterns.get("ATTRIBUTION")
    level_names, prefix_to_level, all_prefixes, top_level_prefixes, heading_stop_patterns = _parse_hierarchy_levels(config.patterns)
    tracker = _KitabBabFaslTracker(
        _level_names=level_names,
        _prefix_to_level=prefix_to_level,
        _all_prefixes=all_prefixes,
        _top_level_prefixes=top_level_prefixes,
        _heading_stop_patterns=heading_stop_patterns,
        _attribution_re=attribution_re,
    )

    content_pages = [p for p in manuscript.pages if p.is_content]
    if not content_pages:
        return manuscript

    combined_text, page_starts, pages_list = _build_combined_text(content_pages)

    page_footnotes: dict[int, dict[str, str]] = {}
    for page in content_pages:
        if page.footnote:
            page_footnotes[page.page_number] = _parse_footnotes(page.footnote)

    paragraphs = _split_with_page_tracking(
        combined_text, boundary_re, page_starts, pages_list, min_span_chars,
    )

    span_index = 0
    for paragraph_text, page_start, page_end in paragraphs:
        span_id = SEGMENT__SPAN_ID__FORMAT.format(
            manifestation_id=manuscript.manifestation_id,
            index=span_index,
        )

        detected_patterns = _detect_patterns(paragraph_text, compiled_patterns)
        detected_patterns = _filter_heading_disqualifiers(
            paragraph_text, detected_patterns, heading_disqualifiers,
        )
        behavior = _route_behavior(detected_patterns, behavior_rules, start_thresholds)

        if behavior == SEGMENT__BEHAVIOR__SECTION_HEADING:
            tracker.advance(paragraph_text, span_id)

        hierarchy = tracker.current_path()

        footnote_entries: dict[str, str] = {}
        for pn in range(page_start, page_end + 1):
            footnote_entries.update(page_footnotes.get(pn, {}))
        footnote_text = _attach_footnotes(paragraph_text, footnote_entries)

        span = Span(
            span_id=span_id,
            text=paragraph_text,
            page_start=page_start,
            page_end=page_end,
            span_type=SEGMENT__SPAN__TYPE_PARAGRAPH,
            patterns=detected_patterns,
            behavior=behavior,
            hierarchy=hierarchy,
            footnote_text=footnote_text,
        )

        manuscript.spans.append(span)
        span_index += 1

    logger.info(
        "Segmented %s: %d spans from %d content pages",
        manuscript.manifestation_id,
        len(manuscript.spans),
        sum(1 for p in manuscript.pages if p.is_content),
    )
    return manuscript


def _build_combined_text(
    content_pages: list[Page],
) -> tuple[str, list[int], list[Page]]:
    """Concatenate content page texts into one string for cross-page splitting.

    Pages are joined with a single newline so the boundary splitter only
    creates breaks where actual boundary patterns appear — not at page
    boundaries. The normalize() step in Phase 1 already strips leading and
    trailing whitespace from page text, so joining with ``\\n`` never
    produces a false ``\\n\\n`` boundary.

    Args:
        content_pages: Pages with is_content=True, in order.

    Returns:
        A tuple of (combined_text, page_starts, pages_list) where:
        - combined_text: All page texts joined by single newlines.
        - page_starts: Sorted list of character offsets where each page begins.
        - pages_list: Parallel list of Page objects (same order as page_starts).
    """
    parts: list[str] = []
    page_starts: list[int] = []
    pages_list: list[Page] = []
    offset = 0

    for page in content_pages:
        if parts:
            parts.append("\n")
            offset += 1
        page_starts.append(offset)
        pages_list.append(page)
        parts.append(page.text)
        offset += len(page.text)

    return "".join(parts), page_starts, pages_list


def _page_number_at_offset(
    char_offset: int,
    page_starts: list[int],
    pages_list: list[Page],
) -> int:
    """Return the page_number of the page containing the given character offset.

    Uses binary search on the sorted page_starts list.
    """
    idx = bisect.bisect_right(page_starts, char_offset) - 1
    if idx < 0:
        idx = 0
    return pages_list[idx].page_number


def _split_with_page_tracking(
    combined_text: str,
    boundary_re: re.Pattern[str],
    page_starts: list[int],
    pages_list: list[Page],
    min_span_chars: int,
) -> list[tuple[str, int, int]]:
    """Split combined text and map each paragraph to its page range.

    Uses re.split on the combined text, then locates each resulting
    paragraph in the original string to determine which page(s) it spans.

    Args:
        combined_text: Joined page texts from _build_combined_text.
        boundary_re: Compiled boundary splitter regex.
        page_starts: Character offsets where each page starts.
        pages_list: Page objects parallel to page_starts.
        min_span_chars: Minimum character count for a valid span.

    Returns:
        List of (paragraph_text, page_start_number, page_end_number) tuples.
    """
    parts = boundary_re.split(combined_text)
    result: list[tuple[str, int, int]] = []
    search_from = 0

    for part in parts:
        if not part:
            continue

        idx = combined_text.find(part, search_from)
        if idx == -1:
            idx = search_from

        stripped = part.strip()
        if stripped and len(stripped) >= min_span_chars:
            strip_offset = part.index(stripped[0]) if stripped else 0
            text_start = idx + strip_offset
            text_end = text_start + len(stripped) - 1

            pg_start = _page_number_at_offset(text_start, page_starts, pages_list)
            pg_end = _page_number_at_offset(text_end, page_starts, pages_list)
            result.append((stripped, pg_start, pg_end))

        search_from = idx + len(part)

    return result


def _extract_heading_line(
    span_text: str,
    all_prefixes: tuple[str, ...] = (),
    top_level_prefixes: frozenset[str] = frozenset(),
    attribution_re: re.Pattern[str] | None = None,
    heading_stop_patterns: tuple[str, ...] = (),
) -> str:
    """Extract the heading title from a SECTION_HEADING span's text.

    Classical Arabic chapter headings (especially in hadith collections like
    Ibn Khuzaymah's Sahih) can be very long and wrap across multiple physical
    lines. Scans lines to find the first one with a known heading prefix.
    Top-level (kitab) titles are single-line — subsequent lines are preface
    text, not the heading name. Lower-level titles collect continuation lines
    until hitting a content boundary: a blank line, a new heading prefix,
    an attribution verb, or a heading stop pattern (e.g. الآيات indicating
    Quranic verse citations that are not part of the heading name).
    Falls back to the first non-empty line if no heading prefix is found.

    Args:
        span_text: The full text of a SECTION_HEADING span.
        all_prefixes: All heading prefixes from config, sorted longest-first.
        top_level_prefixes: Prefixes for the top hierarchy level (single-line).
        attribution_re: Compiled ATTRIBUTION pattern from config. Used to
            detect where heading text ends and narration content begins.
            None for genres without attribution patterns.
        heading_stop_patterns: Patterns that end heading continuation. When
            a continuation line contains any of these, collection stops.

    Returns:
        The heading title, with wrapped lines joined by spaces.
    """
    lines = span_text.split("\n")
    heading_start = -1

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and any(p in stripped for p in all_prefixes):
            heading_start = i
            break

    if heading_start == -1:
        for line in lines:
            stripped = line.strip()
            if stripped:
                return stripped
        return span_text.strip()

    first_heading_line = lines[heading_start].strip()

    if any(p in first_heading_line for p in top_level_prefixes):
        return first_heading_line

    collected = [first_heading_line]
    for line in lines[heading_start + 1:]:
        stripped = line.strip()
        if not stripped:
            break
        if any(p in stripped for p in all_prefixes):
            break
        if attribution_re is not None and attribution_re.search(stripped):
            break
        if heading_stop_patterns and any(sp in stripped for sp in heading_stop_patterns):
            break
        collected.append(stripped)

    return " ".join(collected)


_FOOTNOTE_MARKER_RE = re.compile(r"\((\d+)\)")
_FOOTNOTE_SPLIT_RE = re.compile(r"(?:^|\n)\s*\((\d+)\)\s*")


def _parse_footnotes(footnote_text: str) -> dict[str, str]:
    """Parse a page's footnote block into numbered entries.

    Footnotes in Shamela-format books are a single text block with
    entries like '(1) text... (2) text...'. This splits them into
    a dict keyed by the marker number.

    Args:
        footnote_text: The raw footnote text from the page.

    Returns:
        Dict mapping marker strings ('1', '2', ...) to their text.
    """
    parts = _FOOTNOTE_SPLIT_RE.split(footnote_text)
    entries: dict[str, str] = {}
    # split produces: [preamble, '1', text, '2', text, ...]
    for i in range(1, len(parts) - 1, 2):
        entries[parts[i]] = parts[i + 1].strip()
    return entries


def _attach_footnotes(span_text: str, footnote_entries: dict[str, str]) -> str | None:
    """Find (N) markers in span text and return the matching footnote entries.

    Args:
        span_text: The span's text content.
        footnote_entries: Parsed footnote entries from the page.

    Returns:
        Joined footnote text for markers found in the span, or None.
    """
    if not footnote_entries:
        return None
    markers = _FOOTNOTE_MARKER_RE.findall(span_text)
    matched = []
    seen: set[str] = set()
    for m in markers:
        if m in footnote_entries and m not in seen:
            matched.append(f"({m}) {footnote_entries[m]}")
            seen.add(m)
    return "\n".join(matched) if matched else None


def _compile_patterns(raw_patterns: list[dict[str, Any]]) -> dict[str, re.Pattern[str]]:
    """Compile all pattern regexes from config into a pattern_id → regex mapping.

    Args:
        raw_patterns: Pattern definitions from config.patterns.

    Returns:
        Dict mapping pattern_id to its compiled regex, in config order.

    Raises:
        SegmentError: If a pattern regex fails to compile.
    """
    compiled: dict[str, re.Pattern[str]] = {}
    for entry in raw_patterns:
        pattern_id = entry["id"]
        try:
            compiled[pattern_id] = re.compile(entry["regex"], re.MULTILINE)
        except re.error as exc:
            raise SegmentError(f"Pattern '{pattern_id}' has invalid regex: {exc}") from exc
    return compiled


def _parse_behavior_rules(raw_behaviors: list[dict[str, Any]]) -> list[_BehaviorRule]:
    """Parse behavior routing rules from config into structured objects.

    Rules are sorted by priority descending so the first match wins
    during routing.

    Args:
        raw_behaviors: Behavior definitions from config.behaviors.

    Returns:
        List of parsed behavior rules sorted by priority (highest first).
    """
    rules: list[_BehaviorRule] = []
    for entry in raw_behaviors:
        rules.append(_BehaviorRule(
            behavior_id=entry["id"],
            requires=set(entry.get("requires", [])),
            any_of=set(entry.get("any_of", [])),
            none_of=set(entry.get("none_of", [])),
            priority=entry.get("priority", 0),
        ))
    rules.sort(key=lambda r: r.priority, reverse=True)
    return rules


def _parse_start_thresholds(raw_patterns: list[dict[str, Any]]) -> dict[str, int]:
    """Extract start_threshold values from pattern config.

    A start_threshold on a pattern means it only counts for behavior
    routing if its earliest match starts within that many characters
    of the span's beginning. This prevents polysemous keywords that
    appear mid-span from triggering behavior rules meant for span-initial
    occurrences only.

    Args:
        raw_patterns: Pattern definitions from config.patterns.

    Returns:
        Dict mapping pattern_id to its start_threshold (chars from span start).
    """
    thresholds: dict[str, int] = {}
    for entry in raw_patterns:
        if "start_threshold" in entry:
            thresholds[entry["id"]] = int(entry["start_threshold"])
    return thresholds


def _parse_heading_disqualifiers(raw_patterns: list[dict[str, Any]]) -> list[re.Pattern[str]]:
    """Compile heading disqualifier regexes from the HEADING_MARKER pattern config.

    Heading keywords (باب, كتاب, فصل) are polysemous — باب means both
    'chapter' and 'gate', كتاب means both 'chapter' and 'letter/book'.
    Disqualifiers are regex patterns checked against the text immediately
    following the heading keyword match. When a disqualifier matches, the
    keyword is being used in its common-noun sense and HEADING_MARKER
    should not fire for behavior routing.

    Args:
        raw_patterns: Pattern definitions from config.patterns.

    Returns:
        List of compiled disqualifier regexes, or empty list if none configured.

    Raises:
        SegmentError: If a disqualifier regex fails to compile.
    """
    for entry in raw_patterns:
        if entry["id"] == SEGMENT__PATTERN__HEADING_MARKER and "heading_disqualifiers" in entry:
            compiled: list[re.Pattern[str]] = []
            for dq_regex in entry["heading_disqualifiers"]:
                try:
                    compiled.append(re.compile(dq_regex))
                except re.error as exc:
                    raise SegmentError(
                        f"Heading disqualifier regex invalid: {dq_regex!r}: {exc}"
                    ) from exc
            return compiled
    return []


def _make_non_capturing(body: str) -> str:
    """Convert capturing groups to non-capturing, preserving [] character classes.

    A naive ``re.sub`` on ``(`` corrupts character classes like ``[\\s(*0-9]``
    by injecting ``?:`` into the bracket expression. This parser skips bracket
    content and only converts ``(`` outside character classes.
    """
    out: list[str] = []
    i = 0
    n = len(body)
    while i < n:
        ch = body[i]
        if ch == '\\' and i + 1 < n:
            out.append(body[i:i + 2])
            i += 2
        elif ch == '[':
            # Copy the entire character class verbatim
            j = i + 1
            if j < n and body[j] in ('^', ']'):
                j += 1
            while j < n and body[j] != ']':
                if body[j] == '\\' and j + 1 < n:
                    j += 2
                else:
                    j += 1
            out.append(body[i:j + 1])
            i = j + 1
        elif ch == '(' and (i + 1 >= n or body[i + 1] != '?'):
            out.append('(?:')
            i += 1
        else:
            out.append(ch)
            i += 1
    return ''.join(out)


def _compile_boundary_splitter(raw_patterns: list[dict[str, Any]]) -> re.Pattern[str]:
    """Build the paragraph boundary splitter from config boundary patterns.

    Splits on a blank line (double newline) OR a single newline immediately
    before any pattern marked `is_boundary: true` in config. The lookahead
    uses horizontal-only whitespace ``[^\\S\\n]*`` so it never crosses a line
    boundary, and all capturing groups in the pattern body are converted to
    non-capturing to prevent `re.split` from emitting `None` group slots.

    Args:
        raw_patterns: Pattern definitions from config.patterns.

    Returns:
        A compiled split regex that produces one chunk per span candidate.

    Raises:
        SegmentError: If a boundary pattern regex is invalid.
    """
    lookaheads: list[str] = []
    for entry in raw_patterns:
        if not entry.get("is_boundary"):
            continue
        body = entry["regex"].lstrip("^")
        body = _make_non_capturing(body)
        lookaheads.append(rf"[^\S\n]*(?:{body})")

    if not lookaheads:
        return re.compile(r"\n\n")

    combined = "|".join(lookaheads)
    try:
        return re.compile(rf"\n\n|\n(?={combined})", re.MULTILINE)
    except re.error as exc:
        raise SegmentError(f"Boundary splitter regex failed to compile: {exc}") from exc


def _detect_patterns(
    text: str,
    compiled_patterns: dict[str, re.Pattern[str]],
) -> list[Pattern]:
    """Run all compiled pattern detectors against a span's text.

    Every regex match produces a Pattern object. A single pattern_id can
    produce multiple Pattern objects if the regex matches multiple times.

    Args:
        text: The span text to scan.
        compiled_patterns: Mapping from pattern_id to compiled regex.

    Returns:
        List of all detected Pattern objects.
    """
    detected: list[Pattern] = []
    for pattern_id, compiled_regex in compiled_patterns.items():
        for match in compiled_regex.finditer(text):
            detected.append(Pattern(
                pattern_id=pattern_id,
                matched_text=match.group(),
                char_start=match.start(),
                char_end=match.end(),
            ))
    return detected


def _filter_heading_disqualifiers(
    text: str,
    detected_patterns: list[Pattern],
    disqualifiers: list[re.Pattern[str]],
) -> list[Pattern]:
    """Remove HEADING_MARKER patterns when post-keyword text matches a disqualifier.

    Heading keywords (باب, كتاب, فصل) are polysemous in Arabic — باب means
    both 'chapter' and 'gate/door', كتاب means both 'chapter/book' and
    'letter'. When the text immediately following the keyword matches a
    narrative continuation pattern (possessive pronouns, conjunction+verb,
    conjunction+particle), the keyword is being used in its common-noun
    sense and HEADING_MARKER should not count for behavior routing.

    Args:
        text: The full span text.
        detected_patterns: All patterns detected on this span.
        disqualifiers: Compiled disqualifier regexes from config.

    Returns:
        The detected patterns list, with HEADING_MARKER removed if disqualified.
    """
    if not disqualifiers:
        return detected_patterns

    heading_matches = [p for p in detected_patterns if p.pattern_id == SEGMENT__PATTERN__HEADING_MARKER]
    if not heading_matches:
        return detected_patterns

    earliest = min(heading_matches, key=lambda p: p.char_start)
    after_text = text[earliest.char_end:]

    for dq in disqualifiers:
        if dq.match(after_text):
            return [p for p in detected_patterns if p.pattern_id != SEGMENT__PATTERN__HEADING_MARKER]

    return detected_patterns


def _route_behavior(
    detected_patterns: list[Pattern],
    behavior_rules: list[_BehaviorRule],
    start_thresholds: dict[str, int] | None = None,
) -> str:
    """Route detected patterns to a behavior label via the routing table.

    Evaluates rules in priority order (highest first). A rule matches when:
      - ALL patterns in 'requires' are present among detected pattern_ids
      - If 'any_of' is non-empty, at least ONE of those pattern_ids is present
      - NONE of the patterns in 'none_of' are present among detected pattern_ids

    Patterns with a start_threshold in config are only counted if their
    earliest match starts within that many characters of the span's beginning.
    This prevents mid-span occurrences of polysemous keywords from hijacking
    the behavior label.

    The first matching rule with non-empty requires wins. If no rule matches
    or no patterns were detected, falls back to GENERAL_PROSE.

    Args:
        detected_patterns: Patterns detected on this span.
        behavior_rules: Routing rules sorted by priority descending.
        start_thresholds: Map from pattern_id to max char_start for that
            pattern to count toward routing. None means no filtering.

    Returns:
        The behavior label string.
    """
    if start_thresholds:
        earliest: dict[str, int] = {}
        for p in detected_patterns:
            if p.pattern_id not in earliest or p.char_start < earliest[p.pattern_id]:
                earliest[p.pattern_id] = p.char_start
        detected_ids: set[str] = set()
        for pid, pos in earliest.items():
            threshold = start_thresholds.get(pid)
            if threshold is not None and pos > threshold:
                continue
            detected_ids.add(pid)
    else:
        detected_ids = {p.pattern_id for p in detected_patterns}

    if not detected_ids:
        return SEGMENT__BEHAVIOR__GENERAL_PROSE

    for rule in behavior_rules:
        if not rule.requires and not rule.any_of:
            continue

        if rule.requires and not rule.requires.issubset(detected_ids):
            continue

        if rule.any_of and not rule.any_of.intersection(detected_ids):
            continue

        if rule.none_of and rule.none_of.intersection(detected_ids):
            continue

        return rule.behavior_id

    logger.warning(
        "No behavior rule matched for patterns %s, assigning %s",
        detected_ids,
        SEGMENT__BEHAVIOR__GENERAL_PROSE,
    )
    return SEGMENT__BEHAVIOR__GENERAL_PROSE


def _validate_required_behaviors(raw_behaviors: list[dict[str, Any]]) -> None:
    """Validate that behavior IDs referenced by segment.py exist in config.

    Segment.py references GENERAL_PROSE and SECTION_HEADING by name via
    module constants. If either is missing from config, the routing and
    hierarchy tracking logic will silently malfunction.

    Args:
        raw_behaviors: Behavior definitions from config.behaviors.

    Raises:
        SegmentError: If a required behavior ID is missing from config.
    """
    behavior_ids = {entry["id"] for entry in raw_behaviors}
    required = {SEGMENT__BEHAVIOR__GENERAL_PROSE, SEGMENT__BEHAVIOR__SECTION_HEADING}
    missing = required - behavior_ids
    if missing:
        raise SegmentError(
            f"Config behaviors missing required IDs: {sorted(missing)}. "
            f"Segment phase requires: {sorted(required)}"
        )


def _parse_hierarchy_levels(
    raw_patterns: list[dict[str, Any]],
) -> tuple[tuple[str, ...], dict[str, str], tuple[str, ...], frozenset[str], tuple[str, ...]]:
    """Extract hierarchy level mappings from the HEADING_MARKER pattern config.

    Reads the hierarchy_levels and heading_stop_patterns fields from the
    HEADING_MARKER pattern to build data structures for the KitabBabFaslTracker.
    Prefixes are sorted longest-first so longer prefixes are matched before
    shorter ones.

    Args:
        raw_patterns: Pattern definitions from config.patterns.

    Returns:
        A tuple of (level_names, prefix_to_level, all_prefixes,
        top_level_prefixes, heading_stop_patterns):
        - level_names: Ordered tuple of hierarchy level names (e.g. kitab, bab, fasl)
        - prefix_to_level: Mapping from each Arabic prefix to its level name
        - all_prefixes: All prefixes sorted longest-first
        - top_level_prefixes: Frozenset of prefixes for the top hierarchy level
        - heading_stop_patterns: Patterns that end heading continuation collection
    """
    heading_entry = None
    for entry in raw_patterns:
        if entry["id"] == SEGMENT__PATTERN__HEADING_MARKER:
            heading_entry = entry
            break

    if heading_entry is None or "hierarchy_levels" not in heading_entry:
        return (), {}, (), frozenset(), ()

    hierarchy = heading_entry["hierarchy_levels"]
    level_names = tuple(hierarchy.keys())
    prefix_to_level: dict[str, str] = {}
    for level, prefixes in hierarchy.items():
        for prefix in prefixes:
            prefix_to_level[prefix] = level

    all_prefixes = tuple(sorted(prefix_to_level.keys(), key=len, reverse=True))

    top_level = level_names[0] if level_names else None
    top_level_prefixes = frozenset(
        hierarchy.get(top_level, []) if top_level else []
    )

    heading_stop_patterns = tuple(heading_entry.get("heading_stop_patterns", []))

    return level_names, prefix_to_level, all_prefixes, top_level_prefixes, heading_stop_patterns
