from datetime import date, datetime, timedelta

import pytest

from assistant.nutrition import (NutritionStore, Profile, bmr, calorie_target, meal_plan, tdee,
                                 water_target_ml)
from assistant.nutrition_coach import (NutritionCoach, make_estimator, parse_height_cm, parse_time,
                                       parse_weight_kg, water_ml_from)

MALE = Profile(sex='male', age=30, height_cm=175, weight_kg=70, activity='light', goal='lose',
               wake='07:00', bed='23:00')


def test_formulas_match_the_standard_equations():
    assert bmr(MALE) == pytest.approx(10 * 70 + 6.25 * 175 - 5 * 30 + 5)
    assert tdee(MALE) == pytest.approx(bmr(MALE) * 1.375)
    assert calorie_target(MALE) == 1770                         # 2,267 - 500, rounded
    tiny = Profile(sex='female', age=60, height_cm=150, weight_kg=45, activity='sedentary', goal='lose',
                   wake='07:00', bed='22:00')
    assert calorie_target(tiny) == 1200                         # never below the safe floor
    assert water_target_ml(MALE) == 3000                         # 35 ml x 70 kg = 2.45 L, floor 3.0 L for men
    assert water_target_ml(MALE, hot_day=True) == 3500
    heavy = Profile(**{**MALE.__dict__, 'weight_kg': 150})
    assert water_target_ml(heavy) == 3750                        # capped at 3.7 L, rounded up to a glass


def test_meal_plan_follows_wake_and_bed_times():
    names = [(name, when.strftime('%H:%M')) for name, when in meal_plan(MALE, date(2026, 10, 13))]
    assert names == [('breakfast', '08:00'), ('snack', '11:00'), ('lunch', '14:00'), ('snack', '17:00'),
                     ('dinner', '20:00')]
    owl = Profile(**{**MALE.__dict__, 'wake': '10:00', 'bed': '02:00'})
    owl_plan = meal_plan(owl, date(2026, 10, 13))
    assert owl_plan[0][1].strftime('%H:%M') == '11:00' and owl_plan[-1][1].strftime('%H:%M') == '23:00'


@pytest.mark.parametrize('text, expected', [('7', '07:00'), ('6:30 am', '06:30'), ('11pm', '23:00'),
                                            ('12am', '00:00'), ('10:15', '10:15')])
def test_time_parsing(text, expected):
    assert parse_time(text) == expected


def test_bedtime_without_am_pm_means_evening_and_units_convert():
    assert parse_time('11', evening=True) == '23:00'
    assert parse_time('1', evening=True) == '01:00'
    assert parse_height_cm("5'7") == pytest.approx(170.2)
    assert parse_height_cm('170 cm') == 170 and parse_height_cm('1.65 m') == pytest.approx(165)
    assert parse_weight_kg('143 lbs') == pytest.approx(64.9) and parse_weight_kg('65') == 65
    assert water_ml_from('I drank a glass of water') == 250
    assert water_ml_from('drank 500 ml') == 500 and water_ml_from('had 2 glasses') == 500
    assert water_ml_from('finished a bottle of water') == 500 and water_ml_from('drank 1.5 liters') == 1500


class Now:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


def make(tmp_path, estimate=None, at=datetime(2026, 10, 13, 9, 0), profile=MALE, hot=False):
    store = NutritionStore(tmp_path / 'nutrition.db')
    if profile is not None:
        store.save_profile(profile)
    said = []
    now = Now(at)
    coach = NutritionCoach(store, announce=said.append, estimate=estimate, hot_day=lambda: hot, now=now)
    return coach, store, said, now


def test_setup_question_and_answer_saves_a_plan(tmp_path):
    coach, store, _said, _now = make(tmp_path, profile=None)
    assert coach.intercept('set up my food plan').endswith('What time do you usually wake up?')
    replies = [coach.intercept(answer) for answer in
               ('7am', '11', 'male', "I'm 30", '175 cm', '70 kg', 'light')]
    assert replies[0] == 'And what time do you usually go to sleep?'
    assert replies[-1].startswith('Last one')
    assert 'Sorry' in coach.intercept('banana')                # not a goal; asks again
    summary = coach.intercept('lose weight')
    assert summary.startswith('Your plan: about 1,770 kcal a day.') and 'not medical advice' in summary
    saved = store.profile()
    assert (saved.wake, saved.bed, saved.weight_kg, saved.goal) == ('07:00', '23:00', 70, 'lose')


def test_setup_can_be_cancelled(tmp_path):
    coach, store, _said, _now = make(tmp_path, profile=None)
    coach.intercept('create a calorie plan')
    assert 'cancelled' in coach.intercept('cancel')
    assert store.profile() is None and coach.intercept('7am') is None


