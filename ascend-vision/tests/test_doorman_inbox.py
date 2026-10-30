from datetime import datetime, timedelta
import json

from assistant.doorman_inbox import Inbox, Reception, hold_pushes

NOW = datetime(2026, 10, 30, 15, 0)


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


def block_until(folder, until):
    (folder / 'focus_block.json').write_text(json.dumps({'until': until.isoformat()}))


def lines(folder):
    return [json.loads(line) for line in (folder / 'doorman_inbox.jsonl').read_text(encoding='utf-8').splitlines()]


def test_in_a_block_routine_messages_wait_and_urgent_ones_pass(tmp_path):
    block_until(tmp_path, NOW + timedelta(minutes=40))
    desk = Reception(tmp_path, clock=Clock())
    assert desk.take('42', 'Alex', 'are we still on for dinner?') == 'In a focus block until 3:40 pm. I\'ll pass it on.'
    assert desk.take('42', 'Alex', 'URGENT: the server is down') == (
        "In a focus block until 3:40 pm, but this sounds urgent, so I'm passing it on now.")
    assert [(m['name'], m['urgent']) for m in lines(tmp_path)] == [('Alex', False), ('Alex', True)]
    assert lines(tmp_path)[0]['text'] == 'are we still on for dinner?'


def test_no_block_or_an_old_block_just_passes_it_on(tmp_path):
    desk = Reception(tmp_path, clock=Clock())
    assert desk.take('42', 'Alex', 'hi') == "I'll pass it on."
    block_until(tmp_path, NOW - timedelta(minutes=1))
    assert desk.take('43', 'Sam', 'hi') == "I'll pass it on."
    (tmp_path / 'focus_block.json').write_text('{broken')
    assert desk.take('44', 'Kim', 'hi') == "I'll pass it on."


def test_urgent_senders_and_words_from_config(tmp_path):
    block_until(tmp_path, NOW + timedelta(minutes=5))
    desk = Reception(tmp_path, clock=Clock(), urgent_words=('deadline',), urgent_senders=('99',))
    assert 'urgent' in desk.take('99', 'Zed', 'hello')
    assert 'urgent' in desk.take('2', 'Alex', 'the deadline moved')
    assert 'urgent' not in desk.take('3', 'Alex', 'nice weather')


def test_text_is_cleaned_and_capped_and_each_sender_is_limited(tmp_path):
    desk = Reception(tmp_path, clock=Clock(), per_hour=2, max_chars=20)
    desk.take('7', 'A' * 80 + '\n', 'line one\nline\ttwo\x07 and a lot more words here')
    saved = lines(tmp_path)[0]
    assert saved['text'] == 'line one line two an' and len(saved['name']) == 40
    desk.take('7', 'A', 'second')
    assert desk.take('7', 'A', 'third') == 'I have your earlier messages and will pass them on.'
    assert len(lines(tmp_path)) == 2
    desk.clock.now += timedelta(hours=1, seconds=1)
    assert desk.take('7', 'A', 'fourth') == "I'll pass it on."
    assert desk.take('8', 'B', '   ') == 'Send a message with some words in it.'


def test_the_inbox_speaks_each_message_once(tmp_path):
    said = []
    inbox = Inbox(tmp_path, announce=lambda text, **kw: said.append((text, kw.get('urgent', False))), clock=Clock())
    inbox.tick()
    assert said == []
    desk = Reception(tmp_path, clock=Clock())
    desk.take('1', 'Alex', 'dinner at 8?')
    desk.take('2', 'Sam', 'emergency, call me')
    with open(tmp_path / 'doorman_inbox.jsonl', 'a', encoding='utf-8') as f:
        f.write('not json\n')
    inbox.tick()
    assert said == [('Message from Alex on Discord: dinner at 8?', False),
                    ('Urgent message from Sam on Discord: emergency, call me', True)]
    inbox.tick()
    assert len(said) == 2
    assert not (tmp_path / 'doorman_inbox.jsonl').exists()


