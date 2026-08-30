from engine.like import classify_button_state, post_url
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
