"""
Tests for Phase 2: SEGMENT

Tests load the real config/sol.yaml — the single source of truth.
Every test either asserts correct output or asserts that SegmentError is raised.
No test asserts that partial output is acceptable.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from src.exceptions import SegmentError
from src.models import Manuscript, Page
from src.phases.segment import segment
from tests.phases.conftest import SOL_CONFIG as _SOL_CONFIG


def _make_manuscript(pages: list[dict] | None = None) -> Manuscript:
    """Build a Manuscript with pre-populated pages for testing."""
    if pages is None:
        pages = [{"page_number": 1, "text": "نص عربي طويل بما يكفي للاختبار", "is_content": True}]
    ms = Manuscript(work_id="test_work", manifestation_id="test_ms")
    for p in pages:
        ms.pages.append(Page(
            page_number=p["page_number"],
            page_name=str(p["page_number"]),
            text=p["text"],
            is_content=p.get("is_content", True),
        ))
    return ms


class TestSegmentSuccess:
    def test_should_return_manuscript_when_content_pages_present(self):
        ms = _make_manuscript()
        result = segment(ms, _SOL_CONFIG)
        assert result is ms

    def test_should_produce_spans_when_content_page_has_sufficient_text(self):
        ms = _make_manuscript([{"page_number": 1, "text": "نص كافٍ للاختبار في هذا الموضع"}])
        result = segment(ms, _SOL_CONFIG)
        assert len(result.spans) >= 1

    def test_should_produce_no_spans_when_all_pages_are_non_content(self):
        ms = _make_manuscript([
            {"page_number": 1, "text": "نص طويل جداً", "is_content": False},
        ])
        result = segment(ms, _SOL_CONFIG)
        assert len(result.spans) == 0

    def test_should_skip_paragraph_when_below_min_span_chars(self):
        ms = _make_manuscript([{"page_number": 1, "text": "قصير\n\nنص طويل يتجاوز الحد الأدنى للاختبار"}])
        result = segment(ms, _SOL_CONFIG)
        assert len(result.spans) == 1
        assert result.spans[0].text == "نص طويل يتجاوز الحد الأدنى للاختبار"

    def test_should_split_on_double_newline_when_page_has_multiple_paragraphs(self):
        ms = _make_manuscript([{
            "page_number": 1,
            "text": "الفقرة الأولى تحتوي على نص كافٍ\n\nالفقرة الثانية تحتوي على نص كافٍ أيضاً",
        }])
        result = segment(ms, _SOL_CONFIG)
        assert len(result.spans) == 2

    def test_should_set_behavior_non_none_on_every_span(self):
        ms = _make_manuscript([{"page_number": 1, "text": "نص عربي كافٍ لاختبار السلوك"}])
        result = segment(ms, _SOL_CONFIG)
        assert all(s.behavior is not None for s in result.spans)

    def test_should_set_hierarchy_non_none_on_every_span(self):
        ms = _make_manuscript([{"page_number": 1, "text": "نص عربي كافٍ لاختبار التسلسل الهرمي"}])
        result = segment(ms, _SOL_CONFIG)
        assert all(s.hierarchy is not None for s in result.spans)

    def test_should_format_span_id_with_manifestation_id_and_index(self):
        ms = _make_manuscript([{"page_number": 1, "text": "نص كافٍ للاختبار في هذا الموضع"}])
        ms.manifestation_id = "book_001"
        result = segment(ms, _SOL_CONFIG)
        assert result.spans[0].span_id == "book_001_s0000"

    def test_should_assign_general_prose_when_no_patterns_match(self):
        ms = _make_manuscript([{"page_number": 1, "text": "نص عادي لا يحتوي على أي أنماط محددة في التصنيف"}])
        result = segment(ms, _SOL_CONFIG)
        assert result.spans[0].behavior == "GENERAL_PROSE"

    def test_should_assign_hadith_transmission_when_attribution_detected(self):
        ms = _make_manuscript([{"page_number": 1, "text": "حدثنا أبو بكر عن عمر رضي الله عنهما قال"}])
        result = segment(ms, _SOL_CONFIG)
        assert result.spans[0].behavior == "HADITH_TRANSMISSION"

    def test_should_assign_section_heading_when_bab_detected(self):
        ms = _make_manuscript([{"page_number": 1, "text": "باب في الطهارة وأحكامها الفقهية"}])
        result = segment(ms, _SOL_CONFIG)
        assert result.spans[0].behavior == "SECTION_HEADING"

    def test_should_assign_section_heading_when_kitab_detected(self):
        ms = _make_manuscript([{"page_number": 1, "text": "كتاب الصلاة وفرائضها وسننها"}])
        result = segment(ms, _SOL_CONFIG)
        assert result.spans[0].behavior == "SECTION_HEADING"

    def test_should_assign_section_heading_when_numbered_bab_detected(self):
        ms = _make_manuscript([{"page_number": 1, "text": "22 * (باب) * ما جاء في عروج النبي إلى السماء"}])
        result = segment(ms, _SOL_CONFIG)
        assert result.spans[0].behavior == "SECTION_HEADING"

    def test_should_detect_patterns_on_span_when_regex_matches(self):
        ms = _make_manuscript([{"page_number": 1, "text": "حدثنا أبو بكر عن عمر رضي الله عنه"}])
        result = segment(ms, _SOL_CONFIG)
        span = result.spans[0]
        detected_ids = {p.pattern_id for p in span.patterns}
        assert "ATTRIBUTION" in detected_ids

    def test_should_record_match_position_when_pattern_found(self):
        ms = _make_manuscript([{"page_number": 1, "text": "حدثنا أبو بكر عن سفيان عن أبي هريرة"}])
        result = segment(ms, _SOL_CONFIG)
        span = result.spans[0]
        attr_patterns = [p for p in span.patterns if p.pattern_id == "ATTRIBUTION"]
        assert len(attr_patterns) >= 1
        assert attr_patterns[0].char_start == 0
        assert attr_patterns[0].matched_text == "حدثنا"

    def test_should_set_page_start_and_end_from_page_number(self):
        ms = _make_manuscript([{"page_number": 7, "text": "نص كافٍ في صفحة معينة للاختبار"}])
        result = segment(ms, _SOL_CONFIG)
        assert result.spans[0].page_start == 7
        assert result.spans[0].page_end == 7

    def test_should_set_span_type_to_paragraph(self):
        ms = _make_manuscript([{"page_number": 1, "text": "نص كافٍ للاختبار في هذا الموضع"}])
        result = segment(ms, _SOL_CONFIG)
        assert result.spans[0].span_type == "paragraph"


class TestHierarchyTracking:
    def test_should_set_empty_hierarchy_when_no_heading_seen(self):
        ms = _make_manuscript([{"page_number": 1, "text": "نص عادي قبل أي عنوان في الكتاب"}])
        result = segment(ms, _SOL_CONFIG)
        h = result.spans[0].hierarchy
        assert h.depth == 0
        assert h.path == []

    def test_should_set_kitab_depth_when_kitab_heading_seen(self):
        ms = _make_manuscript([{
            "page_number": 1,
            "text": "كتاب الصلاة وأحكامها الشرعية\n\nنص ما بعد كتاب الصلاة وأحكامها",
        }])
        result = segment(ms, _SOL_CONFIG)
        prose_span = result.spans[1]
        assert prose_span.hierarchy.depth == 1
        assert "كتاب الصلاة وأحكامها الشرعية" in prose_span.hierarchy.path

    def test_should_set_bab_depth_when_bab_heading_seen(self):
        ms = _make_manuscript([{
            "page_number": 1,
            "text": (
                "كتاب الصلاة وأحكامها الشرعية\n\n"
                "باب في الفرائض وأحكامها العملية\n\n"
                "نص بعد باب الفرائض يتناول أحكامها"
            ),
        }])
        result = segment(ms, _SOL_CONFIG)
        prose_span = result.spans[2]
        assert prose_span.hierarchy.depth == 2

    def test_should_set_fasl_depth_when_fasl_heading_seen(self):
        ms = _make_manuscript([{
            "page_number": 1,
            "text": (
                "كتاب الصلاة وأحكامها الشرعية\n\n"
                "باب الفرائض والأحكام العملية في الصلاة\n\n"
                "فصل في النية وشروطها عند العلماء\n\n"
                "نص كافٍ بعد فصل النية يتناول شروطها"
            ),
        }])
        result = segment(ms, _SOL_CONFIG)
        prose_span = result.spans[3]
        assert prose_span.hierarchy.depth == 3

    def test_should_clear_bab_and_fasl_when_new_kitab_seen(self):
        ms = _make_manuscript([{
            "page_number": 1,
            "text": (
                "كتاب الطهارة وأبوابها ومسائلها الفقهية\n\n"
                "باب الوضوء وفرائضه وسننه المستحبة\n\n"
                "فصل في آداب الوضوء والسنن المستحبة فيه\n\n"
                "كتاب الصلاة وأركانها وشروطها العملية\n\n"
                "نص بعد كتاب الصلاة يتناول بعض مسائلها"
            ),
        }])
        result = segment(ms, _SOL_CONFIG)
        last_span = result.spans[-1]
        assert last_span.hierarchy.depth == 1
        assert "كتاب الصلاة وأركانها وشروطها العملية" in last_span.hierarchy.path

    def test_should_clear_fasl_when_new_bab_seen(self):
        ms = _make_manuscript([{
            "page_number": 1,
            "text": (
                "كتاب الصلاة وأحكامها الشرعية العملية\n\n"
                "باب الفرائض الأول وأحكامه في الصلاة\n\n"
                "فصل في أحكام الركوع والسجود عند الأئمة\n\n"
                "باب صلاة الجماعة وأحكامها الفقهية\n\n"
                "نص بعد باب صلاة الجماعة عن أحكامها الشرعية"
            ),
        }])
        result = segment(ms, _SOL_CONFIG)
        last_span = result.spans[-1]
        assert last_span.hierarchy.depth == 2
        assert "باب صلاة الجماعة وأحكامها الفقهية" in last_span.hierarchy.path

    def test_should_assign_same_hierarchy_to_spans_between_headings(self):
        ms = _make_manuscript([{
            "page_number": 1,
            "text": (
                "باب الطهارة وأحكامها الفقهية في الشريعة\n\n"
                "نص أول في باب الطهارة يتناول أحكامها\n\n"
                "نص ثانٍ في باب الطهارة عن الوضوء وآدابه"
            ),
        }])
        result = segment(ms, _SOL_CONFIG)
        assert result.spans[1].hierarchy.path == result.spans[2].hierarchy.path

    def test_should_resolve_bab_level_when_heading_has_leading_digits(self):
        ms = _make_manuscript([{
            "page_number": 1,
            "text": (
                "كتاب الصلاة وأحكامها الشرعية\n\n"
                "22 * (باب) * ما جاء في عروج النبي إلى السماء\n\n"
                "نص بعد الباب المرقم يتناول أحكام هذا الباب"
            ),
        }])
        result = segment(ms, _SOL_CONFIG)
        prose_span = result.spans[2]
        assert prose_span.hierarchy.depth == 2

    def test_should_carry_hierarchy_across_pages_when_no_new_heading(self):
        ms = Manuscript(work_id="w", manifestation_id="m")
        ms.pages = [
            Page(page_number=1, page_name="1", text="باب الطهارة وأحكامها الفقهية الشرعية", is_content=True),
            Page(page_number=2, page_name="2", text="حدثنا أبو بكر عن عمر بن الخطاب رضي الله عنه قال", is_content=True),
        ]
        result = segment(ms, _SOL_CONFIG)
        page2_span = next(s for s in result.spans if s.page_start == 2)
        assert page2_span.hierarchy.depth == 1


class TestBehaviorRouting:
    def test_should_pick_highest_priority_behavior_when_multiple_rules_match(self):
        text = "باب ما جاء في حدثنا أبو بكر عن عمر"
        ms = _make_manuscript([{"page_number": 1, "text": text}])
        result = segment(ms, _SOL_CONFIG)
        detected = {p.pattern_id for p in result.spans[0].patterns}
        assert "HEADING_MARKER" in detected
        assert "ATTRIBUTION" in detected
        assert result.spans[0].behavior == "SECTION_HEADING"

    def test_should_require_any_of_pattern_when_biography_rule_has_any_of(self):
        ms = _make_manuscript([{
            "page_number": 1,
            "text": "الإمام أحمد بن حنبل رحمه الله عالم جليل",
        }])
        result = segment(ms, _SOL_CONFIG)
        assert result.spans[0].behavior == "BIOGRAPHY"

    def test_should_not_match_biography_when_any_of_pattern_absent(self):
        ms = _make_manuscript([{
            "page_number": 1,
            "text": "الإمام عالم كبير في الفقه والحديث المسند",
        }])
        result = segment(ms, _SOL_CONFIG)
        assert result.spans[0].behavior != "BIOGRAPHY"

    def test_should_not_shadow_specific_rule_with_empty_requires(self):
        ms = _make_manuscript([{"page_number": 1, "text": "حدثنا أبو بكر عن عمر رضي الله عنه"}])
        result = segment(ms, _SOL_CONFIG)
        assert result.spans[0].behavior == "HADITH_TRANSMISSION"

    def test_should_assign_hadith_transmission_when_quran_verse_cited_as_dalil_within_isnad(self):
        text = (
            "أخبرنا أبو طاهر ثنا أبو بكر ثنا شيخه عن فلان عن رسول الله ﵌ "
            "قال الله تعالى ولو نزلنا عليك كتابا في قرطاس فلمسوه بأيديهم"
        )
        ms = _make_manuscript([{"page_number": 1, "text": text}])
        result = segment(ms, _SOL_CONFIG)
        detected = {p.pattern_id for p in result.spans[0].patterns}
        assert "ATTRIBUTION" in detected
        assert "QURAN_REF" in detected
        assert result.spans[0].behavior == "HADITH_TRANSMISSION"

    def test_should_assign_quran_verse_when_quran_ref_present_without_attribution(self):
        text = "قال الله تعالى ولو نزلنا عليك كتابا في قرطاس فلمسوه بأيديهم وليقولن هذا سحر مبين"
        ms = _make_manuscript([{"page_number": 1, "text": text}])
        result = segment(ms, _SOL_CONFIG)
        detected = {p.pattern_id for p in result.spans[0].patterns}
        assert "QURAN_REF" in detected
        assert "ATTRIBUTION" not in detected
        assert result.spans[0].behavior == "QURAN_VERSE"

    def test_should_assign_general_prose_when_detected_patterns_have_no_rule(self):
        ms = _make_manuscript([{
            "page_number": 1,
            "text": "وقال في ذلك كلاما طويلا ليس فيه انماط تصنيفية محددة",
        }])
        result = segment(ms, _SOL_CONFIG)
        assert result.spans[0].behavior == "GENERAL_PROSE"


class TestBoundaryDetection:
    def test_should_split_on_attribution_at_line_start_when_no_double_newline(self):
        text = "نص تمهيدي في الطهارة والوضوء\nحدثنا أبو بكر عن عمر بن الخطاب رضي الله عنه قال"
        ms = _make_manuscript([{"page_number": 1, "text": text}])
        result = segment(ms, _SOL_CONFIG)
        assert len(result.spans) == 2
        assert result.spans[0].behavior == "GENERAL_PROSE"
        assert result.spans[1].behavior == "HADITH_TRANSMISSION"

    def test_should_split_on_heading_at_line_start_when_no_double_newline(self):
        text = "نص ختام الباب السابق وما فيه من أحكام\nباب في الطهارة وأحكامها الفقهية"
        ms = _make_manuscript([{"page_number": 1, "text": text}])
        result = segment(ms, _SOL_CONFIG)
        assert len(result.spans) == 2
        assert result.spans[1].behavior == "SECTION_HEADING"

    def test_should_split_on_numbered_entry_at_line_start_when_no_double_newline(self):
        text = "مقدمة الكتاب وما يتعلق بها من بيان\n1- حدثنا الأول عن شيخه الكريم المشهور"
        ms = _make_manuscript([{"page_number": 1, "text": text}])
        result = segment(ms, _SOL_CONFIG)
        assert len(result.spans) == 2

    def test_should_split_on_waw_prefixed_attribution_at_line_start(self):
        text = "نص ختام الحديث السابق وتمامه في الرواية\nوحدثنا أبو بكر عن عمر بن الخطاب رضي الله عنه"
        ms = _make_manuscript([{"page_number": 1, "text": text}])
        result = segment(ms, _SOL_CONFIG)
        assert len(result.spans) == 2
        assert result.spans[1].behavior == "HADITH_TRANSMISSION"

    def test_should_not_split_mid_isnad_when_attribution_not_at_line_start(self):
        text = "حدثنا أبو بكر قال حدثنا وكيع عن سفيان عن أبي هريرة رضي الله عنه"
        ms = _make_manuscript([{"page_number": 1, "text": text}])
        result = segment(ms, _SOL_CONFIG)
        assert len(result.spans) == 1


class TestStartThresholdFiltering:
    """Fix 1: Pattern position-awareness via start_threshold."""

    def test_should_assign_author_commentary_when_marker_near_span_start(self):
        """أقول at the beginning of the span is a real author commentary."""
        ms = _make_manuscript([{"page_number": 1, "text": "أقول إن هذا النص يحتاج إلى بيان وتوضيح للمعنى"}])
        result = segment(ms, _SOL_CONFIG)
        assert result.spans[0].behavior == "AUTHOR_COMMENTARY"

    def test_should_not_assign_author_commentary_when_marker_far_from_start(self):
        """أقول appearing mid-span inside a hadith transmission is not commentary."""
        text = "حدثنا أبو بكر عن عمر بن الخطاب رضي الله عنه قال سمعت النبي يقول أقول لكم هذا"
        ms = _make_manuscript([{"page_number": 1, "text": text}])
        result = segment(ms, _SOL_CONFIG)
        assert result.spans[0].behavior != "AUTHOR_COMMENTARY"

    def test_should_assign_hadith_when_commentary_marker_beyond_threshold(self):
        """When أقول appears far from start alongside ATTRIBUTION, hadith wins."""
        text = "حدثنا أبو بكر عن عمر بن الخطاب رضي الله عنه أقول في هذا الحديث"
        ms = _make_manuscript([{"page_number": 1, "text": text}])
        result = segment(ms, _SOL_CONFIG)
        assert result.spans[0].behavior == "HADITH_TRANSMISSION"


class TestHeadingSuffixLookahead:
    """Fix 2: HEADING_MARKER regex suffix lookahead prevents morphological false positives."""

    def test_should_not_match_heading_when_keyword_is_verb_prefix(self):
        """فصلى (he prayed) starts with فصل but is a verb, not a heading."""
        ms = _make_manuscript([{"page_number": 1, "text": "فصلى النبي ركعتين ثم سلم من الصلاة وخرج"}])
        result = segment(ms, _SOL_CONFIG)
        assert result.spans[0].behavior != "SECTION_HEADING"

    def test_should_not_match_heading_when_keyword_has_suffix(self):
        """كتابي (my book) starts with كتاب but has possessive suffix."""
        ms = _make_manuscript([{"page_number": 1, "text": "كتابي هذا في شرح المسائل الفقهية العملية"}])
        result = segment(ms, _SOL_CONFIG)
        assert result.spans[0].behavior != "SECTION_HEADING"

    def test_should_not_match_heading_when_keyword_is_prepositional(self):
        """بابنه (with his son) starts with باب but is a preposition+noun."""
        ms = _make_manuscript([{"page_number": 1, "text": "بابنه وأهله إلى المسجد وصلوا معا في جماعة"}])
        result = segment(ms, _SOL_CONFIG)
        assert result.spans[0].behavior != "SECTION_HEADING"

    def test_should_still_match_heading_when_keyword_followed_by_space(self):
        """باب followed by space is a real heading."""
        ms = _make_manuscript([{"page_number": 1, "text": "باب في الطهارة وأحكامها الفقهية"}])
        result = segment(ms, _SOL_CONFIG)
        assert result.spans[0].behavior == "SECTION_HEADING"

    def test_should_still_match_heading_when_keyword_followed_by_paren(self):
        """باب) inside decorative wrappers is a real heading."""
        ms = _make_manuscript([{"page_number": 1, "text": "22 * (باب) * ما جاء في عروج النبي إلى السماء"}])
        result = segment(ms, _SOL_CONFIG)
        assert result.spans[0].behavior == "SECTION_HEADING"


class TestHeadingStopPatterns:
    """Fix 3: heading_stop_patterns prevents verse citations from bloating hierarchy paths."""

    def test_should_stop_heading_at_verse_citation(self):
        """الآيات in a continuation line should end heading collection."""
        text = (
            "باب فضل الجهاد في سبيل الله\n"
            "الآيات قال الله تعالى كتب عليكم القتال\n"
            "حدثنا أبو بكر عن عمر بن الخطاب رضي الله عنه"
        )
        ms = _make_manuscript([{"page_number": 1, "text": text}])
        result = segment(ms, _SOL_CONFIG)
        heading_span = next(s for s in result.spans if s.behavior == "SECTION_HEADING")
        assert "الآيات" not in heading_span.hierarchy.path[0]
        assert "باب فضل الجهاد في سبيل الله" in heading_span.hierarchy.path[0]

    def test_should_collect_continuation_when_no_stop_pattern(self):
        """Continuation lines without stop patterns are collected normally."""
        text = (
            "باب ما جاء في فضل الصلاة\n"
            "على النبي صلى الله عليه وسلم\n\n"
            "نص كافٍ بعد الباب يتناول أحكام هذا الباب"
        )
        ms = _make_manuscript([{"page_number": 1, "text": text}])
        result = segment(ms, _SOL_CONFIG)
        heading_span = next(s for s in result.spans if s.behavior == "SECTION_HEADING")
        assert "على النبي صلى الله عليه وسلم" in heading_span.hierarchy.path[0]


class TestHeadingDisqualifiers:
    """Fix 4: heading_disqualifiers prevent false SECTION_HEADING from polysemous keywords.

    Arabic heading keywords are polysemous: باب = chapter/gate, كتاب = chapter/letter/book.
    When the text immediately following the keyword reveals non-heading usage
    (possessive suffixes, conjunction+verb, conjunction+particle), the keyword
    should not trigger SECTION_HEADING.
    """

    def test_should_not_match_heading_when_keyword_followed_by_possessive(self):
        """باب حصنهم = 'gate of their fortress', not a chapter heading."""
        ms = _make_manuscript([{
            "page_number": 1,
            "text": "باب حصنهم فاقتلعت وفتحوا ودخلوا إلى المدينة",
        }])
        result = segment(ms, _SOL_CONFIG)
        assert result.spans[0].behavior != "SECTION_HEADING"

    def test_should_not_match_heading_when_keyword_followed_by_fa_verb(self):
        """كتاب ففتشاها = 'letter, so they searched it', not a chapter heading."""
        ms = _make_manuscript([{
            "page_number": 1,
            "text": "كتاب ففتشاها فلم يجدوا فيه شيئا من الأوراق",
        }])
        result = segment(ms, _SOL_CONFIG)
        assert result.spans[0].behavior != "SECTION_HEADING"

    def test_should_not_match_heading_when_keyword_followed_by_wa_inna(self):
        """كتاب وإنهم = conjunction + particle, not a chapter heading."""
        ms = _make_manuscript([{
            "page_number": 1,
            "text": "كتاب وإنهم لأهل الفضل والعلم من أهل البيت",
        }])
        result = segment(ms, _SOL_CONFIG)
        assert result.spans[0].behavior != "SECTION_HEADING"

    def test_should_still_match_heading_when_followed_by_definite_noun(self):
        """باب الطهارة = real heading (definite noun after keyword)."""
        ms = _make_manuscript([{
            "page_number": 1,
            "text": "باب الطهارة وأحكامها الفقهية في الشريعة",
        }])
        result = segment(ms, _SOL_CONFIG)
        assert result.spans[0].behavior == "SECTION_HEADING"

    def test_should_still_match_heading_when_followed_by_fi(self):
        """باب في = real heading (في is a preposition, not disqualified)."""
        ms = _make_manuscript([{
            "page_number": 1,
            "text": "باب في فضل الصلاة على النبي صلى الله عليه",
        }])
        result = segment(ms, _SOL_CONFIG)
        assert result.spans[0].behavior == "SECTION_HEADING"

    def test_should_still_match_heading_when_definite_noun_ends_in_hm(self):
        """باب الفهم = real heading (الفهم has root هم, not possessive suffix)."""
        ms = _make_manuscript([{
            "page_number": 1,
            "text": "باب الفهم والعلم والحكمة عند أهل العلم",
        }])
        result = segment(ms, _SOL_CONFIG)
        assert result.spans[0].behavior == "SECTION_HEADING"

    def test_should_still_match_heading_with_decorative_wrapper(self):
        """Decorated headings bypass disqualifiers (parens after keyword)."""
        ms = _make_manuscript([{
            "page_number": 1,
            "text": "22 * (باب) * ما جاء في فضل الصلاة",
        }])
        result = segment(ms, _SOL_CONFIG)
        assert result.spans[0].behavior == "SECTION_HEADING"


class TestSegmentFailures:
    def test_should_raise_when_pattern_regex_is_invalid(self):
        bad_patterns = _SOL_CONFIG.patterns + [{"id": "BAD_PATTERN", "regex": "[invalid(regex"}]
        bad_config = replace(_SOL_CONFIG, patterns=bad_patterns)
        with pytest.raises(SegmentError, match="BAD_PATTERN"):
            segment(_make_manuscript(), bad_config)
