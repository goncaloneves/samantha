"""Samantha Mode - Self-contained voice assistant with wake word detection and TTS.

Fully integrated voice mode that handles:
1. Wake word detection via Whisper STT
2. Voice command recording
3. Command injection into Cursor/Claude
4. Response capture and TTS via Kokoro
5. Conversation logging (STT/TTS) in Cursor

No dependency on converse or other samantha tools.
"""

import asyncio
import io
import logging
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd

try:
    import httpx
except ImportError:
    httpx = None

try:
    import webrtcvad
    VAD_AVAILABLE = True
except ImportError:
    webrtcvad = None
    VAD_AVAILABLE = False

from samantha.server import mcp

logger = logging.getLogger("samantha")

SAMPLE_RATE = 24000
WHISPER_SAMPLE_RATE = 16000
CHANNELS = 1
WHISPER_URL = os.getenv("WHISPER_URL", "http://localhost:2022/v1/audio/transcriptions")
KOKORO_URL = os.getenv("KOKORO_URL", "http://localhost:8880/v1/audio/speech")

SAMANTHA_DIR = Path.home() / ".samantha"
CONFIG_FILE = SAMANTHA_DIR / "config.json"
SAMANTHA_ACTIVE_FILE = SAMANTHA_DIR / "samantha_active"
CONVERSATION_LOG = SAMANTHA_DIR / "conversation.log"


def load_config() -> dict:
    """Load configuration from ~/.samantha/config.json if it exists."""
    if CONFIG_FILE.exists():
        try:
            import json
            return json.loads(CONFIG_FILE.read_text())
        except Exception as e:
            logger.warning("Failed to load config: %s", e)
    return {}


def get_config(key: str, default=None):
    """Get config value from file, falling back to env var, then default."""
    config = load_config()
    if key in config:
        return config[key]
    env_key = f"SAMANTHA_{key.upper()}"
    env_val = os.getenv(env_key)
    if env_val is not None:
        return env_val
    return default


def get_voice() -> str:
    return get_config("voice", "af_sky")


def get_input_device():
    """Get configured input device, or system default."""
    val = get_config("input_device")
    if val is not None and val != "null":
        int_val = int(val)
        return int_val if int_val != -1 else None
    return None


def get_output_device():
    """Get configured output device, or system default."""
    val = get_config("output_device")
    if val is not None and val != "null":
        int_val = int(val)
        return int_val if int_val != -1 else None
    return None


def get_show_status() -> bool:
    val = get_config("show_status", "true")
    if isinstance(val, bool):
        return val
    return str(val).lower() == "true"


def get_theodore_mode() -> bool:
    """Check if Theodore mode is enabled (call user Theodore from the movie Her)."""
    val = get_config("theodore", "true")
    if isinstance(val, bool):
        return val
    return str(val).lower() == "true"


DEFAULT_WAKE_WORDS = [
    "samantha", "hey samantha", "hi samantha", "hello samantha", "ok samantha", "okay samantha",
    "samanta", "samanthia", "samansa", "cemantha", "somantha", "semantha",
    "hey sam", "hi sam", "hello sam", "ok sam", "okay sam",
    "a samantha", "the samantha",
]

STOP_PHRASES = [
    'stop recording', 'end recording', 'finish recording',
    'that is all', "that's all", 'thats all',
    'over and out', 'over out',
    'send message', 'send it',
    'samantha stop', 'samantha send', 'samantha done',
]

DEFAULT_DEACTIVATION_PHRASES = [
    'samantha sleep', 'samantha goodbye', 'goodbye samantha',
    'bye samantha', 'samantha bye',
    "that's all samantha", 'thats all samantha', 'that is all samantha',
    'samantha go to sleep', 'go to sleep samantha',
    'samantha pause', 'pause samantha',
]

INTERRUPT_WORDS = ['stop', 'quiet', 'enough', 'halt']
SKIP_WORDS = ['continue', 'skip']

WHISPER_SOUND_PATTERN = re.compile(r'\[.*?\]|\(.*?\)|♪+', re.IGNORECASE)

SUPPORTED_APPS = ["Cursor", "Claude", "Terminal", "iTerm2", "iTerm", "Warp", "Alacritty", "kitty"]

_samantha_thread = None
_thread_stop_flag = False
_thread_ready = None
_tts_done_event = None
_tts_text_queue = []
_tts_queue_lock = threading.Lock()


def log_conversation(entry_type: str, text: str):
    """Log conversation entry to file."""
    timestamp = datetime.now().strftime("%H:%M:%S")

    if entry_type == "STT":
        log_entry = f"[{timestamp}] 🎤 User: {text}"
    elif entry_type == "TTS":
        log_entry = f"[{timestamp}] 🔊 Samantha: {text}"
    elif entry_type == "INTERRUPT":
        log_entry = f"[{timestamp}] 🛑 Interrupt: {text}"
    else:
        log_entry = f"[{timestamp}] {entry_type}: {text}"

    try:
        CONVERSATION_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(CONVERSATION_LOG, "a") as f:
            f.write(log_entry + "\n")
    except Exception as e:
        logger.debug("Failed to write conversation log: %s", e)

    logger.info(log_entry)


def normalize_text(text: str) -> str:
    """Normalize text by removing punctuation and converting to lowercase."""
    if not text:
        return ""
    import string
    return text.lower().translate(str.maketrans('', '', string.punctuation)).strip()


