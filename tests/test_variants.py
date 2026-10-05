import pytest

from iconsearch.ingest.variants import Variant, detect, filter_sizes


@pytest.mark.parametrize("prefix,name,base,variant", [
    ("ph", "trash", "trash", "regular"),
    ("ph", "trash-bold", "trash", "bold"),
    ("ph", "trash-duotone", "trash", "duotone"),
    ("ph", "trash-simple-thin", "trash-simple", "thin"),
    ("ph", "lightbulb", "lightbulb", "regular"),
    ("ph", "trash-light", "trash", "light"),
    ("ph", "heart-fill", "heart", "fill"),
    ("tabler", "heart", "heart", "outline"),
    ("tabler", "heart-filled", "heart", "filled"),
    ("heroicons", "trash", "trash", "outline"),
    ("heroicons", "trash-solid", "trash", "solid"),
    ("heroicons", "trash-20-solid", "trash", "mini"),
    ("heroicons", "trash-16-solid", "trash", "micro"),
    ("bi", "x-circle-fill", "x-circle", "fill"),
    ("bi", "x-circle", "x-circle", "outline"),
    ("iconoir", "check-circle-solid", "check-circle", "solid"),
    ("iconoir", "check-circle", "check-circle", "regular"),
    ("lucide", "arrow-left", "arrow-left", "default"),
    ("carbon", "trash-can", "trash-can", "default"),
])
def test_detect_known_sets(prefix, name, base, variant):
    v = detect(prefix, name)
    assert (v.base_name, v.variant) == (base, variant)


@pytest.mark.parametrize("name,aliases,base,variant", [
    ("home", ["home-outline"], "home", "outline"),        # filling makes no difference
    ("home", [], "home", "filled"),
    ("home-outline", [], "home", "outline"),
    ("home-rounded", [], "home", "rounded-filled"),
    ("home-rounded", ["home-outline-rounded"], "home", "rounded"),
    ("home-outline-rounded", [], "home", "rounded"),
    ("home-sharp", [], "home", "sharp-filled"),
    ("home-outline-sharp", [], "home", "sharp"),
    ("outline", [], "outline", "filled"),
])
def test_detect_material_symbols(name, aliases, base, variant):
    assert detect("material-symbols", name, aliases=aliases) == Variant(base, variant)


def test_detect_fluent():
    assert detect("fluent", "add-circle-24-regular") == Variant("add-circle", "regular", 24)
    assert detect("fluent", "add-32-light") == Variant("add", "light", 32)
    assert detect("fluent", "weird") == Variant("weird", "regular")


def test_detect_unknown_set_uses_suffixes():
    suffixes = {"": "Outline", "solid": "Solid Style", "20-solid": "Mini"}
    assert detect("new", "cat-20-solid", suffixes) == Variant("cat", "mini")
    assert detect("new", "cat-solid", suffixes) == Variant("cat", "solid-style")
    assert detect("new", "cat", suffixes) == Variant("cat", "outline")
    assert detect("new", "cat") == Variant("cat", "default")


def test_filter_sizes_keeps_24_or_nearest():
    names = ["add-20-regular", "add-24-regular", "add-28-regular",
             "only-16-filled", "only-20-filled", "light-32-light"]
    items = [(n, detect("fluent", n)) for n in names]
    keep = filter_sizes(items, [24])
    assert keep == {"add-24-regular", "only-20-filled", "light-32-light"}


def test_filter_sizes_noop_without_keep_sizes():
    items = [("a", Variant("a", "x", 16)), ("b", Variant("a", "x", 24))]
    assert filter_sizes(items, []) == {"a", "b"}
