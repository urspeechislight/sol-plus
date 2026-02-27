"""
Tests for Phase 3: EXTRACT

Tests load the real config/sol.yaml — the single source of truth.
Every test either asserts correct output or asserts that ExtractError is raised.
No test asserts that partial output is acceptable.
"""

from __future__ import annotations

import re
from dataclasses import replace

import pytest

from src.exceptions import ExtractError
from src.models import Entity, HierarchyPath, Manuscript, Pattern, Span
from src.phases.extract import extract
from tests.phases.conftest import SOL_CONFIG as _SOL_CONFIG

_EMPTY_HIERARCHY = HierarchyPath(path=[], path_ids=[], depth=0)


def _make_segmented_manuscript(spans: list[Span]) -> Manuscript:
    """Build a Manuscript with pre-labeled spans ready for Phase 3."""
    ms = Manuscript(work_id="test_work", manifestation_id="test_ms")
    ms.spans = list(spans)
    return ms


def _make_span(
    behavior: str,
    patterns: list[Pattern],
    text: str,
    footnote_text: str | None = None,
) -> Span:
    """Build a Span as if Phase 2 had already labeled it."""
    return Span(
        span_id="test_ms_s0000",
        text=text,
        page_start=1,
        page_end=1,
        span_type="paragraph",
        patterns=patterns,
        behavior=behavior,
        hierarchy=_EMPTY_HIERARCHY,
        footnote_text=footnote_text,
    )


