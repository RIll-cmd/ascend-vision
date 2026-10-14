from pathlib import Path

import runtime_env

ROOT = Path(__file__).resolve().parents[1]


def test_thread_policy_defaults_make_pools_sleep_and_stay_small():
    env = {}
    runtime_env.apply_thread_policy(env)
    assert env == {'OMP_WAIT_POLICY': 'PASSIVE', 'KMP_BLOCKTIME': '0', 'OMP_NUM_THREADS': '2'}


def test_thread_policy_keeps_values_the_owner_already_set():
    env = {'OMP_NUM_THREADS': '6'}
    runtime_env.apply_thread_policy(env)
    assert env['OMP_NUM_THREADS'] == '6'
    assert env['OMP_WAIT_POLICY'] == 'PASSIVE'


def test_entry_points_apply_the_policy_before_native_libraries_load():
    # OpenMP reads these variables once, when the first native library that uses it loads.
    main = (ROOT / 'main.py').read_text(encoding='utf-8')
    assert main.index('import runtime_env') < main.index('import cv2')
    host = (ROOT / 'desktop_host.py').read_text(encoding='utf-8')
    assert host.index('import runtime_env') < host.index('from assistant.runtime_health import')
