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
TTS_QUEUE_FILE = SAMANTHA_DIR / "tts_queue.txt"
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
    val = get_config("input_device")
    if val is not None and val != "null":
        return int(val) if val != -1 else None
    return None


def get_show_status() -> bool:
    val = get_config("show_status", "true")
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

SUPPORTED_APPS = ["Cursor", "Claude", "Terminal", "iTerm2", "iTerm", "Warp", "Alacritty", "kitty"]

_samantha_task: Optional[asyncio.Task] = None
_stop_event: Optional[asyncio.Event] = None
_samantha_thread = None
_thread_stop_flag = False


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

    # Log to file only - no injection (MCP tool calls already visible in Cursor)
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


def resample_audio(audio_data: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    """Resample audio data from one sample rate to another using scipy (high quality)."""
    if from_rate == to_rate:
        return audio_data
    from scipy import signal
    new_length = int(len(audio_data) * to_rate / from_rate)
    resampled = signal.resample(audio_data, new_length)
    return resampled.astype(np.int16)


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


def check_vad_speech(audio_data: np.ndarray, aggressiveness: int = 1) -> bool:
    """Check if audio contains speech using WebRTC VAD.

    Args:
        audio_data: Audio samples at SAMPLE_RATE (24kHz), int16
        aggressiveness: 0-3, higher = more strict (fewer false positives)

    Returns:
        True if speech detected, False otherwise
    """
    if not VAD_AVAILABLE:
        return True

    try:
        vad = webrtcvad.Vad(aggressiveness)

        # Normalize audio before VAD - MacBook mic has low sensitivity
        audio_data = normalize_audio(audio_data, target_peak=15000)

        # Resample to 16kHz for VAD (WebRTC VAD only supports 8k/16k/32k)
        vad_audio = resample_audio(audio_data, SAMPLE_RATE, WHISPER_SAMPLE_RATE)

        frame_duration_ms = 30
        frame_size = int(WHISPER_SAMPLE_RATE * frame_duration_ms / 1000)

        speech_frames = 0
        total_frames = 0

        for i in range(0, len(vad_audio) - frame_size, frame_size):
            frame = vad_audio[i:i + frame_size]
            frame_bytes = frame.tobytes()

            try:
                if vad.is_speech(frame_bytes, WHISPER_SAMPLE_RATE):
                    speech_frames += 1
            except Exception:
                pass
            total_frames += 1

        if total_frames == 0:
            return False

        speech_ratio = speech_frames / total_frames
        return speech_ratio > 0.1
    except Exception as e:
        logger.debug("VAD error: %s", e)
        return True


async def transcribe_audio(audio_data: np.ndarray) -> Optional[str]:
    """Transcribe audio using Whisper STT."""
    try:
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
    """Speak text using Kokoro TTS with PCM streaming directly to sounddevice.

    Can be interrupted by setting _tts_interrupt = True.
    """
    global _last_tts_text, _last_tts_time, _tts_interrupt
    logger.info("🔊 TTS: %s", text[:80] + "..." if len(text) > 80 else text)

    _last_tts_text = text
    _last_tts_time = time.time()
    _tts_interrupt = False

    stream = None
    interrupted = False
    try:
        import requests

        stream = sd.OutputStream(
            samplerate=24000,
            channels=1,
            dtype='int16',
            blocksize=1024,
            latency='low'
        )
        stream.start()

        with requests.post(
            KOKORO_URL,
            json={
                "model": "kokoro",
                "input": text,
                "voice": get_voice(),
                "response_format": "pcm",
                "stream": True
            },
            timeout=60.0,
            stream=True
        ) as response:
            if response.status_code != 200:
                logger.error("TTS error: HTTP %s", response.status_code)
                return False

            for chunk in response.iter_content(chunk_size=1024):
                if _tts_interrupt:
                    logger.info("🛑 TTS interrupted by user - aborting stream")
                    interrupted = True
                    stream.abort()
                    break
                if chunk:
                    audio_array = np.frombuffer(chunk, dtype=np.int16)
                    stream.write(audio_array)

        if not interrupted:
            stream.stop()
            log_conversation("TTS", text)
        return True
    except Exception as e:
        logger.error("TTS error: %s", e)
        return False
    finally:
        if stream:
            try:
                stream.close()
            except Exception:
                pass
        _tts_interrupt = False


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


async def record_command(max_duration: float = 60.0, silence_threshold: float = 3.0, min_recording: float = 2.0) -> Optional[str]:
    """Record user's voice command after wake word."""
    logger.info("🎙️ Recording...")

    chunk_duration = 1.0
    chunk_samples = int(SAMPLE_RATE * chunk_duration)
    audio_chunks = []
    start_time = time.time()
    last_speech_time = time.time()
    full_transcription = ""
    consecutive_silence = 0

    while True:
        elapsed = time.time() - start_time
        if elapsed >= max_duration:
            logger.info("Max duration reached")
            break

        recording = sd.rec(chunk_samples, samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=np.int16, device=get_input_device())
        sd.wait()
        audio_chunk = recording.flatten()

        has_speech = check_vad_speech(audio_chunk, aggressiveness=1)

        audio_chunks.append(audio_chunk)

        if has_speech:
            consecutive_silence = 0
            last_speech_time = time.time()
        else:
            consecutive_silence += 1
            logger.debug("🔇 Silence chunk %d/%d", consecutive_silence, int(silence_threshold / chunk_duration))

            if elapsed > min_recording and consecutive_silence >= int(silence_threshold / chunk_duration):
                logger.info("Silence detected after %.1fs (VAD)", elapsed)
                break

        if len(audio_chunks) >= 2:
            full_audio = np.concatenate(audio_chunks)
            text = await transcribe_audio(full_audio)

            if text and text not in ["[BLANK_AUDIO]", ""]:
                full_transcription = text
                logger.debug("📝 Current: %s", text[:80])

                if check_for_stop_phrase(text):
                    logger.info("Stop phrase detected in: %s", text[:80])
                    full_transcription = text
                    break

    # Final transcription of ALL audio to catch any last words
    if audio_chunks:
        full_audio = np.concatenate(audio_chunks)
        final_text = await transcribe_audio(full_audio)
        if final_text and final_text not in ["[BLANK_AUDIO]", ""] and len(final_text) >= len(full_transcription):
            full_transcription = final_text
            logger.info("📝 Final transcription updated: %s", full_transcription[:100])

    logger.info("📝 Final: %s", full_transcription[:100] if full_transcription else "None")
    return full_transcription if full_transcription else None


def clean_command(text: str) -> str:
    """Clean recorded command text. Removes anything AFTER stop phrases (keeps the stop phrase)."""
    cleaned = re.sub(r'\[[^\]]*\]', '', text).strip()

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
        # Check TTS queue file
        if TTS_QUEUE_FILE.exists():
            try:
                tts_text = TTS_QUEUE_FILE.read_text().strip()
                if tts_text:
                    TTS_QUEUE_FILE.unlink()
                    await speak_tts(tts_text)
                    return True
            except Exception:
                pass

        # Monitor clipboard for response
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

    interrupt_words = []
    if 'stop' not in tts_lower:
        interrupt_words.append('stop')
    if 'quiet' not in tts_lower:
        interrupt_words.append('quiet')

    return interrupt_words


def contains_interrupt_phrase(text: str) -> bool:
    """Check if text contains an active interrupt word.

    Uses dynamic interrupt words based on current TTS text to avoid
    the TTS triggering its own interrupt.
    """
    if not text:
        return False

    text_lower = text.lower()
    active_words = get_active_interrupt_words()

    for word in active_words:
        if word in text_lower:
            return True

    return False


_last_tts_text = ""
_last_tts_time = 0


def is_noise(text: str) -> bool:
    """Check if transcription is just background noise, not speech."""
    if not text:
        return True
    text_lower = text.lower().strip()
    noise_patterns = [
        "(", ")", "[", "]",
        "click", "clap", "ding", "bell", "tick", "thud", "bang",
        "engine", "revving", "keyboard", "typing", "noise",
        "blank_audio", "silence", "static", "hum", "buzz"
    ]
    for pattern in noise_patterns:
        if pattern in text_lower:
            return True
    if len(text_lower) < 3:
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

    # Direct containment check
    if text_lower in tts_lower or tts_lower in text_lower:
        return True

    # Word overlap check for longer responses
    text_words = set(text_lower.split())
    tts_words = set(tts_lower.split())
    if len(text_words) > 3:
        overlap = len(text_words & tts_words) / len(text_words)
        if overlap > 0.5:
            return True

    return False


_tts_queue: asyncio.Queue = None
_tts_playing = False
_pending_voice_command = False
_tts_start_time = 0
_tts_interrupt = False


async def tts_worker():
    """Background worker that plays TTS sequentially from queue."""
    global _tts_queue, _stop_event, _tts_playing
    while not _stop_event.is_set():
        try:
            text = await asyncio.wait_for(_tts_queue.get(), timeout=0.5)
            if text:
                _tts_playing = True
                speak_tts_sync(text)
                _tts_playing = False
        except asyncio.TimeoutError:
            continue
        except Exception as e:
            _tts_playing = False
            logger.error("TTS worker error: %s", e)


def transcribe_audio_sync(audio_data: np.ndarray) -> Optional[str]:
    """Synchronous transcribe for use in thread."""
    try:
        from pydub import AudioSegment
        import requests

        audio_data = normalize_audio(audio_data)

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
    global _thread_stop_flag, _tts_playing, _last_tts_time, _tts_start_time, _tts_interrupt

    device_name = "default"
    input_dev = get_input_device()
    if input_dev is not None:
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
    SILENCE_THRESHOLD_MS = 500
    MIN_RECORDING_DURATION = 0.3
    VAD_SAMPLE_RATE = 16000
    VAD_AGGRESSIVENESS = 1

    chunk_samples = int(SAMPLE_RATE * VAD_CHUNK_DURATION_MS / 1000)
    vad_chunk_samples = int(VAD_SAMPLE_RATE * VAD_CHUNK_DURATION_MS / 1000)
    silence_timeout = 1800.0  # 30 minutes

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
        # Always capture audio - we need it for interrupt detection during TTS
        audio_queue.put(indata.copy())

    try:
        vad = webrtcvad.Vad(VAD_AGGRESSIVENESS) if VAD_AVAILABLE else None

        with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=np.int16,
                           callback=audio_callback, blocksize=chunk_samples, device=get_input_device()):
            logger.debug("Started continuous audio stream")

            while not _thread_stop_flag:
                try:
                    # Check if we need to start TTS (runs in background thread)
                    if TTS_QUEUE_FILE.exists() and not _tts_playing:
                        try:
                            tts_text = TTS_QUEUE_FILE.read_text().strip()
                            TTS_QUEUE_FILE.unlink()
                            if tts_text:
                                # Set flag BEFORE starting TTS
                                _tts_playing = True

                                # Clear any accumulated audio before TTS
                                while not audio_queue.empty():
                                    try:
                                        audio_queue.get_nowait()
                                    except queue.Empty:
                                        break
                                speech_detected = False
                                audio_chunks = []
                                silence_duration_ms = 0
                                recording_start = 0

                                # Start TTS in background thread so we can listen for interrupts
                                import threading

                                def tts_thread_func():
                                    global _tts_playing, _last_tts_time, _tts_start_time
                                    active_words = get_active_interrupt_words()
                                    logger.info("🎯 Active interrupt words: %s", active_words)
                                    speak_tts_sync(tts_text)
                                    _last_tts_time = time.time()
                                    _tts_start_time = 0
                                    _tts_playing = False

                                tts_thread = threading.Thread(target=tts_thread_func, daemon=True)
                                tts_thread.start()
                        except Exception as e:
                            logger.error("TTS queue error: %s", e)
                            _tts_playing = False

                    # While TTS is playing, listen for interrupt trigger words
                    if _tts_playing:
                        try:
                            chunk = audio_queue.get(timeout=0.05)
                            chunk_flat = chunk.flatten()

                            # Always accumulate audio during TTS (don't rely on VAD - TTS interferes)
                            audio_chunks.append(chunk_flat)

                            # Check for interrupt every 300ms of accumulated audio
                            # Only start checking after 2 seconds to avoid echo from TTS start
                            accumulated_duration_ms = len(audio_chunks) * VAD_CHUNK_DURATION_MS
                            tts_elapsed = time.time() - _tts_start_time if _tts_start_time > 0 else 0
                            if accumulated_duration_ms >= 300 and tts_elapsed >= 2.0:
                                full_audio = np.concatenate(audio_chunks)
                                text = transcribe_audio_sync(full_audio)
                                logger.info("🔍 TTS interrupt check: %s", text[:50] if text else "None")

                                if text and contains_interrupt_phrase(text):
                                    logger.info("🛑 Interrupt detected: %s", text[:50])
                                    log_conversation("INTERRUPT", text)
                                    _tts_interrupt = True
                                    _tts_playing = False
                                    time.sleep(0.1)
                                    while not audio_queue.empty():
                                        try:
                                            audio_queue.get_nowait()
                                        except queue.Empty:
                                            break
                                    if get_show_status():
                                        inject_into_app("<!-- 🤫 [Speech interrupted] -->")
                                    if is_active:
                                        last_speech_time = time.time()

                                # Reset for next check window
                                audio_chunks = []

                        except queue.Empty:
                            pass
                        continue  # Skip normal audio processing while TTS is playing

                    # Post-TTS cleanup: clear audio queue and reset state
                    if _last_tts_time > 0 and time.time() - _last_tts_time < 0.2:
                        while not audio_queue.empty():
                            try:
                                audio_queue.get_nowait()
                            except queue.Empty:
                                break
                        speech_detected = False
                        audio_chunks = []
                        silence_duration_ms = 0
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
                            if recording_duration >= MIN_RECORDING_DURATION and silence_duration_ms >= SILENCE_THRESHOLD_MS:
                                logger.info("✓ Silence threshold reached after %.1fs", recording_duration)

                                # No timestamp-based discard - rely on is_echo() for text filtering
                                # The audio queue is cleared after TTS, so we shouldn't get echo audio

                                if not _tts_playing and audio_chunks:
                                    full_audio = np.concatenate(audio_chunks)
                                    text = transcribe_audio_sync(full_audio)

                                    # Debug: log what we got and why it might be filtered
                                    if text:
                                        if is_echo(text):
                                            logger.debug("Filtered as echo: %s", text[:50])
                                        elif is_noise(text):
                                            logger.debug("Filtered as noise: %s", text[:50])

                                    if text and text not in ["[BLANK_AUDIO]", ""] and not is_echo(text) and not is_noise(text):
                                        logger.info("📝 Heard (active=%s): %s", is_active, text[:100])

                                        if is_active:
                                            # Check for deactivation first
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