class TestExtractSuccess:
    def test_should_create_single_unit_when_whole_span_behavior(self):
        span = _make_span("GENERAL_PROSE", [], "نص عادي يتناول موضوعاً فقهياً")
        ms = _make_segmented_manuscript([span])
        result = extract(ms, _SOL_CONFIG)
        assert len(result.spans[0].units) == 1  # type: ignore[arg-type]
        assert result.spans[0].units[0].unit_type == "PROSE_UNIT"  # type: ignore[index]

    def test_should_create_two_units_when_matn_boundary_hint_found(self):
        text = "حدثنا أبو بكر قال رسول الله صلى الله عليه وسلم"
        boundary_text = "قال رسول الله"
        char_start = text.index(boundary_text)
        patterns = [
            Pattern("ATTRIBUTION", "حدثنا", 0, 6),
            Pattern("MATN_BOUNDARY_HINT", boundary_text, char_start, char_start + len(boundary_text)),
        ]
        span = _make_span("HADITH_TRANSMISSION", patterns, text)
        ms = _make_segmented_manuscript([span])
        result = extract(ms, _SOL_CONFIG)
        units = result.spans[0].units
        assert units is not None
        assert len(units) == 2
        assert units[0].unit_type == "ISNAD_UNIT"
        assert units[1].unit_type == "MATN_UNIT"

    def test_should_create_fallback_unit_when_no_matn_boundary_hint(self):
        patterns = [Pattern("ATTRIBUTION", "حدثنا", 0, 6)]
        span = _make_span("HADITH_TRANSMISSION", patterns, "حدثنا أبو بكر عن عمر بن الخطاب")
        ms = _make_segmented_manuscript([span])
        result = extract(ms, _SOL_CONFIG)
        units = result.spans[0].units
        assert units is not None
        assert len(units) == 1
        assert units[0].unit_type == "HADITH_UNIT"

    def test_should_append_footnote_unit_when_footnote_text_set(self):
        span = _make_span(
            "GENERAL_PROSE",
            [],
            "نص مع هامش (1)",
            footnote_text="(1) نص الهامش الأول",
        )
        ms = _make_segmented_manuscript([span])
        result = extract(ms, _SOL_CONFIG)
        units = result.spans[0].units
        assert units is not None
        assert len(units) == 2
        assert units[-1].unit_type == "FOOTNOTE_UNIT"
        assert units[-1].text_ar == "(1) نص الهامش الأول"

    def test_should_extract_narrator_entities_when_attribution_pattern_present(self):
        text = "حدثنا أبو بكر عن شيخه"
        patterns = [
            Pattern("ATTRIBUTION", "حدثنا", 0, 6),
            Pattern("ATTRIBUTION", "عن", 14, 16),
        ]
        span = _make_span("HADITH_TRANSMISSION", patterns, text)
        ms = _make_segmented_manuscript([span])
        result = extract(ms, _SOL_CONFIG)
        entities = result.spans[0].entities
        assert entities is not None
        types = {e.entity_type for e in entities}
        assert "NARRATOR_NAME" in types
        assert "NARRATOR_TRANSMISSION_VERB" not in types
        narrator_names = [e.text for e in entities if e.entity_type == "NARRATOR_NAME"]
        assert "أبو بكر" in narrator_names

    def test_should_extract_narrator_names_between_attribution_patterns(self):
        text = "حدثنا محمد بن عبدالله عن أحمد بن حنبل قال رسول الله"
        matn_start = text.index("قال رسول الله")
        patterns = [
            Pattern("ATTRIBUTION", "حدثنا", 0, 6),
            Pattern("ATTRIBUTION", "عن", 22, 24),
            Pattern("MATN_BOUNDARY_HINT", "قال رسول الله", matn_start, matn_start + 14),
        ]
        span = _make_span("HADITH_TRANSMISSION", patterns, text)
        ms = _make_segmented_manuscript([span])
        result = extract(ms, _SOL_CONFIG)
        entities = result.spans[0].entities
        assert entities is not None
        narrator_names = [e.text for e in entities if e.entity_type == "NARRATOR_NAME"]
        assert len(narrator_names) == 2
        assert "محمد بن عبدالله" in narrator_names
        assert "أحمد بن حنبل" in narrator_names

    def test_should_embed_genealogy_in_narrator_name_when_ibn_present(self):
        patterns = [
            Pattern("ATTRIBUTION", "حدثنا", 0, 6),
            Pattern("GENEALOGY_CHAIN", "ابن", 15, 18),
        ]
        span = _make_span("HADITH_TRANSMISSION", patterns, "حدثنا أبو بكر ابن عياش")
        ms = _make_segmented_manuscript([span])
        result = extract(ms, _SOL_CONFIG)
        entities = result.spans[0].entities
        assert entities is not None
        types = {e.entity_type for e in entities}
        assert "GENEALOGY_LINK" not in types
        narrator_names = [e.text for e in entities if e.entity_type == "NARRATOR_NAME"]
        assert any("ابن" in name for name in narrator_names)

    def test_should_extract_person_entities_when_person_ref_pattern_present(self):
        patterns = [
            Pattern("PERSON_REF", "الإمام", 0, 6),
            Pattern("DEATH_MARKER", "توفي", 20, 24),
        ]
        span = _make_span("BIOGRAPHY", patterns, "الإمام أحمد بن حنبل توفي سنة مئتين")
        ms = _make_segmented_manuscript([span])
        result = extract(ms, _SOL_CONFIG)
        entities = result.spans[0].entities
        assert entities is not None
        types = {e.entity_type for e in entities}
        assert "PERSON_TITLE" in types

    def test_should_extract_death_and_birth_markers_when_date_patterns_present(self):
        patterns = [
            Pattern("PERSON_REF", "الحافظ", 0, 6),
            Pattern("DEATH_MARKER", "توفي", 20, 24),
            Pattern("BIRTH_MARKER", "ولد", 40, 43),
        ]
        span = _make_span(
            "BIOGRAPHY",
            patterns,
            "الحافظ الكبير المشهور توفي رحمه الله ولد في بغداد",
        )
        ms = _make_segmented_manuscript([span])
        result = extract(ms, _SOL_CONFIG)
        entities = result.spans[0].entities
        assert entities is not None
        types = {e.entity_type for e in entities}
        assert "DEATH_MARKER" in types
        assert "BIRTH_MARKER" in types

    def test_should_flatten_units_across_spans_via_manuscript_property(self):
        spans = [
            _make_span("GENERAL_PROSE", [], "النص الأول في الكتاب المبارك"),
            _make_span("GENERAL_PROSE", [], "النص الثاني في الكتاب المبارك"),
        ]
        spans[1].span_id = "test_ms_s0001"
        ms = _make_segmented_manuscript(spans)
        result = extract(ms, _SOL_CONFIG)
        assert len(result.units) == 2

    def test_should_flatten_entities_across_spans_via_manuscript_property(self):
        spans = [
            _make_span(
                "BIOGRAPHY",
                [Pattern("PERSON_REF", "الإمام", 0, 6), Pattern("DEATH_MARKER", "توفي", 10, 14)],
                "الإمام عالم توفي في المدينة",
            ),
            _make_span(
                "BIOGRAPHY",
                [Pattern("PERSON_REF", "الشيخ", 0, 5), Pattern("DEATH_MARKER", "توفي", 10, 14)],
                "الشيخ فقيه توفي في مكة المكرمة",
            ),
        ]
        spans[1].span_id = "test_ms_s0001"
        ms = _make_segmented_manuscript(spans)
        result = extract(ms, _SOL_CONFIG)
        assert len(result.entities) >= 4

    def test_should_assign_sequential_unit_ids_across_spans(self):
        spans = [
            _make_span("GENERAL_PROSE", [], "النص الأول في الكتاب"),
            _make_span("GENERAL_PROSE", [], "النص الثاني في الكتاب"),
        ]
        spans[1].span_id = "test_ms_s0001"
        ms = _make_segmented_manuscript(spans)
        result = extract(ms, _SOL_CONFIG)
        assert result.units[0].unit_id == "test_ms_u0000"
        assert result.units[1].unit_id == "test_ms_u0001"

    def test_should_assign_scoped_entity_ids_within_span(self):
        text = "حدثنا أبو بكر عن عياش الكوفي"
        patterns = [
            Pattern("ATTRIBUTION", "حدثنا", 0, 6),
            Pattern("ATTRIBUTION", "عن", 14, 16),
        ]
        span = _make_span("HADITH_TRANSMISSION", patterns, text)
        ms = _make_segmented_manuscript([span])
        result = extract(ms, _SOL_CONFIG)
        entities = result.spans[0].entities
        assert entities is not None
        ids = [e.entity_id for e in entities]
        assert ids[0] == "test_ms_s0000_e00"
        assert ids[1] == "test_ms_s0000_e01"

    def test_should_use_fallback_when_matn_boundary_hint_at_position_zero(self):
        text = "قال رسول الله صلى الله عليه وسلم"
        patterns = [Pattern("MATN_BOUNDARY_HINT", text[:4], 0, 4)]
        span = _make_span("HADITH_TRANSMISSION", patterns, text)
        ms = _make_segmented_manuscript([span])
        result = extract(ms, _SOL_CONFIG)
        units = result.spans[0].units
        assert units is not None
        assert len(units) == 1
        assert units[0].unit_type == "HADITH_UNIT"

    def test_should_not_create_footnote_unit_when_footnote_text_is_empty(self):
        span = _make_span("GENERAL_PROSE", [], "نص مع هامش فارغ", footnote_text="")
        ms = _make_segmented_manuscript([span])
        result = extract(ms, _SOL_CONFIG)
        units = result.spans[0].units
        assert units is not None
        assert len(units) == 1
        assert units[0].unit_type == "PROSE_UNIT"


    def test_should_cap_last_narrator_at_speech_verb_when_no_matn_boundary(self):
        """Span #5 regression: without MATN_BOUNDARY_HINT the last narrator
        name must stop at the first SPEECH_VERB_GENERIC after the last
        ATTRIBUTION, not extend to the end of the span."""
        text = (
            "حدثنا أبو يعقوب ثنا المعتمر بن سليمان "
            "عن يحيى بن يعمر قال قلت يعني لعبد الله بن عمر"
        )
        qal_pos = text.index("قال")
        patterns = [
            Pattern("ATTRIBUTION", "حدثنا", 0, 6),
            Pattern("ATTRIBUTION", "ثنا", text.index("ثنا"), text.index("ثنا") + 3),
            Pattern("ATTRIBUTION", "عن", text.index("عن"), text.index("عن") + 2),
            Pattern("SPEECH_VERB_GENERIC", "قال", qal_pos, qal_pos + 3),
        ]
        span = _make_span("HADITH_TRANSMISSION", patterns, text)
        ms = _make_segmented_manuscript([span])
        result = extract(ms, _SOL_CONFIG)
        entities = result.spans[0].entities
        assert entities is not None
        narrator_names = [e.text for e in entities if e.entity_type == "NARRATOR_NAME"]
        # Last narrator must be just the name, not the matn text
        assert "يحيى بن يعمر" in narrator_names
        for name in narrator_names:
            assert "قال" not in name, f"Narrator name leaked into matn: {name!r}"

    def test_should_use_full_span_when_no_speech_verb_and_no_matn_boundary(self):
        """When there is no MATN_BOUNDARY_HINT and no SPEECH_VERB_GENERIC,
        isnad_end should fall back to len(span.text)."""
        text = "حدثنا أبو بكر عن عمر بن الخطاب"
        patterns = [
            Pattern("ATTRIBUTION", "حدثنا", 0, 6),
            Pattern("ATTRIBUTION", "عن", 14, 16),
        ]
        span = _make_span("HADITH_TRANSMISSION", patterns, text)
        ms = _make_segmented_manuscript([span])
        result = extract(ms, _SOL_CONFIG)
        entities = result.spans[0].entities
        assert entities is not None
        narrator_names = [e.text for e in entities if e.entity_type == "NARRATOR_NAME"]
        assert "عمر بن الخطاب" in narrator_names

    def test_should_ignore_mid_chain_speech_verb_for_cap(self):
        """A قال between two ATTRIBUTION verbs is a chain connector, not
        the matn boundary. Only speech verbs after the last ATTRIBUTION count."""
        text = "حدثنا أحمد قال حدثنا يحيى عن مالك"
        patterns = [
            Pattern("ATTRIBUTION", "حدثنا", 0, 6),
            Pattern("SPEECH_VERB_GENERIC", "قال", text.index("قال"), text.index("قال") + 3),
            Pattern("ATTRIBUTION", "حدثنا", text.index("حدثنا", 7), text.index("حدثنا", 7) + 6),
            Pattern("ATTRIBUTION", "عن", text.index("عن"), text.index("عن") + 2),
        ]
        span = _make_span("HADITH_TRANSMISSION", patterns, text)
        ms = _make_segmented_manuscript([span])
        result = extract(ms, _SOL_CONFIG)
        entities = result.spans[0].entities
        assert entities is not None
        narrator_names = [e.text for e in entities if e.entity_type == "NARRATOR_NAME"]
        # مالك should be extracted — the mid-chain قال should not cap it
        assert "مالك" in narrator_names

    def test_should_split_when_boundary_uses_anna_rasul_allah(self):
        text = "حدثنا سفيان عن الزهري أن رسول الله صلى الله عليه وسلم"
        boundary_text = "أن رسول الله"
        char_start = text.index(boundary_text)
        patterns = [
            Pattern("ATTRIBUTION", "حدثنا", 0, 6),
            Pattern("ATTRIBUTION", "عن", text.index("عن"), text.index("عن") + 2),
            Pattern("MATN_BOUNDARY_HINT", boundary_text, char_start, char_start + len(boundary_text)),
        ]
        span = _make_span("HADITH_TRANSMISSION", patterns, text)
        ms = _make_segmented_manuscript([span])
        result = extract(ms, _SOL_CONFIG)
        units = result.spans[0].units
        assert units is not None
        assert len(units) == 2
        assert units[0].unit_type == "ISNAD_UNIT"
        assert units[1].unit_type == "MATN_UNIT"

    def test_should_split_when_boundary_uses_kana_al_nabi(self):
        text = "حدثنا عبدالله بن مسعود كان النبي صلى الله عليه وسلم يقول"
        boundary_text = "كان النبي"
        char_start = text.index(boundary_text)
        patterns = [
            Pattern("ATTRIBUTION", "حدثنا", 0, 6),
            Pattern("MATN_BOUNDARY_HINT", boundary_text, char_start, char_start + len(boundary_text)),
        ]
        span = _make_span("HADITH_TRANSMISSION", patterns, text)
        ms = _make_segmented_manuscript([span])
        result = extract(ms, _SOL_CONFIG)
        units = result.spans[0].units
        assert units is not None
        assert len(units) == 2
        assert units[0].unit_type == "ISNAD_UNIT"
        assert units[1].unit_type == "MATN_UNIT"

    def test_should_split_when_boundary_uses_anna_al_nabi(self):
        text = "حدثنا مالك عن نافع أن النبي صلى الله عليه وسلم نهى"
        boundary_text = "أن النبي"
        char_start = text.index(boundary_text)
        patterns = [
            Pattern("ATTRIBUTION", "حدثنا", 0, 6),
            Pattern("ATTRIBUTION", "عن", text.index("عن"), text.index("عن") + 2),
            Pattern("MATN_BOUNDARY_HINT", boundary_text, char_start, char_start + len(boundary_text)),
        ]
        span = _make_span("HADITH_TRANSMISSION", patterns, text)
        ms = _make_segmented_manuscript([span])
        result = extract(ms, _SOL_CONFIG)
        units = result.spans[0].units
        assert units is not None
        assert len(units) == 2
        assert units[0].unit_type == "ISNAD_UNIT"
        assert units[1].unit_type == "MATN_UNIT"

    def test_should_split_when_boundary_uses_yarfauhu(self):
        text = "حدثنا أبو هريرة يرفعه إلى النبي"
        boundary_text = "يرفعه"
        char_start = text.index(boundary_text)
        patterns = [
            Pattern("ATTRIBUTION", "حدثنا", 0, 6),
            Pattern("MATN_BOUNDARY_HINT", boundary_text, char_start, char_start + len(boundary_text)),
        ]
        span = _make_span("HADITH_TRANSMISSION", patterns, text)
        ms = _make_segmented_manuscript([span])
        result = extract(ms, _SOL_CONFIG)
        units = result.spans[0].units
        assert units is not None
        assert len(units) == 2
        assert units[0].unit_type == "ISNAD_UNIT"
        assert units[1].unit_type == "MATN_UNIT"


    def test_should_ignore_matn_boundary_hint_deep_in_narrative(self):
        """Regression: narrative hadiths have a short isnad followed by a long
        story. 'قال رسول الله' appearing inside the narrative must not be used
        as the isnad/matn split point. The speech-verb cap should take priority."""
        text = (
            "وعن سلمة بن الأكوع قال خرجنا مع رسول الله إلى خيبر "
            "فقال رسول الله من هذا السائق"
        )
        qal_pos = text.index("قال")  # first قال right after the isnad
        # "قال رسول الله" deep in the narrative (inside "فقال رسول الله")
        deep_hint_pos = text.index("قال رسول الله", qal_pos + 3)
        patterns = [
            Pattern("ATTRIBUTION", "وعن", 0, 3),
            Pattern("SPEECH_VERB_GENERIC", "قال", qal_pos, qal_pos + 3),
            Pattern("MATN_BOUNDARY_HINT", "قال رسول الله", deep_hint_pos, deep_hint_pos + 14),
        ]
        span = _make_span("HADITH_TRANSMISSION", patterns, text)
        ms = _make_segmented_manuscript([span])
        result = extract(ms, _SOL_CONFIG)

        # Narrator extraction: should extract just the name, not the narrative
        entities = result.spans[0].entities
        assert entities is not None
        narrator_names = [e.text for e in entities if e.entity_type == "NARRATOR_NAME"]
        assert len(narrator_names) == 1
        assert "سلمة بن الأكوع" in narrator_names[0]
        assert "خيبر" not in narrator_names[0]

        # Unit split: should split at the speech verb, not the deep hint
        units = result.spans[0].units
        assert units is not None
        assert len(units) == 2
        assert units[0].unit_type == "ISNAD_UNIT"
        assert units[1].unit_type == "MATN_UNIT"
        assert "سلمة بن الأكوع" in units[0].text_ar
        assert "خيبر" in units[1].text_ar

    def test_should_not_extract_narrator_from_false_attribution_inside_word(self):
        """Regression: سمعنا inside تسمعنا must not match ATTRIBUTION.
        This tests that the word-boundary fix in the ATTRIBUTION regex
        prevents false narrator extraction."""
        text = "وعن سلمة قال ألا تسمعنا من هنيهاتك"
        qal_pos = text.index("قال")
        patterns = [
            Pattern("ATTRIBUTION", "وعن", 0, 3),
            Pattern("SPEECH_VERB_GENERIC", "قال", qal_pos, qal_pos + 3),
            # No ATTRIBUTION for "سمعنا" inside "تسمعنا" — regex fix prevents it
        ]
        span = _make_span("HADITH_TRANSMISSION", patterns, text)
        ms = _make_segmented_manuscript([span])
        result = extract(ms, _SOL_CONFIG)
        entities = result.spans[0].entities
        assert entities is not None
        narrator_names = [e.text for e in entities if e.entity_type == "NARRATOR_NAME"]
        assert len(narrator_names) == 1
        assert "سلمة" in narrator_names[0]
        # The name must not be split at تسمعنا
        assert "هنيهاتك" not in narrator_names[0]


