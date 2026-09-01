from __future__ import annotations

import pytest

from voice_studio.editor_tools import (
    DEFAULT_FILLERS,
    FillerMatch,
    TextMatch,
    apply_replacements,
    find_filler_matches,
    find_matches,
    remove_matches,
    segment_index_for_offset,
    segment_spans,
)
from voice_studio.models import Segment


def segment(text: str, corrected: str | None = None) -> Segment:
    return Segment(start=0.0, end=1.0, text=text, corrected_text=corrected)


def slices(text: str, matches) -> list[str]:
    return [text[match.start : match.end] for match in matches]


def test_empty_or_whitespace_query_finds_nothing():
    text = "будь-який текст"
    assert find_matches(text, "") == ()
    assert find_matches(text, "   ") == ()
    assert find_matches(text, "\t\n ") == ()
    assert find_matches(text, "", whole_word=True, case_sensitive=True) == ()


def test_query_is_literal_text_not_a_regex():
    assert find_matches("aXb", "a.b") == ()
    assert find_matches("a.b aXb", "a.b") == (TextMatch(0, 3),)
    assert find_matches("cost (net)", "(net)") == (TextMatch(5, 10),)
    assert find_matches("a+b", "a+b") == (TextMatch(0, 3),)


def test_matches_are_non_overlapping_left_to_right():
    assert find_matches("aaaa", "aa") == (TextMatch(0, 2), TextMatch(2, 4))


def test_case_sensitivity_on_cyrillic():
    text = "Ем ем ЕМ"
    assert find_matches(text, "ем") == (
        TextMatch(0, 2),
        TextMatch(3, 5),
        TextMatch(6, 8),
    )
    assert find_matches(text, "ем", case_sensitive=True) == (TextMatch(3, 5),)
    assert find_matches(text, "ЕМ", case_sensitive=True) == (TextMatch(6, 8),)


def test_whole_word_versus_substring():
    text = "кіт кітик кіт-кіт"
    assert len(find_matches(text, "кіт")) == 4
    whole = find_matches(text, "кіт", whole_word=True)
    assert whole == (TextMatch(0, 3), TextMatch(10, 13), TextMatch(14, 17))
    assert find_matches("umbrella um", "um", whole_word=True) == (TextMatch(9, 11),)


def test_offsets_index_the_original_string_for_mixed_case_unicode():
    text = "ẞ straße ẞ"
    assert len(text.casefold()) != len(text)
    matches = find_matches(text, "ß")
    assert matches == (TextMatch(0, 1), TextMatch(6, 7), TextMatch(9, 10))
    assert [text[m.start : m.end] for m in matches] == ["ẞ", "ß", "ẞ"]


def test_apply_replacements_single_and_all():
    text = "ем один ем два"
    matches = find_matches(text, "ем", whole_word=True)
    assert len(matches) == 2
    assert apply_replacements(text, [matches[1]], "so") == "ем один so два"
    assert apply_replacements(text, matches, "so") == "so один so два"


def test_apply_replacements_longer_shorter_and_empty():
    text = "abc abc"
    matches = find_matches(text, "abc")
    assert apply_replacements(text, matches, "abcdef") == "abcdef abcdef"
    assert apply_replacements(text, matches, "x") == "x x"
    assert apply_replacements(text, matches, "") == " "
    assert apply_replacements(text, [], "x") == text


def test_apply_replacements_keeps_replacement_literal():
    text = "one one"
    matches = find_matches(text, "one")
    replacement = r"\1 \\ \g<0> $0"
    assert apply_replacements(text, matches, replacement) == f"{replacement} {replacement}"


@pytest.mark.parametrize(
    "matches",
    [
        [TextMatch(0, 3), TextMatch(2, 5)],
        [TextMatch(4, 6), TextMatch(0, 2)],
        [TextMatch(0, 99)],
        [TextMatch(-1, 2)],
        [TextMatch(3, 1)],
    ],
)
def test_apply_replacements_rejects_invalid_spans(matches):
    with pytest.raises(ValueError):
        apply_replacements("abcdefgh", matches, "x")


def test_segment_spans_happy_path():
    segments = [segment("перший"), segment("другий"), segment("третій")]
    document = "перший другий третій"
    assert segment_spans(document, segments) == ((0, 6), (7, 13), (14, 20))


def test_segment_spans_uses_corrected_text_and_strips():
    segments = [segment("raw", "  виправлено  "), segment("хвіст")]
    document = "виправлено хвіст"
    assert segment_spans(document, segments) == ((0, 10), (11, 16))


def test_segment_spans_aligns_duplicate_texts_sequentially():
    segments = [segment("hi"), segment("hi"), segment("hi")]
    assert segment_spans("hi hi hi", segments) == ((0, 2), (3, 5), (6, 8))


def test_segment_spans_unfindable_segment_does_not_advance_the_cursor():
    segments = [segment("відсутній"), segment("x"), segment("y")]
    assert segment_spans("x y", segments) == (None, (0, 1), (2, 3))


