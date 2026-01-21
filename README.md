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
| 3. **Converse** | All speech is sent to Claude until deactivated |
| 4. **Finish message** | Say **"that's all"**, **"send it"**, or **"over and out"** to send immediately |
| 5. **Deactivate** | Say "Samantha sleep" or "Goodbye Samantha" to go idle |
| 6. **Skip** | Say **"continue"** or **"skip"** during TTS to skip to the next queued message |
| 7. **Interrupt** | Say **"stop"**, **"quiet"**, **"enough"**, or **"halt"** during TTS to clear the queue |

## 🎯 Usage

```
/samantha:start          # Start voice mode
"Hey Samantha, ..."      # Activate and speak
"that's all"             # Finish message (send immediately)
"continue" / "skip"      # Skip to next message in queue
"stop" / "quiet"         # Interrupt TTS and clear queue
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
| `SAMANTHA_VOICE` | TTS voice to use | `af_aoede` |
| `SAMANTHA_TARGET_APP` | Target app for injection | Auto-detect |
| `SAMANTHA_INPUT_DEVICE` | Audio input device index | System default (dynamic) |
| `SAMANTHA_OUTPUT_DEVICE` | Audio output device index | System default (dynamic) |
| `SAMANTHA_SHOW_STATUS` | Show status messages (activated/deactivated/interrupted) | `true` |
| `SAMANTHA_THEODORE` | Call user "Theodore" (from the movie Her); if false, use gender-neutral language | `true` |

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
  "theodore": true
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

- **VAD**: WebRTC VAD with aggressiveness level 1 for responsive speech detection
- **Audio filtering**: Energy threshold (1500) filters low-amplitude noise before Whisper
- **STT**: Whisper (local, port 2022) with sanitization for artifacts like `[BLANK_AUDIO]`, `[Music]`
- **TTS**: Kokoro via sounddevice (uses system default output)
- **Recording**: 24kHz, resampled to 16kHz for VAD/Whisper
- **Silence detection**: 1s threshold with 1s initial grace period
- **Echo prevention**: Audio queue cleared during TTS with text-based filtering
- **Interrupt**: Works with phrases like "please stop" - dynamic word filtering prevents self-interruption

### How Injection Works

When you speak to Samantha, your voice is transcribed and "injected" into the target app (Cursor, Terminal, etc.) as if you typed it:

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   🎙️ Mic    │────▶│  Whisper    │────▶│  Clipboard  │────▶│  Activate   │────▶│   Paste +   │
│   Record    │     │  Transcribe │     │    Copy     │     │  Target App │     │    Enter    │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
     Audio              Text              pbcopy/            Bring window          Cmd+V +
                                          xclip              to focus              Return
```

> **Important**: Make sure your cursor is in the input field where you want the text to appear before speaking. The paste will go wherever your cursor was last focused in the target app.

This clipboard-based approach works reliably across all supported apps without requiring app-specific APIs.

## 📄 License

MIT
