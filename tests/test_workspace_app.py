from hermes_voice_studio.workspace import initialize_workspace


def test_initialize_workspace_is_safe_and_repeatable(tmp_path):
    first = initialize_workspace(tmp_path)
    assert len(first["created"]) == 4
    dictionary = tmp_path / "config/dictionary.json"
    original = dictionary.read_text(encoding="utf-8")
    dictionary.write_text("custom", encoding="utf-8")
    second = initialize_workspace(tmp_path)
    assert len(second["skipped"]) == 4
    assert dictionary.read_text(encoding="utf-8") == "custom"
    third = initialize_workspace(tmp_path, overwrite=True)
    assert len(third["created"]) == 4
    assert dictionary.read_text(encoding="utf-8") == original
