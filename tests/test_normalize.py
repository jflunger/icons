from iconsearch.ingest.normalize import Synonyms, build_text, tokens


def test_tokens():
    assert tokens("arrow-up-right-2") == ["arrow", "up", "right", "2"]
    assert tokens("Some_Name") == ["some", "name"]


def test_synonyms_expand_group_once():
    syn = Synonyms([["trash", "delete", "bin"], ["edit", "pencil"]])
    assert syn.expand(["trash"]) == ["delete", "bin"]
    assert syn.expand(["delete", "pencil"]) == ["trash", "bin", "edit"]
    assert syn.expand(["nothing"]) == []


def test_build_text_merges_and_dedupes():
    syn = Synonyms([["trash", "delete", "bin"]])
    name, tags, synonyms = build_text("trash-2", ["garbage-can"], ["Delete", "remove"], ["Actions"], syn)
    assert name == "trash 2"
    assert tags.split() == ["delete", "remove", "garbage", "can", "actions"]
    assert "bin" in synonyms.split()
    assert "delete" not in synonyms.split()  # already in tags


def test_categories_do_not_trigger_synonyms():
    syn = Synonyms([["system", "settings"]])
    _, _, synonyms = build_text("cpu", [], [], ["System"], syn)
    assert synonyms == ""
