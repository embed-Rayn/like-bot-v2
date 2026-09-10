from engine.like import classify_button_state, is_confirmed_liked, post_url
from engine.models import LikeOutcome


def test_post_url():
    assert post_url("someone", "224395288365") == \
        "https://blog.naver.com/someone/224395288365"


# ---- 상태 판정은 aria-pressed로 한다 (2026-09-10 실측) ----
# 예전에는 class의 on/off 토큰을 봤다. 그런데 이 버튼은 aria-haspopup="true"인
# 리액션 레이어 열기 버튼이라, 레이어가 열리거나 아이콘 애니메이션이 도는
# 동안에도 class에 `on`이 붙는다. 공감이 401로 거부된 시도 21건이 전부
# 그 `on`을 보고 SUCCESS로 기록됐다 (run-20260910-195029).


def test_not_pressed_is_clickable():
    assert classify_button_state("false") is None


def test_pressed_means_already_liked():
    assert classify_button_state("true") is LikeOutcome.ALREADY_LIKED


def test_missing_aria_pressed_is_an_error():
    """속성이 아예 없다 = 위젯 구조가 바뀌었다. 조용히 넘기지 않는다."""
    assert classify_button_state(None) is LikeOutcome.ERROR


def test_unknown_aria_pressed_value_is_an_error():
    assert classify_button_state("mixed") is LikeOutcome.ERROR


def test_class_tokens_no_longer_decide_the_state():
    """레이어가 열린 것만으로 붙는 `on`을 '공감함'으로 읽어서는 안 된다."""
    assert classify_button_state("u_likeit_button _face on") is LikeOutcome.ERROR


# ---- CRITICAL 1: 클릭 확인 술어 (is_confirmed_liked) ----
# 응답이 아직 돌아오지 않아 aria-pressed가 false로 남아 있는 동안은 '확인
# 안 됨'으로 취급해야 폴링이 계속된다. 확인됐다고 오판하면 실제로는
# 성공했는지 알 수 없는 상태를 SUCCESS로 잘못 보고하게 된다.


def test_pressed_is_confirmed_liked():
    assert is_confirmed_liked("true") is True


def test_not_pressed_is_not_yet_confirmed():
    """응답이 아직 안 왔다는 뜻 — 계속 기다려야 한다."""
    assert is_confirmed_liked("false") is False


def test_missing_attribute_is_not_confirmed():
    assert is_confirmed_liked(None) is False


def test_layer_open_class_is_not_a_confirmation():
    assert is_confirmed_liked("u_likeit_button _face on") is False


# ---- 공감 API 응답 판정 (2026-09-10 실측) ----
# 클릭이 실제로 부르는 API가 유일하게 권위 있는 신호다. DOM은 레이어가
# 열리기만 해도 눌린 것처럼 보인다.


REJECTED_BODY = (
    '/**/jQuery32108881766913008888_1789038670379({"statusCode":401,'
    '"errorCode":4010,"message":"로그인 하신 후 이용해 주시기 바랍니다.",'
    '"moreInfos":null});'
)


def test_status_code_is_read_out_of_the_jsonp_body():
    from engine.like import parse_like_api_status

    assert parse_like_api_status(REJECTED_BODY) == 401


def test_body_without_a_status_code_yields_none():
    from engine.like import parse_like_api_status

    assert parse_like_api_status('{"reactions":[]}') is None


def test_rejection_401_is_reported_as_not_logged_in():
    """네이버가 '로그인 하신 후 이용해 주시기 바랍니다'로 거부한 경우.
    고칠 것은 셀렉터가 아니라 로그인이다 — 러너는 여기서 즉시 멈춘다."""
    from engine.like import classify_like_api_status, parse_like_api_status

    assert classify_like_api_status(parse_like_api_status(REJECTED_BODY)) \
        is LikeOutcome.NOT_LOGGED_IN


def test_no_rejection_leaves_the_verdict_to_the_dom():
    from engine.like import classify_like_api_status

    assert classify_like_api_status(None) is None
    assert classify_like_api_status(200) is None