def test_food_is_estimated_logged_corrected_and_removed(tmp_path):
    coach, store, _said, _now = make(tmp_path, estimate=lambda text: [('chicken adobo', 350), ('rice', 200)])
    reply = coach.intercept('I ate chicken adobo and rice')
    assert reply.startswith('Logged chicken adobo, rice: about 550 kcal (estimate). 550 of 1,770 kcal today, 1,220 left.')
    assert coach.intercept('that was 500').startswith('Fixed: 500 kcal. 500 of 1,770')
    assert store.foods(date(2026, 10, 13))[0]['source'] == 'owner'
    assert coach.intercept('delete the last food').startswith('Removed it.')
    assert store.foods(date(2026, 10, 13)) == []


def test_without_an_ai_estimate_vision_asks_for_the_number(tmp_path):
    def broken(_text):
        raise RuntimeError('no keys')

    coach, store, _said, _now = make(tmp_path, estimate=broken)
    assert coach.intercept('I had pancit for lunch').endswith('About how many calories was it?')
    assert coach.intercept('about 400').startswith('Logged pancit')
    assert store.foods(date(2026, 10, 13))[0]['kcal'] == 400


def test_water_logging_and_questions(tmp_path):
    coach, _store, _said, _now = make(tmp_path)
    assert coach.intercept('I drank a glass of water') == 'Logged 250 ml of water. 0.25 of 3 L of water today (1 of 12 glasses).'
    assert coach.intercept('I had water') .startswith('Logged 250 ml')      # "had" is not food here
    assert coach.intercept('how much water today?').startswith('0.5 of 3 L')
    assert 'kcal' in coach.intercept('how many calories left?')
    assert coach.intercept("what's the weather?") is None


def test_set_target_by_number(tmp_path):
    coach, store, _said, _now = make(tmp_path)
    assert coach.intercept('change my calorie target to 2000').startswith('Done: 2,000 kcal a day.')
    assert calorie_target(store.profile()) == 2000


def test_reminders_for_meals_long_gaps_and_water(tmp_path):
    coach, store, said, now = make(tmp_path, at=datetime(2026, 10, 13, 8, 10))
    coach.maybe_remind(False)
    assert said == []                                           # away or asleep
    coach.maybe_remind(True)
    assert said[-1].startswith("It's about breakfast time. Maybe take a break and eat?")
    coach.maybe_remind(True)
    assert len(said) == 1                                       # once per meal
    store.add_food('eggs', 300, 'owner', now.value)
    now.value = datetime(2026, 10, 13, 13, 30)                  # 5h20m later, between meals
    coach.maybe_remind(True)
    assert "over 5 hours since you last ate" in said[-1]
    store.add_food('sandwich', 400, 'owner', now.value)
    now.value = datetime(2026, 10, 13, 15, 30)
    coach.maybe_remind(True)
    assert said[-1].startswith('Water break?') and '0 of 12 glasses' in said[-1]
    now.value = datetime(2026, 10, 13, 23, 50)
    count = len(said)
    coach.maybe_remind(True)
    assert len(said) == count                                   # near bedtime: quiet


def test_setup_is_offered_once_when_there_is_no_plan(tmp_path):
    coach, _store, said, _now = make(tmp_path, profile=None)
    coach.maybe_remind(True)
    coach.maybe_remind(True)
    assert len(said) == 1 and "set up my food plan" in said[0]


def test_estimator_uses_a_configured_provider_and_rejects_bad_answers():
    from types import SimpleNamespace

    class Router:
        def __init__(self, data):
            self._groq_pool = SimpleNamespace(has_keys=True)
            self._gemini_pool = SimpleNamespace(has_keys=False)
            self.data, self.prompts = data, []

        def generate_browser_structured_response(self, provider, prompt, **_kw):
            self.prompts.append((provider, prompt))
            return SimpleNamespace(data=self.data)

    router = Router({'items': [{'name': 'adobo', 'kcal': 350}]})
    assert make_estimator(router)('chicken adobo') == [('adobo', 350)]
    assert router.prompts == [('groq', 'chicken adobo')]
    with pytest.raises(ValueError):
        make_estimator(Router({'items': [{'name': 'x', 'kcal': 99999}]}))('x')


def test_food_tab_endpoints(tmp_path):
    from focus_ui import FocusUI
    coach, store, _said, _now = make(tmp_path)
    ui = FocusUI()
    client = ui.app.test_client()
    assert client.get('/api/fairy/food').status_code == 503
    ui.bind_nutrition(store, coach)
    entry = store.add_food('rice', 200, 'ai', datetime.now())
    body = client.get('/api/fairy/food').json
    assert body['ready'] and body['target'] == 1770 and body['eaten'] == 200 and body['meals'][0]['name'] == 'breakfast'
    assert client.patch(f'/api/fairy/food/{entry}', json={'kcal': 180}).json == {'updated': True}
    assert client.patch(f'/api/fairy/food/{entry}', json={'kcal': 'lots'}).status_code == 400
    assert client.post('/api/fairy/water', json={'ml': 250}).json == {'added': 250}
    assert client.post('/api/fairy/water', json={'ml': 9000}).status_code == 400
    assert client.get('/api/fairy/food').json['waterMl'] == 250
    assert client.delete(f'/api/fairy/food/{entry}').json == {'deleted': True}
