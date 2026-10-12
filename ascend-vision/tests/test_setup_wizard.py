import pytest

from setup_wizard import enable_browser

YAML = """feedback:
  enabled: true
companion_context:
  enabled: false # stays off
browser_automation:
  enabled: false # Local browser research is opt-in.
  b3_enabled: false
  provider: gemini
screen_audit:
  enabled: false
"""


def test_only_the_browser_section_is_switched_on():
    result = enable_browser(YAML)
    assert 'browser_automation:\n  enabled: true # Local' in result
    assert 'companion_context:\n  enabled: false # stays off' in result
    assert 'screen_audit:\n  enabled: false' in result
    assert 'b3_enabled: false' in result
    assert enable_browser(result) == result


def test_missing_section_is_an_error_not_a_silent_skip():
    with pytest.raises(ValueError):
        enable_browser('feedback:\n  enabled: true\n')
