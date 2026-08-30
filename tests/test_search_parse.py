from pathlib import Path

import pytest

from engine.search import JSON_PREFIX, SearchParseError, parse_search_response

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_strips_xssi_prefix_and_parses():
    page = parse_search_response(_fixture("search_page1.json"))
    assert page.per_page == 7
    assert len(page.items) == 7


def test_extracts_blog_id_and_log_no_as_strings():
    page = parse_search_response(_fixture("search_page1.json"))
    first = page.items[0]
    assert first.blog_id and isinstance(first.blog_id, str)
    assert first.log_no.isdigit()          # int로 오지만 문자열로 정규화한다


def test_total_count_of_1000_is_flagged_as_capped():
    page = parse_search_response(_fixture("search_page1.json"))
    assert page.total_count == 1000
    assert page.is_capped is True


def test_empty_page_is_detected():
    page = parse_search_response(_fixture("search_empty.json"))
    assert page.items == []
    assert page.is_empty is True


def test_parses_without_the_prefix_too():
    raw = _fixture("search_page1.json").decode("utf-8")
    body = raw.split("\n", 1)[1]
    assert not body.startswith(JSON_PREFIX)
    assert len(parse_search_response(body).items) == 7


def test_garbage_raises_parse_error():
    with pytest.raises(SearchParseError):
        parse_search_response("<html>not json</html>")


def test_missing_search_list_yields_empty_page():
    page = parse_search_response('{"result": {"totalCount": 0}}')
    assert page.is_empty is True
    assert page.total_count == 0