class TestMatnBoundaryRegex:
    """Verify that the MATN_BOUNDARY_HINT regex matches all expected boundary forms."""

    @pytest.fixture()
    def matn_regex(self) -> re.Pattern[str]:
        pattern_entry = next(
            p for p in _SOL_CONFIG.patterns if p["id"] == "MATN_BOUNDARY_HINT"
        )
        return re.compile(pattern_entry["regex"])

    @pytest.mark.parametrize(
        "text",
        [
            # verb + رسول الله (8 forms)
            "قال رسول الله",
            "عن رسول الله",
            "أن رسول الله",
            "كان رسول الله",
            "أمر رسول الله",
            "نهى رسول الله",
            "سئل رسول الله",
            "رأيت رسول الله",
            # verb + النبي (8 forms)
            "قال النبي",
            "عن النبي",
            "أن النبي",
            "كان النبي",
            "أمر النبي",
            "نهى النبي",
            "سئل النبي",
            "رأيت النبي",
            # standalone markers
            "مرفوعا",
            "رفعه",
            "يرفعه",
            "يبلغ به",
        ],
    )
    def test_should_match(self, matn_regex: re.Pattern[str], text: str) -> None:
        assert matn_regex.search(text), f"Expected MATN_BOUNDARY_HINT to match: {text!r}"

    @pytest.mark.parametrize(
        "text",
        [
            "أنس بن مالك",  # أن must be followed by space + prophet ref
            "كان الرجل",  # كان + non-prophet
            "عن أبيه",  # عن + non-prophet
        ],
    )
    def test_should_not_match(self, matn_regex: re.Pattern[str], text: str) -> None:
        assert not matn_regex.search(text), f"Expected MATN_BOUNDARY_HINT NOT to match: {text!r}"