def test_segment_spans_marks_empty_editable_text_as_none():
    segments = [segment("один"), segment("   "), segment("два")]
    assert segment_spans("один два", segments) == ((0, 4), None, (5, 8))


def test_segment_index_for_offset_inside_boundary_and_outside():
    spans = ((0, 4), None, (5, 8))
    assert segment_index_for_offset(spans, 0) == 0
    assert segment_index_for_offset(spans, 3) == 0
    assert segment_index_for_offset(spans, 4) is None
    assert segment_index_for_offset(spans, 5) == 2
    assert segment_index_for_offset(spans, 7) == 2
    assert segment_index_for_offset(spans, 8) is None
    assert segment_index_for_offset(spans, 99) is None
    assert segment_index_for_offset((), 0) is None


def test_default_fillers_match_each_language():
    assert slices("так ем далі", find_filler_matches("так ем далі", "uk")) == ["ем"]
    assert slices("no ehm hm dal", find_filler_matches("no ehm hm dal", "cs")) == [
        "ehm",
        "hm",
    ]
    assert slices("so um uh erm hmm", find_filler_matches("so um uh erm hmm", "en")) == [
        "um",
        "uh",
        "erm",
        "hmm",
    ]


def test_hyphenated_filler_matches_as_one_unit():
    text = "так е-е далі"
    assert find_filler_matches(text, "uk") == (FillerMatch(4, 7, "е-е"),)
    assert find_filler_matches("не-е", "uk") == ()
    assert find_filler_matches("е-ех", "uk") == ()


def test_filler_match_includes_a_directly_following_comma_only():
    text = "Ем, добре"
    assert find_filler_matches(text, "uk") == (FillerMatch(0, 3, "Ем"),)
    spaced = "Ем , добре"
    assert find_filler_matches(spaced, "uk") == (FillerMatch(0, 2, "Ем"),)


def test_filler_word_keeps_the_original_casing():
    matches = find_filler_matches("UM, Ehm hmm", "en", fillers=("um", "ehm", "hmm"))
    assert [match.word for match in matches] == ["UM", "Ehm", "hmm"]


def test_unknown_language_without_custom_fillers_is_empty():
    assert find_filler_matches("um ем ehm", "de") == ()
    assert find_filler_matches("um ем ehm", "") == ()


def test_custom_fillers_override_the_language_defaults():
    text = "ну добре ем"
    matches = find_filler_matches(text, "uk", fillers=("ну",))
    assert matches == (FillerMatch(0, 2, "ну"),)
    assert find_filler_matches(text, "de", fillers=("ну", "ем")) == (
        FillerMatch(0, 2, "ну"),
        FillerMatch(9, 11, "ем"),
    )
    assert find_filler_matches(text, "uk", fillers=()) == ()


def test_aggressive_words_are_not_default_fillers():
    for words in DEFAULT_FILLERS.values():
        assert "ну" not in words
        assert "like" not in words
        assert "you know" not in words
    text = "ну like you know"
    for language in DEFAULT_FILLERS:
        assert find_filler_matches(text, language) == ()


def test_remove_matches_normalizes_only_at_the_removal_point():
    text = "ну um hello"
    assert remove_matches(text, find_filler_matches(text, "en")) == "ну hello"

    leading = "Um, hello"
    assert remove_matches(leading, find_filler_matches(leading, "en")) == "hello"

    trailing = "hello um"
    assert remove_matches(trailing, find_filler_matches(trailing, "en")) == "hello"

    both = "а ем б ем в"
    assert remove_matches(both, find_filler_matches(both, "uk")) == "а б в"


def test_remove_matches_without_matches_returns_the_same_text():
    text = "нічого не змінюється"
    assert remove_matches(text, []) is text


def test_remove_matches_removes_only_the_selected_subset():
    text = "а ем б ем в"
    matches = find_filler_matches(text, "uk")
    assert remove_matches(text, [matches[1]]) == "а ем б в"
    assert remove_matches(text, [matches[0]]) == "а б ем в"


def test_remove_matches_handles_multi_line_text():
    text = "перший ем рядок\nем другий рядок"
    assert remove_matches(text, find_filler_matches(text, "uk")) == (
        "перший рядок\nдругий рядок"
    )
    end_of_line = "добре ем\nдалі"
    assert remove_matches(end_of_line, find_filler_matches(end_of_line, "uk")) == (
        "добре\nдалі"
    )


def test_remove_matches_keeps_double_spaces_away_from_removals():
    text = "а  б ем в"
    assert remove_matches(text, find_filler_matches(text, "uk")) == "а  б в"


def test_remove_matches_rejects_invalid_spans():
    with pytest.raises(ValueError):
        remove_matches("а ем б", [FillerMatch(2, 4, "ем"), FillerMatch(0, 3, "а")])
    with pytest.raises(ValueError):
        remove_matches("а ем б", [FillerMatch(2, 99, "ем")])
