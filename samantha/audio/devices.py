"""Observing the system's current default audio devices.

PortAudio snapshots its device list when it initialises in a process, and
re-reading it means tearing PortAudio down - which is illegal while a stream is
open, i.e. for the entire time the assistant is listening. So the listening loop
cannot use PortAudio to notice that headphones were connected.

CoreAudio can answer the question directly and cheaply, without disturbing any
open stream: ask the system object which device is currently the default. A
change in the returned id is the signal to cycle the stream onto it.

Only macOS is implemented. Elsewhere this reports "unknown", and callers keep
their existing behaviour rather than acting on a signal they do not have.
"""

import ctypes
import ctypes.util
import logging
import platform

logger = logging.getLogger("samantha")

PLATFORM = platform.system()

_K_AUDIO_OBJECT_SYSTEM_OBJECT = 1
_K_PROPERTY_DEFAULT_INPUT = 0x64496E20   # 'dIn '
_K_PROPERTY_DEFAULT_OUTPUT = 0x644F7574  # 'dOut'
_K_SCOPE_GLOBAL = 0x676C6F62             # 'glob'
_K_ELEMENT_MAIN = 0


class _AudioObjectPropertyAddress(ctypes.Structure):
    _fields_ = [
        ("mSelector", ctypes.c_uint32),
        ("mScope", ctypes.c_uint32),
        ("mElement", ctypes.c_uint32),
    ]


def _load_core_audio():
    if PLATFORM != "Darwin":
        return None
    try:
        path = ctypes.util.find_library("CoreAudio")
        return ctypes.CDLL(path) if path else None
    except Exception as e:
        logger.debug("CoreAudio unavailable: %s", e)
        return None


_CORE_AUDIO = _load_core_audio()


def _default_device_id(selector: int):
    if _CORE_AUDIO is None:
        return None
    try:
        address = _AudioObjectPropertyAddress(selector, _K_SCOPE_GLOBAL, _K_ELEMENT_MAIN)
        device_id = ctypes.c_uint32(0)
        size = ctypes.c_uint32(ctypes.sizeof(device_id))
        status = _CORE_AUDIO.AudioObjectGetPropertyData(
            ctypes.c_uint32(_K_AUDIO_OBJECT_SYSTEM_OBJECT),
            ctypes.byref(address),
            ctypes.c_uint32(0),
            None,
            ctypes.byref(size),
            ctypes.byref(device_id),
        )
        if status != 0:
            logger.debug("CoreAudio default-device query returned %s", status)
            return None
        return device_id.value
    except Exception as e:
        logger.debug("CoreAudio default-device query failed: %s", e)
        return None


def current_default_devices():
    """Return (input_id, output_id) as the system sees them right now.

    Returns (None, None) where the query is unavailable, which callers must
    treat as "no information" rather than as "no devices".
    """
    return (
        _default_device_id(_K_PROPERTY_DEFAULT_INPUT),
        _default_device_id(_K_PROPERTY_DEFAULT_OUTPUT),
    )


def device_tracking_available() -> bool:
    """Whether default-device changes can actually be observed on this system."""
    return current_default_devices() != (None, None)
