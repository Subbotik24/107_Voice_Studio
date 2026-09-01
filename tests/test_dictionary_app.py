import csv
import json
import threading
import time

import pytest

from voice_studio.dictionary import (
    MAX_DICTIONARY_BYTES,
    MAX_HINT_PAYLOAD_BYTES,
    MAX_HINT_TERMS,
    DictionaryRule,
    TerminologyDictionary,
    merge_preview,
)
from voice_studio.dictionary_store import DictionaryRepository


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


def test_dictionary_rules_validate_fields_and_hints():
    rule = DictionaryRule(source=" x ", target=" Target ")
    assert rule.use_as_hint is True
    assert rule.to_dict() == {
        "source": " x ",
        "target": " Target ",
        "case_sensitive": False,
        "whole_word": True,
        "use_as_hint": True,
    }
    with pytest.raises(ValueError, match="source must be a string"):
        DictionaryRule(source=1, target="x")
    with pytest.raises(ValueError, match="target must be a string"):
        DictionaryRule(source="x", target=1)
    with pytest.raises(ValueError, match="must be a boolean"):
        DictionaryRule(source="x", target="y", use_as_hint=1)


def test_hint_terms_are_ordered_bounded_and_deduplicated():
    dictionary = TerminologyDictionary(
        [
            DictionaryRule("a", " Alpha "),
            DictionaryRule("b", "alpha"),
            DictionaryRule("c", " ", use_as_hint=True),
            DictionaryRule("d", "Delta", use_as_hint=False),
        ]
    )
    assert dictionary.hint_terms() == ["Alpha"]
    assert MAX_HINT_TERMS == 256
    assert MAX_HINT_PAYLOAD_BYTES == 8192


def test_repository_managed_round_trip_and_bounded_load(tmp_path):
    repository = DictionaryRepository(tmp_path)
    dictionary = TerminologyDictionary([DictionaryRule("x", "y", use_as_hint=False)])
    repository.save_managed(dictionary)
    assert repository.load(tmp_path / "dictionary.json").to_dict() == dictionary.to_dict()
    assert repository.is_managed(tmp_path / "dictionary.json")
    assert not repository.is_managed(tmp_path / "other.json")
    (tmp_path / "dictionary.json").write_bytes(b"x" * (MAX_DICTIONARY_BYTES + 1))
    with pytest.raises(ValueError, match="dictionary file exceeds maximum size"):
        repository.load(tmp_path / "dictionary.json")


def test_csv_and_merge_preview(tmp_path):
    repository = DictionaryRepository(tmp_path)
    csv_path = tmp_path / "rules.csv"
    csv_path.write_text(
        "source,target,case_sensitive,whole_word,use_as_hint\na,b,false,true,false\n",
        encoding="utf-8",
    )
    incoming = repository.load_csv(csv_path)
    preview = merge_preview(TerminologyDictionary([DictionaryRule("a", "b")]), incoming)
    assert preview.hint_update_count == 1
    assert preview.merged.rules[0].use_as_hint is False
    out = tmp_path / "out.json"
    repository.export_json(incoming, out)
    assert json.loads(out.read_text(encoding="utf-8")) == incoming.to_dict()


def test_hint_exact_term_and_utf8_bounds(monkeypatch):
    monkeypatch.setattr("voice_studio.dictionary.MAX_HINT_TERMS", 256)
    dictionary = TerminologyDictionary([DictionaryRule(str(i), str(i)) for i in range(257)])
    assert len(dictionary.hint_terms()) == 256
    monkeypatch.setattr("voice_studio.dictionary.MAX_HINT_PAYLOAD_BYTES", 8192)
    exact = ("я" * 4094) + "a"  # 8189 bytes + separator + one-byte term = 8192
    second = "z"
    assert dictionary.__class__(
        [DictionaryRule("x", exact), DictionaryRule("y", second)]
    ).hint_terms() == [exact, second]
    over = ("я" * 4094) + "ab"  # 8190 bytes; with separator + z = 8193
    assert dictionary.__class__(
        [DictionaryRule("x", over), DictionaryRule("y", second)]
    ).hint_terms() == [over]


def test_merge_counts_conflicts_keys_and_append_order():
    existing = TerminologyDictionary(
        [
            DictionaryRule("Term", "one"),
            DictionaryRule("Case", "two", case_sensitive=True),
            DictionaryRule("word", "three", whole_word=False),
        ]
    )
    incoming = TerminologyDictionary(
        [
            DictionaryRule("Term", "one"),
            DictionaryRule("TERM", "changed"),
            DictionaryRule("Case", "new", case_sensitive=True),
            DictionaryRule("word", "three", whole_word=False, use_as_hint=False),
            DictionaryRule("append", "four"),
        ]
    )
    result = merge_preview(existing, incoming)
    assert result.exact_skipped_count == 1
    assert result.hint_update_count == 1
    assert result.added_count == 1
    assert len(result.conflicts) == 2
    assert result.conflicts[0].existing.target == "one"
    assert result.conflicts[0].incoming.target == "changed"
    assert [r.source for r in result.rules][-1] == "append"


