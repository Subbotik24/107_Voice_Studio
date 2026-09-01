from voice_studio.workspace import initialize_workspace


def test_initialize_workspace_is_safe_and_repeatable(tmp_path):
    first = initialize_workspace(tmp_path)
    assert len(first["created"]) == 1
    dictionary = tmp_path / "config/dictionary.json"
    original = dictionary.read_text(encoding="utf-8")
    dictionary.write_text("custom", encoding="utf-8")
    second = initialize_workspace(tmp_path)
    assert len(second["skipped"]) == 1
    assert dictionary.read_text(encoding="utf-8") == "custom"
    third = initialize_workspace(tmp_path, overwrite=True)
    assert len(third["created"]) == 1
    assert dictionary.read_text(encoding="utf-8") == original


def test_initialize_workspace_preserves_unrelated_neighbor_tmp_file(tmp_path):
    neighbor = tmp_path / "config" / "dictionary.json.tmp"
    neighbor.parent.mkdir(parents=True)
    neighbor.write_text("user data", encoding="utf-8")
    initialize_workspace(tmp_path)
    assert (tmp_path / "config" / "dictionary.json").exists()
    assert neighbor.read_text(encoding="utf-8") == "user data"
