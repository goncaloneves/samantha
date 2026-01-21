"""Constants for Samantha voice assistant."""

import os
import re
from pathlib import Path

SAMPLE_RATE = 24000
WHISPER_SAMPLE_RATE = 16000
CHANNELS = 1
WHISPER_URL = os.getenv("WHISPER_URL", "http://localhost:2022/v1/audio/transcriptions")
KOKORO_URL = os.getenv("KOKORO_URL", "http://localhost:8880/v1/audio/speech")

SAMANTHA_DIR = Path.home() / ".samantha"
SAMANTHA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = SAMANTHA_DIR / "config.json"
SAMANTHA_ACTIVE_FILE = SAMANTHA_DIR / "samantha_active"
CONVERSATION_LOG = SAMANTHA_DIR / "conversation.log"
LOG_FILE = SAMANTHA_DIR / "samantha.log"
LOG_LEVEL = os.getenv("SAMANTHA_LOG_LEVEL", "DEBUG")

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

# IDEs with Claude Code extension/plugin support (Cmd/Ctrl+Escape focuses Claude input)
# VS Code family: VS Code, Cursor, Windsurf
# JetBrains family: IntelliJ IDEA, PyCharm, WebStorm, PhpStorm, GoLand, RubyMine, CLion, Rider, DataGrip, Android Studio
SUPPORTED_IDES = ["Cursor", "Code", "Visual Studio Code", "Windsurf",
                  "IntelliJ IDEA", "PyCharm", "WebStorm", "PhpStorm", "GoLand",
                  "RubyMine", "CLion", "Rider", "DataGrip", "Android Studio"]
# Process names vary by platform - these are used for detection
IDE_PROCESS_NAMES = {
    "Darwin": [
        # VS Code family
        "Cursor", "Code", "Code - Insiders", "Windsurf",
        # JetBrains family (macOS app names)
        "IntelliJ IDEA", "PyCharm", "WebStorm", "PhpStorm", "GoLand",
        "RubyMine", "CLion", "Rider", "DataGrip", "Android Studio",
    ],
    "Linux": [
        # VS Code family
        "cursor", "code", "code-insiders", "windsurf",
        # JetBrains family (Linux process names)
        "idea", "pycharm", "webstorm", "phpstorm", "goland",
        "rubymine", "clion", "rider", "datagrip", "studio",
    ],
    "Windows": [
        # VS Code family
        "Cursor", "Code", "Code - Insiders", "Windsurf",
        # JetBrains family (Windows process names)
        "idea64", "pycharm64", "webstorm64", "phpstorm64", "goland64",
        "rubymine64", "clion64", "rider64", "datagrip64", "studio64",
    ],
}
SUPPORTED_TERMINALS = ["Terminal", "iTerm2", "iTerm", "Warp", "Alacritty", "kitty", "gnome-terminal", "konsole", "xfce4-terminal", "xterm"]
SUPPORTED_APPS = SUPPORTED_IDES + ["Claude"] + SUPPORTED_TERMINALS
