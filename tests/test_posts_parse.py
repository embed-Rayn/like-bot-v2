from pathlib import Path

import httpx

from engine.posts import PostsClient, parse_rss_log_nos, rss_url

FIXTURES = Path(__file__).parent / "fixtures"

CDATA_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title><![CDATA[blog]]></title>
  <link><![CDATA[https://blog.naver.com/someone/999999999999?fromRss=true]]></link>
  <item><link><![CDATA[https://blog.naver.com/someone/224395288365]]></link></item>
  <item><link><![CDATA[https://blog.naver.com/someone/224387801453?fromRss=true]]></link></item>
</channel></rss>"""


def test_rss_url():
    assert rss_url("someone") == "https://rss.blog.naver.com/someone.xml"


def test_parses_log_nos_out_of_cdata_wrapped_links():
    assert parse_rss_log_nos(CDATA_RSS) == ["224395288365", "224387801453"]


def test_channel_link_is_not_mistaken_for_a_post():
    """channel/link에는 글 번호가 없다. item만 읽어야 한다."""
    log_nos = parse_rss_log_nos(CDATA_RSS)
    assert "999999999999" not in log_nos
    assert "224395288365" in log_nos
    assert "224387801453" in log_nos


def test_real_fixture_yields_log_nos():
    log_nos = parse_rss_log_nos((FIXTURES / "rss_sample.xml").read_bytes())
    assert len(log_nos) >= 5
    assert all(n.isdigit() for n in log_nos)


def test_malformed_xml_returns_empty_list():
    assert parse_rss_log_nos(b"<not xml") == []


async def test_client_truncates_to_limit():
    transport = httpx.MockTransport(lambda r: httpx.Response(200, content=CDATA_RSS))
    async with httpx.AsyncClient(transport=transport) as http:
        assert await PostsClient(http).recent_log_nos("someone", 1) == ["224395288365"]


async def test_client_returns_empty_on_http_error():
    transport = httpx.MockTransport(lambda r: httpx.Response(404))
    async with httpx.AsyncClient(transport=transport) as http:
        assert await PostsClient(http).recent_log_nos("someone", 3) == []