def test_csv_rejects_extra_cells_and_uppercase_booleans(tmp_path):
    repository = DictionaryRepository(tmp_path)
    for payload in (
        "source,target,case_sensitive,whole_word,use_as_hint\na,b,TRUE,true,false\n",
        "source,target,case_sensitive,whole_word,use_as_hint\na,b,false,true,false,extra\n",
    ):
        path = tmp_path / "bad.csv"
        path.write_text(payload, encoding="utf-8")
        with pytest.raises(ValueError):
            repository.load_csv(path)


def test_csv_accepts_valid_field_larger_than_standard_csv_limit(tmp_path):
    repository = DictionaryRepository(tmp_path)
    path = tmp_path / "large.csv"
    target = "x" * 131_073
    path.write_text(
        f'source,target,case_sensitive,whole_word,use_as_hint\na,"{target}",false,true,true\n',
        encoding="utf-8",
    )
    assert repository.load_csv(path).rules[0].target == target


def test_atomic_replace_failure_preserves_destination_and_cleans_temp(tmp_path, monkeypatch):
    repository = DictionaryRepository(tmp_path)
    destination = tmp_path / "out.json"
    destination.write_text("original", encoding="utf-8")

    def fail_replace(_source, _target):
        raise OSError("replace failed")

    monkeypatch.setattr("voice_studio.dictionary_store.os.replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        repository.export_json(TerminologyDictionary([DictionaryRule("a", "b")]), destination)
    assert destination.read_text(encoding="utf-8") == "original"
    assert list(tmp_path.glob(".out.json.*.tmp")) == []


def test_managed_and_export_reparse_refusal_uses_injectable_seam(tmp_path, monkeypatch):
    repository = DictionaryRepository(tmp_path)
    monkeypatch.setattr("voice_studio.dictionary_store._reparse", lambda _path: True)
    with pytest.raises(ValueError, match="symlink/reparse"):
        repository.save_managed(TerminologyDictionary())
    with pytest.raises(ValueError, match="symlink/reparse"):
        repository.export_json(TerminologyDictionary(), tmp_path / "export.json")


@pytest.mark.parametrize("kind", ["legacy", "repository", "csv"])
def test_each_loader_accepts_exact_bound_and_rejects_over(tmp_path, monkeypatch, kind):
    if kind == "csv":
        content = b"source,target,case_sensitive,whole_word,use_as_hint\n"
        path = tmp_path / "rules.csv"
        path.write_bytes(content)

        def loader():
            return DictionaryRepository(tmp_path).load_csv(path)
    else:
        content = b"[]"
        path = tmp_path / f"{kind}.json"
        path.write_bytes(content)

        def loader():
            if kind == "legacy":
                return TerminologyDictionary.load(path)
            return DictionaryRepository(tmp_path).load(path)

    monkeypatch.setattr("voice_studio.dictionary.MAX_DICTIONARY_BYTES", len(content))
    loader()
    path.write_bytes(content + b"x")
    with pytest.raises(ValueError, match="maximum size"):
        loader()


@pytest.mark.parametrize("loader", ["legacy", "repository"])
def test_invalid_utf8_is_normalized_for_json_loaders(tmp_path, loader):
    path = tmp_path / "bad.json"
    path.write_bytes(b"\xff")
    with pytest.raises(ValueError, match="invalid dictionary JSON"):
        (
            TerminologyDictionary.load(path)
            if loader == "legacy"
            else DictionaryRepository(tmp_path).load(path)
        )


@pytest.mark.parametrize("method", ["save_managed", "export_json", "export_csv"])
def test_all_atomic_writers_preserve_existing_and_only_clean_own_temp(
    tmp_path, monkeypatch, method
):
    repository = DictionaryRepository(tmp_path)
    target = tmp_path / (
        "dictionary.json"
        if method == "save_managed"
        else "out.json"
        if method == "export_json"
        else "out.csv"
    )
    target.write_text("original", encoding="utf-8")
    unrelated = tmp_path / f".{target.name}.keep.tmp"
    unrelated.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(
        "voice_studio.dictionary_store.os.replace",
        lambda *_: (_ for _ in ()).throw(OSError("replace failed")),
    )
    with pytest.raises(OSError):
        if method == "save_managed":
            repository.save_managed(TerminologyDictionary())
        elif method == "export_json":
            repository.export_json(TerminologyDictionary(), target)
        else:
            repository.export_csv(TerminologyDictionary(), target)
    assert target.read_text(encoding="utf-8") == "original"
    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert list(tmp_path.glob(f".{target.name}.*.tmp")) == [unrelated]


def test_reparse_seam_covers_managed_load_and_both_exports(tmp_path, monkeypatch):
    repository = DictionaryRepository(tmp_path)
    repository.save_managed(TerminologyDictionary())
    monkeypatch.setattr("voice_studio.dictionary_store._reparse", lambda _path: True)
    with pytest.raises(ValueError, match="symlink/reparse"):
        repository.load(repository.managed_path)
    for method, target in (
        ("export_json", tmp_path / "x.json"),
        ("export_csv", tmp_path / "x.csv"),
    ):
        with pytest.raises(ValueError, match="symlink/reparse"):
            getattr(repository, method)(TerminologyDictionary(), target)


def test_concurrent_long_csv_parses_restore_global_limit(tmp_path):
    baseline = csv.field_size_limit()
    repository = DictionaryRepository(tmp_path)
    paths = []
    for index in range(2):
        path = tmp_path / f"long-{index}.csv"
        target = "x" * 131_073
        path.write_text(
            f'source,target,case_sensitive,whole_word,use_as_hint\na,"{target}",false,true,true\n',
            encoding="utf-8",
        )
        paths.append(path)
    barrier = threading.Barrier(2)
    results = []

    def run(path):
        barrier.wait()
        results.append(repository.load_csv(path).rules[0].target)

    threads = [threading.Thread(target=run, args=(path,)) for path in paths]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 2 and all(len(value) == 131_073 for value in results)
    assert csv.field_size_limit() == baseline


def test_csv_parser_lock_blocks_second_parser_until_first_releases(tmp_path, monkeypatch):
    baseline = csv.field_size_limit()
    repository = DictionaryRepository(tmp_path)
    paths = []
    for index in range(2):
        path = tmp_path / f"blocked-{index}.csv"
        path.write_text(
            "source,target,case_sensitive,whole_word,use_as_hint\na,b,false,true,true\n",
            encoding="utf-8",
        )
        paths.append(path)
    entered = threading.Event()
    release = threading.Event()
    calls = []
    real_reader = csv.DictReader

    def blocking_reader(*args, **kwargs):
        calls.append(threading.get_ident())
        if len(calls) == 1:
            entered.set()
            assert release.wait(2)
        return real_reader(*args, **kwargs)

    monkeypatch.setattr("voice_studio.dictionary_store.csv.DictReader", blocking_reader)
    results = []
    first = threading.Thread(target=lambda: results.append(repository.load_csv(paths[0])))
    second = threading.Thread(target=lambda: results.append(repository.load_csv(paths[1])))
    first.start()
    assert entered.wait(2)
    second.start()
    time.sleep(0.05)
    assert len(calls) == 1
    release.set()
    first.join(2)
    second.join(2)
    assert len(results) == 2
    assert csv.field_size_limit() == baseline


def test_merge_full_accounting_order_and_no_filesystem_write(tmp_path):
    existing = TerminologyDictionary(
        [
            DictionaryRule("Term", "one"),
            DictionaryRule("Sensitive", "two", case_sensitive=True),
            DictionaryRule("Word", "three", whole_word=False),
            DictionaryRule("Hint", "four"),
        ]
    )
    incoming = TerminologyDictionary(
        [
            DictionaryRule("Term", "one"),
            DictionaryRule("TERM", "changed"),
            DictionaryRule("sensitive", "new"),
            DictionaryRule("word", "three", whole_word=True),
            DictionaryRule("hint", "four"),
            DictionaryRule("Hint", "four", use_as_hint=False),
            DictionaryRule("append", "five"),
        ]
    )
    result = merge_preview(existing, incoming)
    assert result.exact_skipped_count == 1
    assert result.conflicts[0].existing == existing.rules[0]
    assert result.conflicts[0].incoming == incoming.rules[1]
    assert result.added_count == 4
    assert result.hint_update_count == 1
    assert [rule.source for rule in result.rules] == [
        "Term",
        "Sensitive",
        "Word",
        "Hint",
        "sensitive",
        "word",
        "hint",
        "append",
    ]
    assert list(tmp_path.iterdir()) == []


def test_csv_export_contract_and_reordered_header_rejection(tmp_path):
    repository = DictionaryRepository(tmp_path)
    dictionary = TerminologyDictionary([DictionaryRule("a", "b", True, False, False)])
    target = tmp_path / "export.csv"
    repository.export_csv(dictionary, target)
    lines = target.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "source,target,case_sensitive,whole_word,use_as_hint"
    assert lines[1].endswith(",true,false,false")
    bad = tmp_path / "reordered.csv"
    bad.write_text(
        "target,source,case_sensitive,whole_word,use_as_hint\nb,a,true,false,false\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="header must be exactly"):
        repository.load_csv(bad)


def test_legacy_and_repository_share_indexed_rule_error(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"replacements":[{"source":"", "target":"x"}]}', encoding="utf-8")
    with pytest.raises(ValueError) as legacy_error:
        TerminologyDictionary.load(path)
    with pytest.raises(ValueError) as repository_error:
        DictionaryRepository(tmp_path).load(path)
    assert str(legacy_error.value) == str(repository_error.value)
    assert "invalid dictionary rule 0" in str(legacy_error.value)
    assert "source cannot be empty" in str(legacy_error.value)


def test_bounded_reader_requests_only_max_plus_one(monkeypatch, tmp_path):
    from pathlib import Path

    from voice_studio.dictionary import _read_bounded_bytes

    class Handle:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def read(self, size):
            assert size == MAX_DICTIONARY_BYTES + 1
            return b"[]"

    monkeypatch.setattr(Path, "open", lambda *_args, **_kwargs: Handle())
    assert _read_bounded_bytes(tmp_path / "x") == b"[]"