class TestAttributionRegex:
    """Verify ATTRIBUTION regex word boundaries prevent false matches."""

    @pytest.fixture()
    def attribution_regex(self) -> re.Pattern[str]:
        pattern_entry = next(
            p for p in _SOL_CONFIG.patterns if p["id"] == "ATTRIBUTION"
        )
        return re.compile(pattern_entry["regex"])

    @pytest.mark.parametrize(
        "text",
        [
            "حدثنا أبو بكر",
            "وحدثنا أبو بكر",
            "أخبرنا الشيخ",
            "عن مالك",
            "وعن أنس",
            "روى عنه",
            "سمعت الشيخ",
            "سمعنا من فلان",
            "ثنا المعتمر",
        ],
    )
    def test_should_match(self, attribution_regex: re.Pattern[str], text: str) -> None:
        assert attribution_regex.search(text), f"Expected ATTRIBUTION to match: {text!r}"

    @pytest.mark.parametrize(
        "text,description",
        [
            ("ألا تسمعنا", "سمعنا embedded in تسمعنا"),
            ("يسمعنا الناس", "سمعنا embedded in يسمعنا"),
        ],
    )
    def test_should_not_match_inside_word(
        self, attribution_regex: re.Pattern[str], text: str, description: str,
    ) -> None:
        assert not attribution_regex.search(text), (
            f"ATTRIBUTION should not match inside word: {description} in {text!r}"
        )


