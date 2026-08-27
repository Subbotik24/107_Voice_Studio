import json

import pytest

from voice_studio.dictionary import DictionaryRule, TerminologyDictionary


def test_dictionary_is_deterministic_and_respects_words():
    dictionary = TerminologyDictionary(
        [DictionaryRule(source="войс", target="VOICE", whole_word=True)]
    )
    assert dictionary.apply("войс і войсовий") == "VOICE і войсовий"
    assert dictionary.apply("войс і войсовий") == "VOICE і войсовий"
    assert dictionary.version != "none"


def test_dictionary_rejects_invalid_shape(tmp_path):
    path = tmp_path / "dictionary.json"
    path.write_text(json.dumps({"replacements": "not-a-list"}), encoding="utf-8")
    with pytest.raises(ValueError, match="must be a list"):
        TerminologyDictionary.load(path)
