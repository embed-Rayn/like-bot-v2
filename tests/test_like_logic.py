from engine.like import classify_button_state, is_confirmed_liked, post_url
from engine.models import LikeOutcome


def test_post_url():
    assert post_url("someone", "224395288365") == \
        "https://blog.naver.com/someone/224395288365"


def test_off_button_is_clickable():
    assert classify_button_state("u_likeit_list_btn _button off pcol2") is None


def test_on_button_means_already_liked():
    assert classify_button_state("u_likeit_list_btn _button on pcol2") \
        is LikeOutcome.ALREADY_LIKED


def test_missing_class_attribute_is_an_error():
    assert classify_button_state(None) is LikeOutcome.ERROR


def test_unknown_state_is_an_error():
    assert classify_button_state("u_likeit_list_btn _button") is LikeOutcome.ERROR


def test_on_takes_precedence_when_both_words_appear():
    """'off'가 다른 클래스명 조각에 섞여 들어와도 'on' 상태를 뒤집지 못한다."""
    assert classify_button_state("btn_off_wrap u_likeit_list_btn on") \
        is LikeOutcome.ALREADY_LIKED


# ---- CRITICAL 1: 클릭 확인 술어 (is_confirmed_liked) ----
# AJAX 응답이 아직 돌아오지 않아 클래스가 off로 남아 있는 동안은 '확인
# 안 됨'으로 취급해야 폴링이 계속된다. 확인됐다고 오판하면 실제로는
# 성공했는지 알 수 없는 상태를 SUCCESS로 잘못 보고하게 된다.


def test_on_class_is_confirmed_liked():
    assert is_confirmed_liked("u_likeit_list_btn _button on pcol2") is True


def test_off_class_is_not_yet_confirmed():
    """AJAX 응답이 아직 안 왔다는 뜻 — 계속 기다려야 한다."""
    assert is_confirmed_liked("u_likeit_list_btn _button off pcol2") is False


def test_missing_class_is_not_confirmed():
    assert is_confirmed_liked(None) is False


def test_unknown_class_is_not_confirmed():
    assert is_confirmed_liked("u_likeit_list_btn _button") is False


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

    assert stage_detail("클릭 후 on 확인") == "클릭 후 on 확인"