async def ensure_kokoro_running() -> bool:
    """Start Kokoro TTS if not already running."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(KOKORO_URL.replace("/v1/audio/speech", "/health"))
            if response.status_code == 200:
                return True
    except Exception:
        pass

    logger.info("Starting Kokoro TTS service...")
    try:
        plist_path = Path.home() / "Library/LaunchAgents/com.samantha.kokoro.plist"
        subprocess.run(["launchctl", "load", str(plist_path)], capture_output=True)
        subprocess.run(["launchctl", "start", "com.samantha.kokoro"], capture_output=True)
        for _ in range(10):
            await asyncio.sleep(1)
            try:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    response = await client.post(
                        KOKORO_URL,
                        json={"model": "kokoro", "input": "ready", "voice": "af_sarah"},
                    )
                    if response.status_code == 200:
                        logger.info("Kokoro TTS started successfully")
                        return True
            except Exception:
                pass
        logger.error("Failed to start Kokoro TTS")
        return False
    except Exception as e:
        logger.error("Error starting Kokoro: %s", e)
        return False


async def ensure_whisper_running() -> bool:
    """Start Whisper STT if not already running."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(WHISPER_URL.replace("/v1/audio/transcriptions", "/health"))
            if response.status_code == 200:
                return True
    except Exception:
        pass

    logger.info("Starting Whisper STT service...")
    try:
        plist_path = Path.home() / "Library/LaunchAgents/com.samantha.whisper.plist"
        subprocess.run(["launchctl", "load", str(plist_path)], capture_output=True)
        subprocess.run(["launchctl", "start", "com.samantha.whisper"], capture_output=True)
        for _ in range(10):
            await asyncio.sleep(1)
            try:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    response = await client.get(WHISPER_URL.replace("/v1/audio/transcriptions", "/health"))
                    if response.status_code == 200:
                        logger.info("Whisper STT started successfully")
                        return True
            except Exception:
                pass
        logger.error("Failed to start Whisper STT")
        return False
    except Exception as e:
        logger.error("Error starting Whisper: %s", e)
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
    global _samantha_thread, _thread_stop_flag
    import threading

    if _samantha_thread and _samantha_thread.is_alive():
        return "Samantha is already running"

    SAMANTHA_DIR.mkdir(parents=True, exist_ok=True)
    SAMANTHA_ACTIVE_FILE.touch()

    kokoro_ok = await ensure_kokoro_running()
    whisper_ok = await ensure_whisper_running()

    if not kokoro_ok:
        return "❌ Failed to start Kokoro TTS service"
    if not whisper_ok:
        return "❌ Failed to start Whisper STT service"

    _thread_stop_flag = False
    _samantha_thread = threading.Thread(target=samantha_loop_thread, daemon=True)
    _samantha_thread.start()

    return "🎧 Samantha started. Say 'Hey Samantha' to activate. Say 'Samantha sleep' to deactivate. Say 'stop' or 'quiet' to interrupt TTS."


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
    TTS_QUEUE_FILE.unlink(missing_ok=True)

    # Kill any remaining samantha processes
    import subprocess
    try:
        subprocess.run(["pkill", "-f", "samantha"], capture_output=True, timeout=5)
    except Exception:
        pass

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


@mcp.tool()
async def samantha_speak(text: str, include_persona: bool = True) -> str:
    """Speak text via Samantha TTS.

    IMPORTANT: Only use this tool when responding to voice commands (messages starting with 🎤).
    Do NOT use this for typed text messages - only respond in text for those.

    Args:
        text: Text to speak
        include_persona: Whether to include persona guidelines in response (default True)

    Returns:
        Status message with persona reminder
    """
    global _last_tts_text, _last_tts_time, _tts_start_time
    try:
        _last_tts_text = text
        _tts_start_time = time.time()

        # Write to TTS queue file - the listening thread will pick it up and set _tts_playing
        TTS_QUEUE_FILE.write_text(text)

        result = f"🔊 Spoke: {text[:50]}..."

        if include_persona:
            persona = get_persona()
            if persona:
                # Compact single-line reminder - include full persona
                compact = ' '.join(persona.replace('\n', ' ').replace('  ', ' ').split())
                result += f" | Persona: {compact}"

        return result
    except Exception as e:
        return f"❌ TTS failed: {e}"


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
