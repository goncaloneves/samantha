<p align="center">
  <h1 align="center">🎙️ Samantha</h1>
  <p align="center"><strong>Voice assistant for Claude Code with wake word detection</strong></p>
  <p align="center">
    <em>Inspired by the AI from the movie "Her"</em>
  </p>
</p>

---

Samantha enables hands-free voice conversations with Claude Code. Say **"Hey Samantha"** anywhere in your speech to activate.

> 🙏 Thanks to [Mike Bailey](https://github.com/mbailey) for creating [VoiceMode](https://github.com/mbailey/voicemode) - the original project this is based on.

## ✨ Features

- **Wake word activation** - Say "Hey Samantha" to start
- **Continuous conversation** - Stay active until you say goodbye
- **Natural TTS responses** - Powered by Kokoro with multiple voice options
- **Interrupt support** - Say "stop", "quiet", "enough", or "halt" to interrupt TTS
- **Cross-platform** - Works on macOS, Linux, and Windows

## 🚀 Quick Start

### Installation

**Option 1: Install from GitHub (recommended)**
```bash
pip install git+https://github.com/goncaloneves/samantha.git
```

**Option 2: Clone and install locally**
```bash
git clone https://github.com/goncaloneves/samantha.git
cd samantha
pip install -e .
```

### Install Voice Services

Install local Whisper STT and Kokoro TTS:
```bash
samantha-install install
```

This clones, builds, and configures:
- **Whisper** - Local speech-to-text (runs on localhost:2022)
- **Kokoro** - Local text-to-speech (runs on localhost:8880)

Options:
```bash
samantha-install install -m base      # Use smaller/faster Whisper model (142MB)
samantha-install install --force      # Reinstall everything
```

### Add to Claude Code

```bash
claude mcp add samantha -- samantha
```

## 📖 How It Works

| Step | Description |
|------|-------------|
| 1. **Listen** | Samantha listens in the background using WebRTC VAD |
| 2. **Activate** | Say "Hey Samantha" anywhere in your sentence |
| 3. **Converse** | Speak naturally - messages are sent automatically after 1 second of silence |
| 4. **Finish early** | *(Optional)* Say **"that's all"** or **"send it"** to send without waiting for silence |
| 5. **Deactivate** | Say "Samantha sleep" or "Goodbye Samantha" to go idle |
| 6. **Skip** | Say **"skip"** or **"continue"** during TTS to skip to next message |
| 7. **Interrupt** | Say **"stop"** or **"quiet"** during TTS to stop and clear queue |

## 🎯 Usage

```
/samantha:start          # Start voice mode
"Hey Samantha, ..."      # Activate and speak (auto-sends after 1s silence)
"that's all"             # (Optional) Send immediately without waiting
"skip" / "continue"      # Skip to next TTS message
"stop" / "quiet"         # Stop TTS and clear queue
"Samantha sleep"         # Deactivate (go idle, keeps listening for wake word)
/samantha:stop           # Stop voice mode completely
```

### CLI Commands

```bash
samantha-install install    # Install Whisper + Kokoro
samantha-install status     # Check service status
samantha-install download-model small  # Download additional Whisper model
```

## 🔧 MCP Tools

| Tool | Description |
|------|-------------|
| `samantha_start` | Start continuous listening |
| `samantha_stop` | Stop voice mode |
| `samantha_speak` | Speak text via TTS |
| `samantha_status` | Check if voice mode is active |

## ⚙️ Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `SAMANTHA_WAKE_WORDS` | Comma-separated activation phrases | `hey samantha,samantha,...` |
| `SAMANTHA_DEACTIVATION_WORDS` | Comma-separated deactivation phrases | `samantha sleep,goodbye samantha,...` |
| `SAMANTHA_VOICE` | TTS voice to use | `af_aoede` |
| `SAMANTHA_TARGET_APP` | Target app for injection | Auto-detect |
| `SAMANTHA_INPUT_DEVICE` | Audio input device index | System default (dynamic) |
| `SAMANTHA_OUTPUT_DEVICE` | Audio output device index | System default (dynamic) |
| `SAMANTHA_SHOW_STATUS` | Show status messages (activated/deactivated/interrupted) | `true` |
| `SAMANTHA_THEODORE` | Call user "Theodore" (from the movie Her); if false, use gender-neutral language | `true` |
| `SAMANTHA_RESTORE_FOCUS` | Restore focus to previous app after injection | `true` |
| `SAMANTHA_MIN_AUDIO_ENERGY` | Minimum audio energy to send to Whisper (see below) | `3000` |

### Config File

Create `~/.samantha/config.json` for easy customization:

```json
{
  "voice": "af_aoede",
  "wake_words": ["hey samantha", "samantha", "hey sam"],
  "deactivation_words": ["samantha sleep", "goodbye samantha", "sam bye"],
  "show_status": true,
  "input_device": null,
  "output_device": null,
  "theodore": true,
  "restore_focus": true,
  "min_audio_energy": 3000
}
```

Config file values take precedence over environment variables.

### Environment Variables

Alternatively, use environment variables:

```bash
export SAMANTHA_VOICE="af_aoede"
export SAMANTHA_WAKE_WORDS="hey sam,hi sam,sam"
export SAMANTHA_DEACTIVATION_WORDS="sam sleep,bye sam"
export SAMANTHA_SHOW_STATUS="false"
export SAMANTHA_INPUT_DEVICE="2"   # Use `python -m sounddevice` to list devices
export SAMANTHA_OUTPUT_DEVICE="0"  # Audio output device (null = system default, dynamic)
export SAMANTHA_THEODORE="true"    # Set to "false" for gender-neutral language
export SAMANTHA_RESTORE_FOCUS="true"  # Return to previous app after injection
export SAMANTHA_MIN_AUDIO_ENERGY="3000"  # Audio energy threshold (see below)
```

### Audio Energy Threshold

The `min_audio_energy` setting filters low-energy audio before sending to Whisper. This prevents Whisper from hallucinating phrases like "Thank you for watching" on silence or background noise.

**Why this matters:** Whisper can produce confident transcriptions from pure noise. Without filtering, keyboard typing, mouse clicks, or ambient sounds can trigger false transcriptions.

**Recommended values** (16-bit PCM scale, max 32768):
| Value | Use Case |
|-------|----------|
| `1500` | Headset mic in quiet environment |
| `3000` | Balanced - laptop mic, filters typing (default) |
| `5000` | Noisy environment, requires clearer speech |

**How to tune:** Run Samantha and check logs for "Audio energy: X" messages. Your speech should be well above the threshold, while silence/typing should be below.

### Voice Options

```bash
export SAMANTHA_VOICE="af_aoede"  # Expressive voice inspired by the Greek muse of song
```

Available voices: `af_sky`, `af_heart`, `af_bella`, `af_nova`, `af_nicole`, `af_aoede`, `af_kore`, `bf_emma`, `bf_isabella`

### Whisper Model Selection

The default model is `small` (466MB) which provides good accuracy. To download additional models:

```bash
samantha-install download-model base    # Smaller/faster (142MB)
samantha-install download-model medium  # Better accuracy (1.5GB)
```

| Model | Size | Accuracy | Speed |
|-------|------|----------|-------|
| tiny | 74MB | Low | Fast |
| base | 142MB | Medium | Fast |
| **small** | **466MB** | **Good (default)** | **Balanced** |
| medium | 1.5GB | Better | Slower |
| large | 3GB | Best | Slowest |

## 🖥️ Platform Support

| Platform | Clipboard | Keystrokes | Window Detection |
|----------|-----------|------------|------------------|
| **macOS** | Built-in | AppleScript | System Events |
| **Linux (X11)** | `xclip`/`xsel` | `xdotool` | `xdotool`/`wmctrl` |
| **Linux (Wayland)** | `wl-copy` | `ydotool` | Limited |
| **Windows** | Built-in | `pyautogui` | `pygetwindow`/PowerShell |

### Supported Apps

**IDEs** (with Claude Code extension/plugin): Cursor, VS Code, Windsurf, IntelliJ IDEA, PyCharm, WebStorm, and other JetBrains IDEs

**Terminals**: Terminal, iTerm2, Warp, Alacritty, kitty, gnome-terminal, konsole, xfce4-terminal, xterm, Windows Terminal, PowerShell, cmd

### Smart Injection

Samantha tries IDEs first (using Cmd/Ctrl+Escape to focus Claude input), then falls back to terminal. After injection, it restores focus to your previous app (configurable via `restore_focus`).

## 🔬 Technical Details

- **VAD**: WebRTC VAD with aggressiveness level 1 for responsive speech detection
- **Audio filtering**: Energy threshold (default 3000) filters noise before Whisper to prevent hallucinations
- **STT**: Whisper (local, port 2022) with sanitization for artifacts like `[BLANK_AUDIO]`, `[Music]`
- **TTS**: Kokoro via sounddevice (uses system default output)
- **Recording**: 24kHz, resampled to 16kHz for VAD/Whisper
- **Silence detection**: 1s threshold with 1s initial grace period
- **Echo prevention**: Audio queue cleared during TTS with text-based filtering
- **Interrupt**: Works with phrases like "please stop" - dynamic word filtering prevents self-interruption

### How Injection Works

When you speak to Samantha, your voice is transcribed and "injected" into the target app (Cursor, Terminal, etc.) as if you typed it:

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   🎙️ Mic    │────▶│  Whisper    │────▶│  Clipboard  │────▶│  Activate   │────▶│   Paste +   │────▶│  Restore    │
│   Record    │     │  Transcribe │     │    Copy     │     │  Target App │     │    Enter    │     │    Focus    │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
     Audio              Text              pbcopy/            Bring window          Cmd+V +          Return to
                                          xclip              to focus              Return           previous app
```

**IDE-specific**: When injecting into an IDE (Cursor, VS Code, JetBrains), Samantha sends `Cmd+Escape` (macOS) or `Ctrl+Escape` (Linux/Windows) to focus the Claude input field before pasting.

This clipboard-based approach works reliably across all supported apps without requiring app-specific APIs.

## 📄 License

MIT