class TestExtractFailures:
    def test_should_raise_when_behavior_has_no_atomicizer_rule(self):
        bad_atomicizers = {k: v for k, v in _SOL_CONFIG.atomicizers.items() if k != "GENERAL_PROSE"}
        bad_config = replace(_SOL_CONFIG, atomicizers=bad_atomicizers)
        span = _make_span("GENERAL_PROSE", [], "نص عادي بلا أنماط محددة")
        ms = _make_segmented_manuscript([span])
        with pytest.raises(ExtractError, match="No atomicizer rule"):
            extract(ms, bad_config)

    def test_should_raise_when_atomicizer_strategy_is_unknown(self):
        bad_atomicizers = {
            **_SOL_CONFIG.atomicizers,
            "GENERAL_PROSE": {"strategy": "nonexistent_strategy", "unit_type": "PROSE_UNIT"},
        }
        bad_config = replace(_SOL_CONFIG, atomicizers=bad_atomicizers)
        span = _make_span("GENERAL_PROSE", [], "نص عادي بلا أنماط محددة")
        ms = _make_segmented_manuscript([span])
        with pytest.raises(ExtractError, match="Unknown atomicizer strategy"):
            extract(ms, bad_config)

    def test_should_raise_when_content_span_produces_zero_units(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "src.phases.extract._STRATEGY_DISPATCH",
            {"whole_span": lambda *args: [], "sanad_matn_split": lambda *args: []},
        )
        span = _make_span("GENERAL_PROSE", [], "نص عربي طويل بما يكفي للاختبار")
        ms = _make_segmented_manuscript([span])
        with pytest.raises(ExtractError, match="zero units"):
            extract(ms, _SOL_CONFIG)

    def test_should_raise_when_entity_type_is_invalid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def bad_extractor(span: Span) -> list[Entity]:
            return [Entity(
                entity_id="",
                entity_type="INVENTED_NONEXISTENT_TYPE",
                text="test",
                char_start=0,
                char_end=4,
            )]

        from src.extractors import EXTRACTOR_REGISTRY as _real_registry
        monkeypatch.setattr(
            "src.phases.extract.EXTRACTOR_REGISTRY",
            {**_real_registry, "narrator_extractor": bad_extractor},
        )
        patterns = [Pattern("ATTRIBUTION", "حدثنا", 0, 6)]
        span = _make_span("HADITH_TRANSMISSION", patterns, "حدثنا رجل عن شيخه الكريم")
        ms = _make_segmented_manuscript([span])
        with pytest.raises(ExtractError, match="Unknown entity_type"):
            extract(ms, _SOL_CONFIG)

    def test_should_raise_when_extractor_name_not_in_registry(self):
        bad_extractors = {
            **_SOL_CONFIG.extractors,
            "GENERAL_PROSE": ["nonexistent_extractor_name"],
        }
        bad_config = replace(_SOL_CONFIG, extractors=bad_extractors)
        span = _make_span("GENERAL_PROSE", [], "نص عادي بلا أنماط محددة")
        ms = _make_segmented_manuscript([span])
        with pytest.raises(ExtractError, match="Unknown extractor"):
            extract(ms, bad_config)

    def test_should_raise_when_span_behavior_is_none(self):
        span = replace(_make_span("GENERAL_PROSE", [], "نص عادي"), behavior=None)
        ms = _make_segmented_manuscript([span])
        with pytest.raises(ExtractError, match="has no behavior"):
            extract(ms, _SOL_CONFIG)
