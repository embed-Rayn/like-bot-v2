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


def test_no_result_key_raises_parse_error():
    """Valid JSON with no result key at all should raise SearchParseError."""
    with pytest.raises(SearchParseError):
        parse_search_response('{"foo": 1}')


def test_raw_count_tracks_pre_filter_rows():
    """raw_count should reflect searchList length before filtering."""
    response_with_bad_row = '{"result": {"searchList": [{"domainIdOrBlogId": "", "logNo": 123}], "totalCount": 1, "pagePerCount": 7}}'
    page = parse_search_response(response_with_bad_row)
    assert page.raw_count == 1
    assert len(page.items) == 0
    assert page.dropped == 1


def test_row_missing_log_no_is_dropped():
    """Rows missing logNo should be dropped and tracked in raw_count."""
    response = '{"result": {"searchList": [{"domainIdOrBlogId": "blog123", "title": "test", "blogName": "test", "addDate": 123456}], "totalCount": 1, "pagePerCount": 7}}'
    page = parse_search_response(response)
    assert page.raw_count == 1
    assert len(page.items) == 0
    assert page.dropped == 1


def test_row_missing_domain_id_is_dropped():
    """Rows missing domainIdOrBlogId should be dropped and tracked in raw_count."""
    response = '{"result": {"searchList": [{"logNo": 123, "title": "test", "blogName": "test", "addDate": 123456}], "totalCount": 1, "pagePerCount": 7}}'
    page = parse_search_response(response)
    assert page.raw_count == 1
    assert len(page.items) == 0
    assert page.dropped == 1


def test_page_with_all_unusable_rows_is_distinguishable_from_empty():
    """A page where every row is unusable should be distinguishable from a genuinely empty page."""
    # All rows unusable (both missing required fields)
    response = '{"result": {"searchList": [{"title": "test"}, {"blogName": "test"}], "totalCount": 2, "pagePerCount": 7}}'
    page = parse_search_response(response)
    assert page.is_empty is True  # No usable items
    assert page.raw_count == 2  # But we had 2 rows originally
    assert page.dropped == 2

    # Genuinely empty page
    empty_page = parse_search_response('{"result": {"totalCount": 0}}')
    assert empty_page.is_empty is True
    assert empty_page.raw_count == 0
    assert empty_page.dropped == 0


def test_real_fixture_has_zero_dropped():
    """The real fixture search_page1.json should have no dropped rows."""
    page = parse_search_response(_fixture("search_page1.json"))
    assert page.raw_count == 7
    assert page.dropped == 0
    assert len(page.items) == 7
