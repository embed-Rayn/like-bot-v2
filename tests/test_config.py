from engine.config import DEFAULTS, RunConfig


def _raw(**over):
    base = {
        "account": "myblog",
        "keywords": ["헬스장"],
        "excludes": [],
        "start_date": "2026-08-29",
        "end_date": "2026-08-30",
        "blog_limit": "200",
        "likes_per_blog": "3",
        "likes_per_minute": "6",
        "dry_run": False,
    }
    base.update(over)
    return base


def test_valid_config_parses():
    cfg, errors = RunConfig.validate(_raw())
    assert errors == []
    assert cfg.blog_limit == 200
    assert cfg.likes_per_blog == 3


def test_one_bad_field_does_not_reset_the_others():
    """레거시 결함 8: except ValueError 하나가 8개 필드를 전부 되돌렸다."""
    cfg, errors = RunConfig.validate(_raw(likes_per_blog="셋"))
    assert cfg is None
    assert [e.field for e in errors] == ["likes_per_blog"]


def test_multiple_bad_fields_are_all_reported():
    cfg, errors = RunConfig.validate(_raw(likes_per_blog="x", blog_limit="-5"))
    assert cfg is None
    assert {e.field for e in errors} == {"likes_per_blog", "blog_limit"}


def test_keywords_must_not_be_empty():
    cfg, errors = RunConfig.validate(_raw(keywords=[]))
    assert cfg is None
    assert [e.field for e in errors] == ["keywords"]


def test_end_date_must_not_precede_start_date():
    cfg, errors = RunConfig.validate(_raw(start_date="2026-08-30", end_date="2026-08-29"))
    assert cfg is None
    assert [e.field for e in errors] == ["end_date"]


def test_search_query_appends_exclusions():
    cfg, errors = RunConfig.validate(_raw(excludes=["협찬", "체험단", ""]))
    assert errors == []
    assert cfg.search_query("헬스장") == "헬스장 -협찬 -체험단"


def test_defaults_are_defined_once_and_are_conservative():
    assert DEFAULTS["likes_per_blog"] == 3
    assert DEFAULTS["likes_per_minute"] < 10   # 보수적 기본값
