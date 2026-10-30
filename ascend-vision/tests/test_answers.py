from assistant.answers import is_not_now, is_yes, words


def test_words_lowercases_and_drops_punctuation():
    assert words('Hey Vision, YES!  Let\'s do it.') == "hey vision yes let's do it"


def test_yes_starts_the_answer_and_a_negation_turns_it_down():
    assert is_yes('yes')
    assert is_yes('Okay vision, sounds good')
    assert is_yes("let's do it")
    assert not is_yes('yes but not now')
    assert not is_yes('maybe')


def test_not_now_is_the_whole_answer():
    assert is_not_now('not now')
    assert is_not_now('Nope.')
    assert is_not_now('hey vision no thanks')
    assert not is_not_now('no idea what you mean')
