import pytest


@pytest.fixture(scope="module")
def fts_query(data_env):
    from iconsearch.server import fts_query
    return fts_query


@pytest.mark.parametrize("q,expected", [
    ("arr", '"arr"*'),
    ("arrow left", '"arrow" "left"*'),
    ('say "hi"', '"say" "hi"*'),
    ("a-b", '"a" "b"*'),
    ("x*", '"x"*'),
    ("prefix:name", '"prefix" "name"*'),
    ("(foo) OR bar", '"foo" "or" "bar"*'),
    ("NEAR(a b)", '"near" "a" "b"*'),
    ("snake_case", '"snake" "case"*'),
    ("", ""),
    ("  -*:()\" ", ""),
])
def test_fts_query_escaping(fts_query, q, expected):
    assert fts_query(q)[0] == expected
