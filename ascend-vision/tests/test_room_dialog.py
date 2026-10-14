from assistant.room_dialog import RoomAction, RoomDialog

PLAN_GATE = {
    'id': 's1', 'mode': 'build', 'stage': 'gate-plan',
    'attention': {'kind': 'gate', 'gate': 'plan', 'summary': 'The plan has 2 tasks. 1, Scaffold, for Claude. 2, Tests, for Antigravity.',
                  'options': ['Merge both tasks into one', 'Give the tests to Claude'], 'decisions': ['approve', 'revise']},
}
RESULT_GATE = {
    'id': 's1', 'mode': 'build', 'stage': 'gate-result',
    'attention': {'kind': 'gate', 'gate': 'result', 'summary': 'Built the to-do app.', 'options': [], 'decisions': ['accept', 'reject']},
}
PAUSED = {'id': 's1', 'mode': 'build', 'stage': 'build',
          'attention': {'kind': 'pause', 'summary': 'Claude failed twice.', 'options': [], 'decisions': ['retry', 'stop']}}
WORKING = {'id': 's1', 'mode': 'build', 'stage': 'build', 'attention': None}


def test_a_new_gate_is_announced_once_with_what_to_say():
    dialog = RoomDialog()
    assert dialog.observe(WORKING) == []
    lines = dialog.observe(PLAN_GATE)
    assert lines == ['The agents need you to review the plan. The plan has 2 tasks. 1, Scaffold, for Claude. 2, Tests, for Antigravity. Say approve, or change.']
    assert dialog.observe(PLAN_GATE) == []


def test_finished_work_and_pauses_are_announced_in_plain_words():
    dialog = RoomDialog()
    assert dialog.observe(RESULT_GATE) == ['Codex, Claude and Antigravity are done. Built the to-do app. Say accept, or reject.']
    assert RoomDialog().observe(PAUSED) == ['The agent room is paused. Claude failed twice. Say retry, or stop.']
    debate = RoomDialog()
    debate.observe({'id': 'd1', 'mode': 'debate', 'stage': 'verdict', 'attention': None})
    assert debate.observe({'id': 'd1', 'mode': 'debate', 'stage': 'done', 'attention': None}) == ['The debate is finished. The verdict is in the agent room.']


def test_speech_is_ignored_when_nothing_is_waiting():
    dialog = RoomDialog()
    dialog.observe(WORKING)
    assert dialog.handle('approve') is None


def test_approve_asks_for_a_yes_before_sending():
    dialog = RoomDialog()
    dialog.observe(PLAN_GATE)
    assert dialog.handle('approve').text == 'Approve the plan? Say yes to confirm.'
    reply = dialog.handle('yes')
    assert reply.action == RoomAction('gate', 'plan', 'approve')


def test_change_lists_the_options_then_confirms_the_chosen_one():
    dialog = RoomDialog()
    dialog.observe(PLAN_GATE)
    assert dialog.handle('change it').text == 'Option 1: Merge both tasks into one. Option 2: Give the tests to Claude. Say an option number, or say what to change.'
    assert dialog.handle('option two').text == 'Send this change: Give the tests to Claude? Say yes to confirm.'
    assert dialog.handle('yes').action == RoomAction('gate', 'plan', 'revise', 'Give the tests to Claude')


def test_change_can_be_dictated_and_cancelled():
    dialog = RoomDialog()
    dialog.observe(PLAN_GATE)
    assert dialog.handle('change the plan to add a readme task').text == 'Send this change: add a readme task? Say yes to confirm.'
    assert dialog.handle('no').text == 'Okay, nothing was sent. Say approve, or change.'
    assert dialog.handle('change').text.startswith('Option 1:')
    assert dialog.handle('use dark mode for the app').text == 'Send this change: use dark mode for the app? Say yes to confirm.'
    assert dialog.handle('never mind').text == 'Okay, nothing was sent. Say approve, or change.'


def test_option_numbers_out_of_range_and_unclear_confirmations_ask_again():
    dialog = RoomDialog()
    dialog.observe(PLAN_GATE)
    dialog.handle('change')
    assert dialog.handle('option four').text == 'There are only 2 options. Say an option number, or say what to change.'
    dialog.handle('option 1')
    assert dialog.handle('hmm').text == 'Say yes to send it, or no to cancel.'


def test_result_and_pause_decisions_map_to_the_right_actions():
    dialog = RoomDialog()
    dialog.observe(RESULT_GATE)
    assert dialog.handle('reject').text == 'Reject the result? Say yes to confirm.'
    assert dialog.handle('yes').action == RoomAction('gate', 'result', 'reject')
    paused = RoomDialog()
    paused.observe(PAUSED)
    assert paused.handle('try again').text == 'Retry the agent room? Say yes to confirm.'
    assert paused.handle('yes').action == RoomAction('resume', None, 'retry')


def test_a_gate_answered_elsewhere_ends_the_dialog_quietly():
    dialog = RoomDialog()
    dialog.observe(PLAN_GATE)
    dialog.handle('approve')
    assert dialog.observe(WORKING) == []
    assert dialog.handle('yes') is None


def test_a_failed_send_lets_the_owner_try_again():
    dialog = RoomDialog()
    dialog.observe(RESULT_GATE)
    dialog.handle('accept')
    dialog.handle('yes')
    dialog.sent(False)
    assert dialog.handle('accept').text == 'Accept the result? Say yes to confirm.'
    dialog.handle('yes')
    dialog.sent(True)
    assert dialog.handle('accept') is None


def test_long_summaries_are_cut_for_speech():
    long_gate = {**PLAN_GATE, 'attention': {**PLAN_GATE['attention'], 'summary': 'Task. ' * 200}}
    line = RoomDialog().observe(long_gate)[0]
    assert len(line) < 520 and 'and more' in line
