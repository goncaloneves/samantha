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
- **Interrupt support** - Say "stop" or "quiet" to interrupt TTS
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
| 3. **Converse** | All speech is sent to Claude until deactivated |
| 4. **Deactivate** | Say "Samantha sleep" or "Goodbye Samantha" |
| 5. **Interrupt** | Say "stop" or "quiet" during TTS to stop playback |

## 🎯 Usage

```
/samantha:start          # Start voice mode
"Hey Samantha, ..."      # Activate and speak
"stop" / "quiet"         # Interrupt TTS
"Samantha sleep"         # Deactivate (go idle)
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
| `SAMANTHA_VOICE` | TTS voice to use | `af_sky` |
| `SAMANTHA_TARGET_APP` | Target app for injection | Auto-detect |
| `SAMANTHA_INPUT_DEVICE` | Audio input device index | System default |
| `SAMANTHA_SHOW_STATUS` | Show status messages (activated/deactivated/interrupted) | `true` |

### Config File

Create `~/.samantha/config.json` for easy customization:

```json
{
  "voice": "af_aoede",
  "wake_words": ["hey samantha", "samantha", "hey sam"],
  "deactivation_words": ["samantha sleep", "goodbye samantha", "sam bye"],
  "show_status": true,
  "input_device": null
}
```

Config file values take precedence over environment variables.

### Environment Variables

Alternatively, use environment variables:

```bash
export SAMANTHA_VOICE="af_sky"
export SAMANTHA_WAKE_WORDS="hey sam,hi sam,sam"
export SAMANTHA_DEACTIVATION_WORDS="sam sleep,bye sam"
export SAMANTHA_SHOW_STATUS="false"
export SAMANTHA_INPUT_DEVICE="2"  # Use `python -m sounddevice` to list devices
```

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

| Platform | Clipboard | Keystrokes |
|----------|-----------|------------|
| **macOS** | Built-in | Built-in |
| **Linux (X11)** | `xclip`/`xsel` | `xdotool` |
| **Linux (Wayland)** | `wl-copy` | `ydotool` |
| **Windows** | Built-in | `pyautogui` |

### Supported Apps

Cursor, Claude Code CLI, Terminal, iTerm2, Warp, Alacritty, kitty

## 🔬 Technical Details

- **VAD**: WebRTC VAD (aggressiveness=1) with audio normalization
- **STT**: Whisper (local, port 2022)
- **TTS**: Kokoro PCM streaming (local, port 8880)
- **Recording**: 24kHz, resampled to 16kHz for VAD/Whisper
- **Echo prevention**: Audio queue cleared during TTS with text-based filtering
- **Interrupt**: Dynamic word selection prevents self-interruption

## 📄 License

MIT
