"""State management for Samantha."""

import threading

_samantha_thread = None
_thread_stop_flag = False
_thread_ready = None
_tts_done_event = None