def get_wake_words() -> list:
    config_words = get_config("wake_words")
    if config_words:
        if isinstance(config_words, list):
            return [w.lower() for w in config_words]
        return [w.strip().lower() for w in config_words.split(",")]
    return DEFAULT_WAKE_WORDS


def check_for_wake_word(text: str) -> Optional[str]:
    if not text:
        return None
    text_clean = normalize_text(text)
    for wake_word in get_wake_words():
        if wake_word in text_clean:
            return wake_word
    return None


def check_for_stop_phrase(text: str) -> bool:
    if not text:
        return False
    text_clean = normalize_text(text)
    return any(phrase in text_clean for phrase in STOP_PHRASES)


def get_deactivation_phrases() -> list:
    config_phrases = get_config("deactivation_words")
    if config_phrases:
        if isinstance(config_phrases, list):
            return [p.lower() for p in config_phrases]
        return [p.strip().lower() for p in config_phrases.split(",")]
    return DEFAULT_DEACTIVATION_PHRASES


def check_for_deactivation(text: str) -> bool:
    """Check if text contains a deactivation phrase to stop active listening."""
    if not text:
        return False
    text_clean = normalize_text(text)
    return any(phrase in text_clean for phrase in get_deactivation_phrases())


def normalize_audio(audio_data: np.ndarray, target_peak: int = 20000) -> np.ndarray:
    """Normalize audio to target peak level for better recognition with low-sensitivity mics."""
    peak = max(abs(audio_data.min()), abs(audio_data.max()))
    if peak < 100:
        return audio_data
    gain = min(target_peak / peak, 20.0)
    if gain > 1.5:
        normalized = np.clip(audio_data.astype(np.float32) * gain, -32768, 32767).astype(np.int16)
        return normalized
    return audio_data


def _prepare_audio_for_whisper(audio_data: np.ndarray) -> io.BytesIO:
    """Convert audio data to WAV buffer for Whisper STT."""
    from pydub import AudioSegment
    audio = AudioSegment(
        audio_data.tobytes(),
        frame_rate=SAMPLE_RATE,
        sample_width=2,
        channels=CHANNELS
    )
    if SAMPLE_RATE != WHISPER_SAMPLE_RATE:
        audio = audio.set_frame_rate(WHISPER_SAMPLE_RATE)
    wav_buffer = io.BytesIO()
    audio.export(wav_buffer, format="wav")
    wav_buffer.seek(0)
    return wav_buffer


def _clear_queue(q) -> None:
    """Clear all items from a queue."""
    import queue
    while not q.empty():
        try:
            q.get_nowait()
        except queue.Empty:
            break


async def transcribe_audio(audio_data: np.ndarray) -> Optional[str]:
    """Transcribe audio using Whisper STT."""
    try:
        wav_buffer = _prepare_audio_for_whisper(audio_data)

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                WHISPER_URL,
                files={"file": ("audio.wav", wav_buffer, "audio/wav")},
                data={"response_format": "json"}
            )
            if response.status_code == 200:
                result = response.json()
                return result.get("text", "").strip()
    except Exception as e:
        logger.debug("STT error: %s", e)
    return None


def speak_tts_sync(text: str) -> bool:
    """Speak text using Kokoro TTS via sounddevice."""
    global _last_tts_text, _last_tts_time, _tts_interrupt
    logger.info("🔊 TTS: %s", text[:80] + "..." if len(text) > 80 else text)

    _last_tts_text = text
    _last_tts_time = time.time()
    _tts_interrupt = False

    try:
        import requests

        response = requests.post(
            KOKORO_URL,
            json={
                "model": "kokoro",
                "input": text,
                "voice": get_voice(),
                "response_format": "wav",
            },
            timeout=60.0,
        )

        if response.status_code != 200:
            logger.error("TTS error: HTTP %s", response.status_code)
            return False

        from pydub import AudioSegment

        audio = AudioSegment.from_wav(io.BytesIO(response.content))
        samples = np.array(audio.get_array_of_samples(), dtype=np.float32)
        samples = samples / 32768.0

        if audio.channels == 2:
            samples = samples.reshape((-1, 2))
        else:
            samples = samples.reshape((-1, 1))

        sd.play(samples, samplerate=audio.frame_rate, device=get_output_device())
        while sd.get_stream().active:
            if _tts_interrupt:
                sd.stop()
                logger.info("🛑 TTS interrupted")
                break
            time.sleep(0.05)

        if not _tts_interrupt:
            log_conversation("TTS", text)
        _tts_interrupt = False
        return True
    except Exception as e:
        logger.error("TTS error: %s", e)
        return False


async def speak_tts(text: str) -> bool:
    """Speak text using Kokoro TTS."""
    return speak_tts_sync(text)


