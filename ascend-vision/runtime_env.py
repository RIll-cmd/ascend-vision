"""Process-wide thread policy, applied before OpenCV, NumPy, torch or ONNX Runtime load.

OpenMP worker threads spin while waiting for work by default. Measured on 2026-10-14,
idle spinning kept about five cores busy while Vision watched the camera; sleeping
workers and a two-thread pool gave the same inference rate with far less CPU and memory.
"""
import os

THREAD_POLICY = {'OMP_WAIT_POLICY': 'PASSIVE', 'KMP_BLOCKTIME': '0', 'OMP_NUM_THREADS': '2'}


def apply_thread_policy(environ=os.environ):
    """Set the policy without overriding values the owner set in the environment."""
    for name, value in THREAD_POLICY.items():
        environ.setdefault(name, value)


apply_thread_policy()
