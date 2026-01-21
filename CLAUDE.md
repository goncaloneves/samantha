# Samantha - Voice Assistant for AI Coding Tools

## Project Overview

Samantha is a voice assistant MCP server for AI coding tools (Claude, Gemini, Copilot, Aider, and more) with wake word detection. Say "Hey Samantha" anywhere in your speech to activate hands-free voice mode.

## Structure

```
samantha/
├── server.py              # FastMCP server entry point
├── cli.py                 # CLI entry point
├── config/
│   ├── constants.py       # Configuration constants
│   └── settings.py        # Config file and env var loading
├── core/
│   ├── loop.py            # Main voice assistant loop
│   └── state.py           # State management
├── audio/
│   ├── recording.py       # Microphone recording
│   ├── playback.py        # TTS playback
│   └── processing.py      # Audio processing utilities
├── speech/
│   └── stt.py             # Speech-to-text (Whisper)
├── injection/
│   ├── detection.py       # IDE/terminal detection
│   ├── inject.py          # Text injection
│   └── clipboard.py       # Clipboard utilities
├── tools/
│   └── samantha_tools.py  # MCP tool definitions
└── prompts/
    └── services.py        # MCP prompts
```

## Commands

```bash
pip install git+https://github.com/goncaloneves/samantha.git  # Install
samantha-install install   # Install Whisper + Kokoro
samantha                   # Run MCP server
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `samantha_start` | Start continuous listening with wake word detection |
| `samantha_stop` | Stop voice mode completely |
| `samantha_speak` | Speak text via TTS (use for voice responses) |
| `samantha_status` | Check if voice mode is active |

## Voice Interaction Flow

1. **Idle** → User says "Hey Samantha" → **Active** (plays activation chime)
2. **Active** → All speech sent to AI with `[🎙️ Voice - samantha_speak]` prefix
3. **Active** → User says "Samantha sleep" → **Idle** (plays deactivation chime)
4. **During TTS** → User says "skip" or "continue" → Skip to next queued message
5. **During TTS** → User says "stop" or "quiet" → TTS interrupted and queue cleared

## Configuration

### Config File (Recommended)

Create `~/.samantha/config.json` for easy customization:

```json
{
  "voice": "af_aoede",
  "wake_words": ["hey samantha", "samantha", "hey sam"],
  "deactivation_words": ["samantha sleep", "goodbye samantha"],
  "show_status": true,
  "theodore": true,
  "restore_focus": true,
  "min_audio_energy": 3000,
  "target_app": null,
  "injection_mode": "auto",
  "ai_process_pattern": "claude|gemini|copilot|aider|chatgpt|gpt|sgpt|codex",
  "ai_window_titles": ["claude", "gemini", "copilot", "aider", "chatgpt", "gpt"]
}
```

Config file values take precedence over environment variables.

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SAMANTHA_VOICE` | Kokoro TTS voice | `af_aoede` |
| `SAMANTHA_WAKE_WORDS` | Activation phrases | `hey samantha,samantha,...` |
| `SAMANTHA_DEACTIVATION_WORDS` | Deactivation phrases | `samantha sleep,goodbye samantha,...` |
| `SAMANTHA_SHOW_STATUS` | Show status messages in chat | `true` |
| `SAMANTHA_THEODORE` | Call user "Theodore" like in the movie | `true` |
| `SAMANTHA_RESTORE_FOCUS` | Return to previous app after injection | `true` |
| `SAMANTHA_MIN_AUDIO_ENERGY` | Audio threshold for noise filtering | `3000` |
| `SAMANTHA_TARGET_APP` | Target app for injection | Auto-detect |
| `SAMANTHA_INJECTION_MODE` | `auto`, `extension`, or `cli` | `auto` |
| `SAMANTHA_AI_PROCESS_PATTERN` | Regex to detect AI CLIs | `claude\|gemini\|copilot\|...` |
| `SAMANTHA_AI_WINDOW_TITLES` | Window titles to search | `claude,gemini,...` |
| `SAMANTHA_INPUT_DEVICE` | Audio input device index | System default |
| `SAMANTHA_OUTPUT_DEVICE` | Audio output device index | System default |

## Technical Details

- **Recording**: 24kHz, resampled to 16kHz for VAD/Whisper
- **VAD**: WebRTC VAD for responsive speech detection
- **Audio filtering**: Energy threshold (3000) filters background noise before Whisper
- **STT**: Whisper (localhost:2022)
- **TTS**: Kokoro (localhost:8880) via sounddevice
- **Injection**: Clipboard paste into IDE or terminal
- **Interrupt**: Dynamic word selection prevents TTS self-interruption
- **Session timeout**: 30 minutes of silence returns to idle

## Dependencies

- `mcp` - MCP server framework
- `sounddevice` - Audio recording/playback
- `numpy` - Audio processing
- `webrtcvad` - Voice activity detection
- `httpx` - HTTP client for STT/TTS
- `scipy` - Audio resampling