def test_a_reading_file_left_by_a_crash_is_read_first(tmp_path):
    said = []
    (tmp_path / 'doorman_inbox.reading.jsonl').write_text(
        json.dumps({'at': NOW.isoformat(), 'sender': '1', 'name': 'Old', 'text': 'from before', 'urgent': False}) + '\n')
    Reception(tmp_path, clock=Clock()).take('2', 'New', 'now')
    Inbox(tmp_path, announce=lambda text, **kw: said.append(text), clock=Clock()).tick()
    assert said == ['Message from Old on Discord: from before', 'Message from New on Discord: now']


def test_pushes_wait_while_holding():
    sent, holding = [], [True]

    class Publisher:
        def publish(self, intent):
            sent.append(intent)
            return True

    held = hold_pushes(Publisher(), lambda: holding[0])
    assert held.publish('agent needs you') is False and sent == []
    holding[0] = False
    assert held.publish('agent needs you') is True and sent == ['agent needs you']


def test_urgent_senders_are_matched_by_id_only_since_names_can_be_changed(tmp_path):
    block_until(tmp_path, NOW + timedelta(minutes=5))
    desk = Reception(tmp_path, clock=Clock(), urgent_senders=('Mom', '99'))
    assert 'urgent' not in desk.take('5', 'Mom', 'hello')
    assert 'urgent' in desk.take('99', 'Zed', 'hello')


def test_there_is_a_total_cap_and_quiet_senders_are_forgotten(tmp_path):
    desk = Reception(tmp_path, clock=Clock(), per_hour=3, total_per_hour=4)
    for sender in '1234':
        assert desk.take(sender, 'P', 'hi') == "I'll pass it on."
    assert desk.take('5', 'Q', 'hi') == 'Vision is not taking more messages right now. Please try again later.'
    assert len(lines(tmp_path)) == 4
    desk.clock.now += timedelta(hours=2)
    assert desk.take('6', 'R', 'hi') == "I'll pass it on."
    assert set(desk._recent) == {'6'}


def test_during_a_block_routine_messages_stay_on_disk_and_urgent_ones_are_spoken(tmp_path):
    said, holding = [], [True]
    inbox = Inbox(tmp_path, announce=lambda text, **kw: said.append(text), holding=lambda: holding[0], clock=Clock())
    desk = Reception(tmp_path, clock=Clock())
    desk.take('1', 'Alex', 'dinner at 8?')
    desk.take('2', 'Sam', 'urgent: call me')
    inbox.tick()
    assert said == ['Urgent message from Sam on Discord: urgent: call me']
    restarted = Inbox(tmp_path, announce=lambda text, **kw: said.append(text), holding=lambda: holding[0], clock=Clock())
    restarted.tick()
    assert len(said) == 1  # a restart during the block loses nothing and repeats nothing
    holding[0] = False
    restarted.tick()
    assert said[-1] == 'Message from Alex on Discord: dinner at 8?'
    restarted.tick()
    assert len(said) == 2


def test_a_doorman_starting_up_clears_a_block_left_by_a_crash(tmp_path):
    from assistant.announcement_pacer import AnnouncementPacer, FocusDoorman
    state = tmp_path / 'focus_block.json'
    block_until(tmp_path, NOW + timedelta(hours=1))
    FocusDoorman(AnnouncementPacer(lambda text: None), clock=Clock(), state_path=state)
    assert not state.exists()


# Polish: the summary and "what's waiting" count Discord messages; age; negation

def doorman_with_inbox(tmp_path):
    from assistant.announcement_pacer import AnnouncementPacer, FocusDoorman
    said, wall = [], Clock(NOW)
    holder = {}
    inbox = Inbox(tmp_path, announce=lambda text, **kw: holder['pacer'](text, **kw), clock=wall,
                  holding=lambda: holder['doorman'].messages_held(), in_block=lambda: holder['doorman'].holding())
    pacer = AnnouncementPacer(said.append, gap_seconds=0, clock=lambda: 0.0,
                              hold=lambda: holder['doorman'].holding(), also_waiting=inbox.waiting)
    doorman = FocusDoorman(pacer, clock=wall, state_path=tmp_path / 'focus_block.json')
    holder.update(pacer=pacer, doorman=doorman)
    return said, wall, inbox, pacer, doorman


