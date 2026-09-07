from integrations.ascend_routing import is_ascend_command


def test_only_supported_mission_and_habit_requests_route_to_ascend():
    assert is_ascend_command('What missions do I have today?')
    assert is_ascend_command('Mark my gym mission complete.')
    assert is_ascend_command('Log my morning habit.')
    assert not is_ascend_command('start focus')
    assert not is_ascend_command('Why are you roasting my posture?')