def test_restriction_codes_are_reported_as_blocked():
    from engine.like import classify_like_api_status

    assert classify_like_api_status(429) is LikeOutcome.BLOCKED
    assert classify_like_api_status(403) is LikeOutcome.BLOCKED


def test_other_failures_are_errors():
    from engine.like import classify_like_api_status

    assert classify_like_api_status(500) is LikeOutcome.ERROR


def test_like_api_url_must_match_this_post_and_be_a_post_request():
    from engine.like import is_like_api_url

    url = ("https://apis.naver.com/blogserver/like/v1/services/BLOG/contents/"
           "show2217_224406140792?suppress_response_codes=true&_method=POST&pool=blogid")
    assert is_like_api_url(url, "show2217", "224406140792") is True
    # 같은 페이지에 딸려 온 다른 글의 요청을 우리 결과로 읽으면 안 된다
    assert is_like_api_url(url, "show2217", "999999999999") is False
    # 읽기(GET)는 공감 등록이 아니다
    assert is_like_api_url(url.replace("&_method=POST", ""),
                           "show2217", "224406140792") is False


# ---- 공감 버튼 셀렉터 (2026-08-31 실측) ----
#
# 글 하나에 같은 공감 버튼이 둘 렌더링된다.
#   [0] 스크롤할 때 따라오는 플로팅 버튼 — 늘 뷰포트 바로 아래(y = 뷰포트 높이 + 6)에
#       있어서 클릭이 "element is outside of the viewport"로 타임아웃된다.
#   [1] 본문 안 버튼 — 조상이 #area_sympathy{logNo} 다. 스크롤해서 누를 수 있다.
# 글 번호로 범위를 좁히면 같은 페이지에 딸려 오는 다른 글의 버튼을 누를 위험도 없다.

def test_selector_is_scoped_to_the_post_number():
    from engine.like import like_button_selector

    selector = like_button_selector("224396233459")

    assert "#area_sympathy224396233459" in selector
    assert "u_likeit_button" in selector


def test_selector_of_two_posts_differ():
    from engine.like import like_button_selector

    assert like_button_selector("111") != like_button_selector("222")


def test_fallback_selector_is_not_scoped_to_a_post_number():
    """스킨에 따라 id가 없을 수 있다. 그때 쓰는 대비책은 글 번호를 담지 않는다."""
    from engine.like import LIKE_BUTTON_FALLBACK

    assert "area_sympathy" not in LIKE_BUTTON_FALLBACK or "{" not in LIKE_BUTTON_FALLBACK
    assert "u_likeit_button" in LIKE_BUTTON_FALLBACK


# ---- 타임아웃 단계 구분 (진단) ----
# `timeout` 한 값이 페이지 로딩 · 버튼 탐색 · 스크롤 · 클릭 · 클릭 후 확인
# 다섯 군데에서 똑같이 나오면, 안전장치가 실행을 멈췄을 때 무엇을
# 고쳐야 하는지 알 방법이 없다 (레거시 결함 9). 단계 이름을 값에 싣는다.


def test_like_result_defaults_to_no_detail():
    from engine.models import LikeResult

    assert LikeResult(LikeOutcome.SUCCESS).detail == ""


def test_stage_detail_names_the_stage_it_came_from():
    from engine.like import STAGE_CLICK, STAGE_CONFIRM, STAGE_GOTO

    assert len({STAGE_GOTO, STAGE_CLICK, STAGE_CONFIRM}) == 3
    for stage in (STAGE_GOTO, STAGE_CLICK, STAGE_CONFIRM):
        assert stage.strip()


def test_detail_never_carries_an_exception_message():
    """예외 메시지에 storage_state가 실린 적이 있다 (보안 규칙). 단계
    이름과 예외 '유형'까지만 남기고 본문은 절대 싣지 않는다."""
    from engine.like import stage_detail

    detail = stage_detail("페이지 로딩", RuntimeError("NID_AUT=SECRETCOOKIE"))
    assert "SECRETCOOKIE" not in detail
    assert "RuntimeError" in detail
    assert "페이지 로딩" in detail


def test_detail_without_an_exception_is_just_the_stage():
    from engine.like import stage_detail

    assert stage_detail("클릭 후 공감 확인") == "클릭 후 공감 확인"