def test_the_end_of_block_summary_counts_held_discord_messages(tmp_path):
    said, wall, inbox, pacer, doorman = doorman_with_inbox(tmp_path)
    doorman.intercept('do not disturb for 30 minutes')
    pacer('It may rain later.')
    desk = Reception(tmp_path, clock=wall)
    desk.take('1', 'Alex', 'dinner at 8?')
    desk.take('2', 'Sam', 'got the keys')
    inbox.tick()
    assert said == []
    assert doorman.intercept("what's waiting") == ("One notice and two Discord messages are waiting for the end of "
                                                   "your focus block. Say 'let them through' to hear them now.")
    wall.now += timedelta(minutes=31)
    pacer.pump()
    inbox.tick()
    pacer.pump()
    pacer.pump()
    assert said[0] == 'While you focused, one notice and two Discord messages waited.'
    assert set(said[1:]) == {'It may rain later.', 'Message from Alex on Discord, 31 minutes ago: dinner at 8?',
                             'Message from Sam on Discord, 31 minutes ago: got the keys'}


def test_let_them_through_includes_discord_messages(tmp_path):
    said, wall, inbox, pacer, doorman = doorman_with_inbox(tmp_path)
    doorman.intercept('do not disturb')
    Reception(tmp_path, clock=wall).take('1', 'Alex', 'dinner at 8?')
    inbox.tick()
    assert doorman.intercept('let them through') == 'Okay.'
    inbox.tick()
    pacer.pump()
    assert said == ['Message from Alex on Discord: dinner at 8?']


def test_old_messages_say_how_old_they_are(tmp_path):
    said = []
    wall = Clock(NOW)
    Reception(tmp_path, clock=wall).take('1', 'Alex', 'call me back')
    wall.now += timedelta(days=1, hours=2)
    Inbox(tmp_path, announce=lambda text, **kw: said.append(text), clock=wall).tick()
    assert said == ['Urgent message from Alex on Discord, yesterday: call me back']


def test_negated_or_punctuated_urgent_words(tmp_path):
    desk = Reception(tmp_path, clock=Clock(), urgent_words=('help!',))
    block_until(tmp_path, NOW + timedelta(minutes=5))
    assert 'urgent' not in desk.take('1', 'A', 'this is not urgent, whenever')
    assert 'urgent' not in desk.take('2', 'A', 'no rush, not right now')
    assert 'urgent' in desk.take('3', 'A', 'help! the build is red')
    assert 'urgent' in desk.take('4', 'A', 'URGENT: server down')


def test_let_them_through_lets_only_what_was_waiting(tmp_path):
    said, wall, inbox, pacer, doorman = doorman_with_inbox(tmp_path)
    inbox.released = doorman.messages_released
    doorman.intercept('do not disturb')
    desk = Reception(tmp_path, clock=wall)
    desk.take('1', 'Alex', 'dinner at 8?')
    inbox.tick()
    doorman.intercept('let them through')
    inbox.tick()
    pacer.pump()
    desk.take('2', 'Sam', 'got the keys')
    inbox.tick()
    pacer.pump()
    assert said == ['Message from Alex on Discord: dinner at 8?']
    assert inbox.waiting() == 1  # Sam's waits for the end of the block


def test_more_negations_are_not_urgent(tmp_path):
    desk = Reception(tmp_path, clock=Clock())
    block_until(tmp_path, NOW + timedelta(minutes=5))
    assert 'urgent' not in desk.take('1', 'A', "it isn't urgent")
    assert 'urgent' not in desk.take('2', 'A', 'not really urgent tbh')
