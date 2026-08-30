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