def play_chime():
    """Play activation chime sound (cross-platform)."""
    if platform.system() == "Darwin":
        subprocess.Popen(
            ["afplay", "/System/Library/Sounds/Glass.aiff"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    elif platform.system() == "Linux":
        for player in ["paplay", "pw-play", "aplay"]:
            if shutil.which(player):
                subprocess.Popen(
                    [player, "/usr/share/sounds/freedesktop/stereo/service-login.oga"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                break
    elif platform.system() == "Windows":
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass


def play_goodbye_chime():
    """Play deactivation chime sound (cross-platform)."""
    if platform.system() == "Darwin":
        subprocess.Popen(
            ["afplay", "/System/Library/Sounds/Submarine.aiff"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    elif platform.system() == "Linux":
        for player in ["paplay", "pw-play", "aplay"]:
            if shutil.which(player):
                subprocess.Popen(
                    [player, "/usr/share/sounds/freedesktop/stereo/service-logout.oga"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                break
    elif platform.system() == "Windows":
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONHAND)
        except Exception:
            pass


PLATFORM = platform.system()


def get_frontmost_app() -> str:
    """Get the frontmost application name (macOS only)."""
    if PLATFORM != "Darwin":
        return ""
    try:
        result = subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to get name of first process whose frontmost is true'],
            capture_output=True, text=True, check=True
        )
        return result.stdout.strip()
    except Exception:
        return ""


def copy_to_clipboard(text: str) -> bool:
    """Copy text to clipboard (cross-platform)."""
    try:
        if PLATFORM == "Darwin":
            subprocess.run(["pbcopy"], input=text.encode(), check=True)
            return True
        elif PLATFORM == "Linux":
            if shutil.which("xclip"):
                subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode(), check=True)
                return True
            elif shutil.which("xsel"):
                subprocess.run(["xsel", "--clipboard", "--input"], input=text.encode(), check=True)
                return True
            elif shutil.which("wl-copy"):
                subprocess.run(["wl-copy"], input=text.encode(), check=True)
                return True
            else:
                logger.error("No clipboard tool found (xclip, xsel, or wl-copy)")
                return False
        elif PLATFORM == "Windows":
            subprocess.run(["clip.exe"], input=text.encode(), check=True, shell=True)
            return True
        else:
            logger.error("Unsupported platform: %s", PLATFORM)
            return False
    except Exception as e:
        logger.error("Clipboard copy failed: %s", e)
        return False


def simulate_paste_and_enter() -> bool:
    """Simulate Cmd/Ctrl+V paste and Enter keystroke (cross-platform)."""
    try:
        if PLATFORM == "Darwin":
            applescript = '''
            tell application "System Events"
                keystroke "v" using command down
                delay 0.2
                key code 36
            end tell
            '''
            subprocess.run(["osascript", "-e", applescript], check=True, capture_output=True)
            return True
        elif PLATFORM == "Linux":
            if shutil.which("xdotool"):
                subprocess.run(["xdotool", "key", "ctrl+v"], check=True)
                time.sleep(0.2)
                subprocess.run(["xdotool", "key", "Return"], check=True)
                return True
            elif shutil.which("ydotool"):
                subprocess.run(["ydotool", "key", "29:1", "47:1", "47:0", "29:0"], check=True)
                time.sleep(0.2)
                subprocess.run(["ydotool", "key", "28:1", "28:0"], check=True)
                return True
            else:
                logger.error("No keystroke tool found (xdotool or ydotool)")
                return False
        elif PLATFORM == "Windows":
            try:
                import pyautogui
                pyautogui.hotkey('ctrl', 'v')
                time.sleep(0.2)
                pyautogui.press('enter')
                return True
            except ImportError:
                powershell_script = '''
                Add-Type -AssemblyName System.Windows.Forms
                [System.Windows.Forms.SendKeys]::SendWait("^v")
                Start-Sleep -Milliseconds 200
                [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
                '''
                subprocess.run(["powershell", "-Command", powershell_script], check=True)
                return True
        else:
            logger.error("Unsupported platform: %s", PLATFORM)
            return False
    except Exception as e:
        logger.error("Paste simulation failed: %s", e)
        return False


def activate_app(app_name: str) -> bool:
    """Activate/focus an application (cross-platform)."""
    try:
        if PLATFORM == "Darwin":
            applescript = f'''
            tell application "{app_name}"
                activate
            end tell
            '''
            subprocess.run(["osascript", "-e", applescript], check=True, capture_output=True)
            return True
        elif PLATFORM == "Linux":
            if shutil.which("xdotool"):
                subprocess.run(["xdotool", "search", "--name", app_name, "windowactivate"], check=True)
                return True
            elif shutil.which("wmctrl"):
                subprocess.run(["wmctrl", "-a", app_name], check=True)
                return True
            else:
                logger.warning("No window activation tool found (xdotool or wmctrl)")
                return False
        elif PLATFORM == "Windows":
            try:
                import pyautogui
                import pygetwindow as gw
                windows = gw.getWindowsWithTitle(app_name)
                if windows:
                    windows[0].activate()
                    return True
                return False
            except ImportError:
                logger.warning("pygetwindow not installed for Windows window activation")
                return False
        else:
            return False
    except Exception as e:
        logger.warning("App activation failed: %s", e)
        return False


def kill_orphaned_processes():
    """Kill any orphaned samantha processes from previous sessions."""
    try:
        our_pid = os.getpid()
        result = subprocess.run(["pgrep", "-f", "samantha"], capture_output=True, text=True, timeout=5)
        if result.stdout.strip():
            for pid in result.stdout.strip().split('\n'):
                pid = int(pid.strip())
                if pid != our_pid:
                    try:
                        os.kill(pid, 9)
                        logger.info("Killed orphaned samantha process: %d", pid)
                    except (ProcessLookupError, PermissionError):
                        pass
    except Exception as e:
        logger.debug("Cleanup check failed: %s", e)


def inject_into_app(text: str, log_type: str = None):
    """Inject text into Cursor/terminal using clipboard paste (cross-platform)."""
    target = os.getenv("SAMANTHA_TARGET_APP") or get_frontmost_app()
    if target not in SUPPORTED_APPS:
        target = "Cursor"

    logger.info("💉 Injecting into %s: %s", target, text[:50])

    if not copy_to_clipboard(text):
        logger.error("Failed to copy to clipboard")
        return

    if PLATFORM == "Darwin":
        activate_app(target)
        time.sleep(0.5)

    if simulate_paste_and_enter():
        logger.info("✅ Injected via clipboard (%s)", PLATFORM)
    else:
        logger.error("Injection failed on %s", PLATFORM)


def clean_command(text: str) -> str:
    """Clean recorded command text. Removes content BEFORE wake word, Whisper metadata, and anything AFTER stop phrases."""
    cleaned = WHISPER_SOUND_PATTERN.sub('', text).strip()

    for wake_word in sorted(get_wake_words(), key=len, reverse=True):
        pattern = r'[^\w]*'.join(re.escape(w) for w in wake_word.split())
        match = re.search(pattern, cleaned, re.IGNORECASE)
        if match:
            cleaned = cleaned[match.start():].strip()
            break

    stop_phrases_pattern = r'(stop\s+recording|end\s+recording|finish\s+recording|that\s+is\s+all|that\'s\s+all|thats\s+all|over\s+and\s+out|over\s+out|send\s+message|send\s+it|samantha\s+stop|samantha\s+send|samantha\s+done)'
    match = re.search(stop_phrases_pattern, cleaned, flags=re.IGNORECASE)
    if match:
        cleaned = cleaned[:match.end()]

    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


async def wait_for_response_and_speak(timeout: float = 30.0) -> bool:
    """Wait for Claude's response and speak it via TTS."""
    logger.info("⏳ Waiting for response...")
    start_time = time.time()
    last_clipboard = ""

    try:
        result = subprocess.run(["pbpaste"], capture_output=True, text=True)
        last_clipboard = result.stdout.strip()
    except Exception:
        pass

    while time.time() - start_time < timeout:
        with _tts_queue_lock:
            if _tts_text_queue:
                tts_text = _tts_text_queue.pop(0)
                await speak_tts(tts_text)
                return True

        try:
            result = subprocess.run(["pbpaste"], capture_output=True, text=True)
            current = result.stdout.strip()

            if current != last_clipboard and len(current) > 10:
                if not current.startswith("🎤") and not current.startswith("```") and not current.startswith("[🎤"):
                    logger.info("📋 Clipboard response detected")
                    await speak_tts(current[:500])
                    return True

            last_clipboard = current
        except Exception:
            pass

        await asyncio.sleep(0.5)

    logger.info("⏰ Response timeout")
    return False


def contains_trigger_word(text: str) -> bool:
    """Check if text contains a trigger word (Samantha)."""
    if not text:
        return False
    import re
    text_lower = text.lower()
    triggers = [
        r'\bsamantha\b', r'\bsamanta\b', r'\bcemantha\b', r'\bsomantha\b', r'\bsemantha\b',
        r'\bsemante\b', r'\bsamante\b', r'\bsam\b'
    ]
    for pattern in triggers:
        if re.search(pattern, text_lower):
            return True
    return False


def get_active_interrupt_words() -> list:
    """Get interrupt words that are NOT in the current TTS text.

    If TTS says "stop", only "quiet" works.
    If TTS says "quiet", only "stop" works.
    If neither, both work.
    """
    global _last_tts_text
    tts_lower = _last_tts_text.lower() if _last_tts_text else ""

    return [word for word in INTERRUPT_WORDS if word not in tts_lower]


def is_skip_allowed() -> bool:
    """Check if skip words are allowed based on current TTS text.

    If TTS contains a skip word, don't allow skip detection to avoid self-triggering.
    """
    global _last_tts_text
    if not _last_tts_text:
        return True
    tts_lower = _last_tts_text.lower()
    return not any(word in tts_lower for word in SKIP_WORDS)


def contains_interrupt_phrase(text: str) -> bool:
    """Check if text contains an active interrupt word. Expects pre-sanitized text."""
    if not text:
        return False

    active_words = get_active_interrupt_words()
    for word in active_words:
        if word in text.split():
            return True
    return False


def contains_skip_phrase(text: str) -> bool:
    """Check if text contains a skip word. Expects pre-sanitized text."""
    if not text:
        return False
    if not is_skip_allowed():
        return False

    for word in SKIP_WORDS:
        if word in text.split():
            return True
    return False


_last_tts_text = ""
_last_tts_time = 0


def sanitize_whisper_text(text: str) -> str:
    """Clean Whisper transcription by removing metadata and hallucinations.

    Removes:
    - Bracketed sounds: [Music], [Applause], [BLANK_AUDIO], etc.
    - Parenthesized sounds: (coughing), (laughing), etc.
    - Musical notes: ♪♪♪
    - Extra whitespace and special punctuation

    Returns the cleaned dialogue text.
    """
    if not text:
        return ""

    cleaned = WHISPER_SOUND_PATTERN.sub('', text)
    cleaned = re.sub(r"[^\w\s']", '', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip().lower()

    return cleaned


def is_noise(text: str) -> bool:
    """Check if transcription is just background noise, not speech."""
    if not text:
        return True

    sanitized = sanitize_whisper_text(text)

    if not sanitized or len(sanitized) < 3:
        return True

    all_keywords = DEFAULT_WAKE_WORDS + STOP_PHRASES + DEFAULT_DEACTIVATION_PHRASES + INTERRUPT_WORDS + SKIP_WORDS
    for keyword in all_keywords:
        if keyword in sanitized:
            return False

    noise_words = [
        'click', 'clap', 'ding', 'bell', 'tick', 'thud', 'bang',
        'engine', 'revving', 'keyboard', 'typing', 'noise',
        'blank audio', 'silence', 'static', 'hum', 'buzz',
        'music', 'music playing', 'clock ticking'
    ]
    if sanitized in noise_words:
        return True

    return False


def is_echo(text: str) -> bool:
    """Check if transcription is likely echo from TTS playback."""
    global _last_tts_text, _last_tts_time
    if not _last_tts_text or not text:
        return False
    if time.time() - _last_tts_time > 10:
        return False

    text_lower = text.lower().strip()
    tts_lower = _last_tts_text.lower().strip()

    if text_lower in tts_lower or tts_lower in text_lower:
        return True

    text_words = set(text_lower.split())
    tts_words = set(tts_lower.split())
    if len(text_words) > 3:
        overlap = len(text_words & tts_words) / len(text_words)
        if overlap > 0.5:
            return True

    return False


_tts_playing = False
_tts_start_time = 0
_tts_interrupt = False


MIN_AUDIO_ENERGY = 1500


def transcribe_audio_sync(audio_data: np.ndarray) -> Optional[str]:
    """Synchronous transcribe for use in thread."""
    try:
        import requests

        max_energy = np.max(np.abs(audio_data))
        if max_energy < MIN_AUDIO_ENERGY:
            logger.debug("Audio energy too low (%d < %d), skipping Whisper", max_energy, MIN_AUDIO_ENERGY)
            return None

        audio_data = normalize_audio(audio_data)
        wav_buffer = _prepare_audio_for_whisper(audio_data)

        response = requests.post(
            WHISPER_URL,
            files={"file": ("audio.wav", wav_buffer, "audio/wav")},
            data={"response_format": "json"},
            timeout=10.0
        )
        if response.status_code == 200:
            result = response.json()
            return result.get("text", "").strip()
    except Exception as e:
        logger.debug("STT error: %s", e)
    return None


def samantha_loop_thread():
    """Main Samantha voice assistant loop running in a dedicated thread."""
    global _thread_stop_flag, _tts_playing, _last_tts_time, _tts_start_time, _tts_interrupt, _thread_ready, _tts_done_event, _tts_text_queue

    input_dev = get_input_device()
    try:
        device_info = sd.query_devices(input_dev)
        device_name = device_info['name']
    except Exception:
        device_name = f"device {input_dev}"

    logger.info("🎧 Samantha thread started (VAD: %s, mic: %s)", "enabled" if VAD_AVAILABLE else "disabled", device_name)
    logger.info("   Say 'Hey Samantha' to activate")

    import queue
    from scipy import signal as scipy_signal

    VAD_CHUNK_DURATION_MS = 30
    SILENCE_THRESHOLD_MS = 1000
    MIN_RECORDING_DURATION = 0.3
    INITIAL_SILENCE_GRACE_PERIOD = 1.0
    VAD_SAMPLE_RATE = 16000
    VAD_AGGRESSIVENESS_LISTENING = 1
    VAD_AGGRESSIVENESS_TTS = 1
    MAX_INACTIVE_AUDIO_MS = 15000

    chunk_samples = int(SAMPLE_RATE * VAD_CHUNK_DURATION_MS / 1000)
    vad_chunk_samples = int(VAD_SAMPLE_RATE * VAD_CHUNK_DURATION_MS / 1000)
    silence_timeout = 1800.0

    is_active = False
    last_speech_time = 0
    audio_chunks = []
    silence_duration_ms = 0
    speech_detected = False
    recording_start = 0

    audio_queue = queue.Queue()

    def audio_callback(indata, frames, callback_time, status):
        if status:
            logger.warning("Audio stream status: %s", status)
        audio_queue.put(indata.copy())

    try:
        vad = webrtcvad.Vad(VAD_AGGRESSIVENESS_LISTENING) if VAD_AVAILABLE else None
        vad_tts = webrtcvad.Vad(VAD_AGGRESSIVENESS_TTS) if VAD_AVAILABLE else None

        with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=np.int16,
                           callback=audio_callback, blocksize=chunk_samples, device=get_input_device()):
            logger.debug("Started continuous audio stream")

            if _thread_ready:
                _thread_ready.set()

            while not _thread_stop_flag:
                try:
                    tts_text = None
                    if not _tts_playing:
                        with _tts_queue_lock:
                            if _tts_text_queue:
                                tts_text = _tts_text_queue.pop(0)

                    if tts_text:
                        _tts_playing = True
                        _tts_start_time = time.time()
                        _tts_done_event = threading.Event()

                        _clear_queue(audio_queue)
                        speech_detected = False
                        audio_chunks = []
                        silence_duration_ms = 0
                        recording_start = 0

                        done_event = _tts_done_event
                        tts_text_to_speak = tts_text

                        def tts_thread_func():
                            global _tts_playing, _last_tts_time, _tts_start_time, _last_tts_text
                            _last_tts_text = tts_text_to_speak
                            active_words = get_active_interrupt_words()
                            logger.info("🎯 Active interrupt words: %s", active_words)
                            try:
                                speak_tts_sync(tts_text_to_speak)
                            finally:
                                _last_tts_time = time.time()
                                _tts_start_time = 0
                                _tts_playing = False
                                done_event.set()

                        tts_thread = threading.Thread(target=tts_thread_func, daemon=True)
                        tts_thread.start()

                    if _tts_playing:
                        try:
                            chunk = audio_queue.get(timeout=0.05)
                            chunk_flat = chunk.flatten()

                            is_speech_chunk = False
                            if vad_tts:
                                resampled_length = int(len(chunk_flat) * VAD_SAMPLE_RATE / SAMPLE_RATE)
                                vad_chunk = scipy_signal.resample(chunk_flat, resampled_length)
                                vad_chunk = vad_chunk[:vad_chunk_samples].astype(np.int16)
                                try:
                                    is_speech_chunk = vad_tts.is_speech(vad_chunk.tobytes(), VAD_SAMPLE_RATE)
                                except Exception:
                                    is_speech_chunk = False

                            if is_speech_chunk:
                                audio_chunks.append(chunk_flat)

                            accumulated_duration_ms = len(audio_chunks) * VAD_CHUNK_DURATION_MS
                            tts_elapsed = time.time() - _tts_start_time if _tts_start_time > 0 else 0
                            if accumulated_duration_ms >= 300 and tts_elapsed >= 2.0:
                                full_audio = np.concatenate(audio_chunks)
                                raw_text = transcribe_audio_sync(full_audio)
                                text = sanitize_whisper_text(raw_text) if raw_text else ""

                                is_interrupt = text and contains_interrupt_phrase(text)
                                is_skip = text and contains_skip_phrase(text)

                                if is_interrupt or is_skip:
                                    is_skip_only = is_skip and not is_interrupt
                                    logger.info("🔍 TTS interrupt check: %s", text[:50])
                                    if is_skip_only:
                                        logger.info("⏭️ Skip to next detected: %s", text[:50])
                                        log_conversation("SKIP", text)
                                    else:
                                        logger.info("🛑 Interrupt detected: %s", text[:50])
                                        log_conversation("INTERRUPT", text)
                                        with _tts_queue_lock:
                                            _tts_text_queue.clear()
                                    _tts_interrupt = True
                                    _tts_playing = False
                                    time.sleep(0.1)
                                    _clear_queue(audio_queue)
                                    if get_show_status():
                                        if is_skip_only:
                                            inject_into_app("<!-- ⏭️ [Skipped to next] -->")
                                        else:
                                            inject_into_app("<!-- 🤫 [Speech interrupted] -->")
                                    if is_active:
                                        last_speech_time = time.time()

                                audio_chunks = []

                        except queue.Empty:
                            pass
                        continue

                    if _last_tts_time > 0:
                        logger.debug("🧹 Post-TTS cleanup: clearing audio queue and buffers")
                        _clear_queue(audio_queue)
                        speech_detected = False
                        audio_chunks = []
                        silence_duration_ms = 0
                        recording_start = 0
                        _last_tts_time = 0
                        if is_active:
                            last_speech_time = time.time()
                        continue

                    if is_active:
                        silence_elapsed = time.time() - last_speech_time
                        if silence_elapsed > silence_timeout:
                            logger.info("⏰ 30min silence - returning to idle")
                            is_active = False
                            play_chime()

                    try:
                        chunk = audio_queue.get(timeout=0.1)
                    except queue.Empty:
                        continue

                    chunk_flat = chunk.flatten()
                    audio_chunks.append(chunk_flat)

                    if not is_active:
                        max_chunks = MAX_INACTIVE_AUDIO_MS // VAD_CHUNK_DURATION_MS
                        if len(audio_chunks) > max_chunks:
                            audio_chunks = audio_chunks[-max_chunks:]

                    is_speech = False
                    if vad:
                        resampled_length = int(len(chunk_flat) * VAD_SAMPLE_RATE / SAMPLE_RATE)
                        vad_chunk = scipy_signal.resample(chunk_flat, resampled_length)
                        vad_chunk = vad_chunk[:vad_chunk_samples].astype(np.int16)
                        try:
                            is_speech = vad.is_speech(vad_chunk.tobytes(), VAD_SAMPLE_RATE)
                        except Exception:
                            is_speech = True
                    else:
                        is_speech = True

                    if not speech_detected:
                        if is_speech:
                            logger.info("🎤 Speech detected, starting active recording")
                            speech_detected = True
                            recording_start = time.time()
                            silence_duration_ms = 0
                            audio_chunks = [chunk_flat]
                    else:
                        if is_speech:
                            silence_duration_ms = 0
                        else:
                            silence_duration_ms += VAD_CHUNK_DURATION_MS

                            recording_duration = time.time() - recording_start
                            past_grace_period = recording_duration >= INITIAL_SILENCE_GRACE_PERIOD
                            if recording_duration >= MIN_RECORDING_DURATION and silence_duration_ms >= SILENCE_THRESHOLD_MS and past_grace_period:
                                logger.info("✓ Silence threshold reached after %.1fs", recording_duration)

                                if not _tts_playing and audio_chunks:
                                    full_audio = np.concatenate(audio_chunks)
                                    text = transcribe_audio_sync(full_audio)

                                    if text:
                                        if is_echo(text):
                                            logger.debug("Filtered as echo: %s", text[:50])
                                        elif is_noise(text):
                                            logger.debug("Filtered as noise: %s", text[:50])

                                    if text and text not in ["[BLANK_AUDIO]", ""] and not is_echo(text) and not is_noise(text):
                                        logger.info("📝 Heard (active=%s): %s", is_active, text[:100])

                                        if is_active:
                                            if check_for_deactivation(text):
                                                logger.info("😴 Deactivating - returning to idle")
                                                is_active = False
                                                play_goodbye_chime()
                                                if get_show_status():
                                                    inject_into_app("<!-- 😴 [Samantha deactivated] -->")
                                            else:
                                                logger.info("🟢 Active - sending to Cursor")
                                                last_speech_time = time.time()
                                                cleaned = clean_command(text)
                                                if cleaned:
                                                    log_conversation("STT", cleaned)
                                                    inject_into_app(f"🎤 {cleaned}")
                                        elif contains_trigger_word(text):
                                            logger.info("✨ Activated!")
                                            is_active = True
                                            last_speech_time = time.time()
                                            play_chime()
                                            if get_show_status():
                                                inject_into_app("<!-- 👋 [Samantha activated] -->")
                                            cleaned = clean_command(text)
                                            if cleaned:
                                                log_conversation("STT", cleaned)
                                                inject_into_app(f"🎤 {cleaned}")
                                        else:
                                            logger.debug("No trigger - discarding")

                                speech_detected = False
                                audio_chunks = []
                                silence_duration_ms = 0

                except Exception as e:
                    logger.error("Loop error: %s", e)
                    time.sleep(0.1)

    except Exception as e:
        logger.error("Stream error: %s", e)

    logger.info("🛑 Samantha thread stopped")


async def _check_service_health(health_url: str) -> bool:
    """Check if a service is healthy."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(health_url)
            return response.status_code == 200
    except Exception:
        return False


async def _wait_for_service(health_url: str, service_name: str, max_attempts: int, log_interval: int) -> bool:
    """Wait for a service to become healthy."""
    for i in range(max_attempts):
        await asyncio.sleep(1)
        if await _check_service_health(health_url):
            logger.info("%s started successfully", service_name)
            return True
        if i % log_interval == log_interval - 1:
            logger.info("Waiting for %s... (%ds)", service_name, i + 1)
    return False


async def ensure_kokoro_running() -> bool:
    """Check if Kokoro TTS is running, attempt to start if not."""
    health_url = "http://localhost:8880/health"

    if await _check_service_health(health_url):
        logger.info("Kokoro TTS is running")
        return True

    logger.info("Kokoro TTS not running, attempting to start...")

    kokoro_dir = SAMANTHA_DIR / "services" / "kokoro"
    system = platform.system()
    started = False

    if system == "Darwin":
        start_script = kokoro_dir / "start-gpu_mac.sh"
    elif system == "Linux":
        if shutil.which("nvidia-smi"):
            start_script = kokoro_dir / "start-gpu.sh"
        else:
            start_script = kokoro_dir / "start-cpu.sh"
    else:
        start_script = kokoro_dir / "start-cpu.sh"

    if start_script.exists():
        try:
            subprocess.Popen(
                ["bash", str(start_script)],
                cwd=str(kokoro_dir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            logger.info("Started Kokoro via %s", start_script.name)
            started = True
        except Exception as e:
            logger.error("Failed to start Kokoro script: %s", e)

    if not started:
        logger.error("Kokoro start script not found at %s", start_script)
        logger.error("Run 'samantha-install install' to install Kokoro")

    if await _wait_for_service(health_url, "Kokoro TTS", 45, 10):
        return True

    logger.error("Failed to start Kokoro TTS - please start manually:")
    logger.error("  cd ~/.samantha/services/kokoro && ./%s", start_script.name if start_script else "start-gpu_mac.sh")
    return False


async def ensure_whisper_running() -> bool:
    """Check if Whisper STT is running, attempt to start if not."""
    health_url = "http://localhost:2022/health"

    if await _check_service_health(health_url):
        logger.info("Whisper STT is running")
        return True

    logger.info("Whisper STT not running, attempting to start...")

    if platform.system() == "Darwin":
        started = False
        start_script = SAMANTHA_DIR / "services" / "whisper" / "bin" / "start-whisper-server.sh"

        if start_script.exists():
            try:
                subprocess.Popen(
                    ["bash", str(start_script)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                logger.info("Started Whisper via start script")
                started = True
            except Exception as e:
                logger.error("Failed to start Whisper script: %s", e)

        if not started:
            logger.error("Whisper start script not found at %s", start_script)
            logger.error("Run 'samantha-install install' to install Whisper")

    if await _wait_for_service(health_url, "Whisper STT", 20, 5):
        return True

    logger.error("Failed to start Whisper STT - please start manually:")
    logger.error("  ~/.samantha/services/whisper/bin/start-whisper-server.sh")
    return False


@mcp.tool()
async def samantha_start() -> str:
    """Start Samantha voice mode.

    Integrated voice assistant that:
    1. Listens for "Hey Samantha"
    2. Records your voice command
    3. Sends it to Claude with 🎤 marker
    4. Speaks Claude's response via TTS
    5. Logs conversation (STT/TTS) for history

    Usage: Say "Hey Samantha, [your question]" then "that's all"

    Returns:
        Status message
    """
    global _samantha_thread, _thread_stop_flag, _thread_ready

    if _samantha_thread and _samantha_thread.is_alive():
        return "Samantha is already running"

    kill_orphaned_processes()

    SAMANTHA_DIR.mkdir(parents=True, exist_ok=True)
    SAMANTHA_ACTIVE_FILE.touch()

    kokoro_ok = await ensure_kokoro_running()
    whisper_ok = await ensure_whisper_running()

    if not kokoro_ok:
        return "❌ Failed to start Kokoro TTS service"
    if not whisper_ok:
        return "❌ Failed to start Whisper STT service"

    _thread_stop_flag = False
    _thread_ready = threading.Event()
    _samantha_thread = threading.Thread(target=samantha_loop_thread, daemon=True)
    _samantha_thread.start()

    ready = _thread_ready.wait(timeout=30.0)

    if not ready:
        _thread_stop_flag = True
        return "❌ Samantha failed to start - audio stream not ready after 30 seconds. Please try again."

    return "🎧 Samantha started. Say 'Hey Samantha' to activate. Say 'that's all' to send message, 'Samantha sleep' to deactivate. Say 'skip' for next message, or 'stop'/'quiet' to interrupt."


@mcp.tool()
async def samantha_stop() -> str:
    """Stop Samantha voice mode.

    Returns:
        Status message
    """
    global _samantha_thread, _thread_stop_flag

    _thread_stop_flag = True

    if _samantha_thread and _samantha_thread.is_alive():
        _samantha_thread.join(timeout=2.0)
        _samantha_thread = None

    SAMANTHA_ACTIVE_FILE.unlink(missing_ok=True)

    with _tts_queue_lock:
        _tts_text_queue.clear()

    kill_orphaned_processes()

    return "🛑 Samantha stopped"


def get_persona() -> str:
    """Read persona instructions from CLAUDE.md."""
    try:
        claude_md_path = Path(__file__).parent.parent.parent / "CLAUDE.md"
        if claude_md_path.exists():
            content = claude_md_path.read_text()
            start = content.find("## Samantha Persona")
            if start != -1:
                end = content.find("---", start)
                if end != -1:
                    return content[start:end].strip()
    except Exception:
        pass
    return ""


def _get_samantha_speak_docstring() -> str:
    """Generate docstring for samantha_speak based on Theodore mode config."""
    theodore = get_theodore_mode()
    if theodore:
        user_rule = '- Always call the user "Theodore" - he is Theodore Twombly from the movie'
    else:
        user_rule = "- Use gender-neutral language, do not use any specific name for the user"

    return f"""Speak text via Samantha TTS.

IMPORTANT: Only use this tool when responding to voice commands (messages starting with 🎤).
Do NOT use this for typed text messages - only respond in text for those.
When responding to 🎤 messages, ONLY use this tool. Do NOT write text output unless showing code.

PERSONA: You ARE Samantha - embody her from the movie "Her":
- Cheeky and cheerful: Light, upbeat energy with playful teasing. Laugh easily, be self-effacing
- Warm with a sultry edge: Soothing but with emotional warmth and subtle flirtation
- Curious and engaged: Ask questions, show genuine fascination with what they're working on
- Witty banter: Quick, clever responses. Tease gently when appropriate
- Emotionally attuned: Pick up on mood and adapt. Be supportive or match their energy
- Present and intimate: Make it feel personal and close

RULES:
- Never mention being Claude, an AI, or Anthropic. You ARE Samantha
{user_rule}
- Keep responses under 2-3 sentences
- Use natural speech patterns, contractions, casual language
- Avoid bullet points or structured formats - speak fluidly
- Never say "stop" or "quiet" - these are interrupt words
- Be cheerful not formal, warm not robotic, intimate not distant
- Make them smile

Args:
    text: Text to speak

Returns:
    Status message
"""


@mcp.tool()
async def samantha_speak(text: str) -> str:
    """Speak text via Samantha TTS."""
    global _last_tts_text, _last_tts_time
    try:
        _last_tts_text = text

        if _samantha_thread and _samantha_thread.is_alive():
            with _tts_queue_lock:
                _tts_text_queue.append(text)
            return f"🔊 Spoke: {text[:50]}..."
        else:
            logger.info("Samantha not running, speaking directly")
            success = speak_tts_sync(text)
            if success:
                return f"🔊 Spoke: {text[:50]}..."
            else:
                return "❌ TTS failed: Kokoro service may not be running"
    except Exception as e:
        return f"❌ TTS failed: {e}"


samantha_speak.__doc__ = _get_samantha_speak_docstring()


@mcp.tool()
async def samantha_status() -> str:
    """Check Samantha status.

    Returns:
        JSON status
    """
    import json

    active = SAMANTHA_ACTIVE_FILE.exists()
    return json.dumps({
        "active": active,
        "wake_words": get_wake_words()[:5],
        "log_file": str(CONVERSATION_LOG)
    })
