#!/usr/bin/env python
"""Samantha CLI - Installation and management commands."""

import subprocess
import shutil
import sys
import os
import platform
from pathlib import Path

import click


LOGO = """
╔══════════════════════════════════════════════════════════════════════════╗
║                                                                          ║
║  ███████╗ █████╗ ███╗   ███╗ █████╗ ███╗   ██╗████████╗██╗  ██╗ █████╗   ║
║  ██╔════╝██╔══██╗████╗ ████║██╔══██╗████╗  ██║╚══██╔══╝██║  ██║██╔══██╗  ║
║  ███████╗███████║██╔████╔██║███████║██╔██╗ ██║   ██║   ███████║███████║  ║
║  ╚════██║██╔══██║██║╚██╔╝██║██╔══██║██║╚██╗██║   ██║   ██╔══██║██╔══██║  ║
║  ███████║██║  ██║██║ ╚═╝ ██║██║  ██║██║ ╚████║   ██║   ██║  ██║██║  ██║  ║
║  ╚══════╝╚═╝  ╚═╝╚═╝     ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝   ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝  ║
║                                                                          ║
║                 🎙️  Voice Assistant for Claude Code                      ║
║                                                                          ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

SAMANTHA_DIR = Path.home() / ".samantha"
SERVICES_DIR = SAMANTHA_DIR / "services"
WHISPER_DIR = SERVICES_DIR / "whisper"
KOKORO_DIR = SERVICES_DIR / "kokoro"


def print_logo():
    click.echo('\033[38;5;208m' + '\033[1m' + LOGO + '\033[0m')


def print_step(message: str):
    click.echo(click.style(f"🔧 {message}", fg='blue'))


def print_success(message: str):
    click.echo(click.style(f"✅ {message}", fg='green'))


def print_warning(message: str):
    click.echo('\033[38;5;208m' + f"⚠️  {message}" + '\033[0m')


def print_error(message: str):
    click.echo(click.style(f"❌ {message}", fg='red'))


def check_command(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def run_command(cmd: list, cwd: Path = None, capture: bool = False) -> subprocess.CompletedProcess:
    kwargs = {"cwd": cwd, "check": True}
    if capture:
        kwargs["capture_output"] = True
        kwargs["text"] = True
    return subprocess.run(cmd, **kwargs)


def install_whisper(model: str = "base", force: bool = False) -> bool:
    """Install whisper.cpp with the specified model."""
    print_step("Installing Whisper STT...")

    system = platform.system()
    is_macos = system == "Darwin"

    SERVICES_DIR.mkdir(parents=True, exist_ok=True)

    if WHISPER_DIR.exists() and not force:
        if (WHISPER_DIR / "build" / "bin" / "whisper-server").exists():
            print_success("Whisper already installed")
            return download_whisper_model(model)

    if not check_command("cmake"):
        if is_macos and check_command("brew"):
            print_step("Installing cmake via Homebrew...")
            run_command(["brew", "install", "cmake"])
        else:
            print_error("cmake is required. Install it first:")
            if is_macos:
                click.echo("  brew install cmake")
            else:
                click.echo("  sudo apt install cmake")
            return False

    if not check_command("git"):
        print_error("git is required")
        return False

    if force and WHISPER_DIR.exists():
        shutil.rmtree(WHISPER_DIR)

    if not WHISPER_DIR.exists():
        print_step("Cloning whisper.cpp...")
        run_command([
            "git", "clone", "--depth", "1",
            "https://github.com/ggerganov/whisper.cpp.git",
            str(WHISPER_DIR)
        ])

    print_step("Building whisper.cpp (this may take a few minutes)...")

    cmake_flags = ["-DWHISPER_SDL2=OFF"]
    if is_macos:
        cmake_flags.append("-DGGML_METAL=ON")
        if platform.machine() == "arm64":
            cmake_flags.extend([
                "-DWHISPER_COREML=ON",
                "-DWHISPER_COREML_ALLOW_FALLBACK=ON"
            ])

    run_command(["cmake", "-B", "build"] + cmake_flags, cwd=WHISPER_DIR)

    cpu_count = os.cpu_count() or 4
    run_command([
        "cmake", "--build", "build",
        "-j", str(cpu_count),
        "--config", "Release"
    ], cwd=WHISPER_DIR)

    server_path = WHISPER_DIR / "build" / "bin" / "whisper-server"
    if not server_path.exists():
        print_error("Build failed - whisper-server not found")
        return False

    print_success("Whisper built successfully")

    return download_whisper_model(model)


def download_whisper_model(model: str = "base") -> bool:
    """Download a whisper model."""
    models_dir = WHISPER_DIR / "models"
    models_dir.mkdir(parents=True, exist_ok=True)

    model_file = models_dir / f"ggml-{model}.bin"
    if model_file.exists():
        print_success(f"Model '{model}' already downloaded")
        return True

    print_step(f"Downloading Whisper model '{model}'...")

    download_script = WHISPER_DIR / "models" / "download-ggml-model.sh"
    if download_script.exists():
        run_command(["bash", str(download_script), model], cwd=WHISPER_DIR / "models")
    else:
        base_url = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
        model_url = f"{base_url}/ggml-{model}.bin"
        run_command(["curl", "-L", "-o", str(model_file), model_url])

    if model_file.exists():
        print_success(f"Model '{model}' downloaded")
        return True
    else:
        print_error(f"Failed to download model '{model}'")
        return False


def install_kokoro(force: bool = False) -> bool:
    """Install kokoro-fastapi TTS service."""
    print_step("Installing Kokoro TTS...")

    system = platform.system()

    SERVICES_DIR.mkdir(parents=True, exist_ok=True)

    if KOKORO_DIR.exists() and not force:
        if (KOKORO_DIR / "main.py").exists():
            print_success("Kokoro already installed")
            return True

    if not check_command("git"):
        print_error("git is required")
        return False

    if not check_command("uv"):
        print_step("Installing UV package manager...")
        subprocess.run(
            "curl -LsSf https://astral.sh/uv/install.sh | sh",
            shell=True,
            check=True
        )
        os.environ["PATH"] = f"{Path.home() / '.local' / 'bin'}:{os.environ['PATH']}"

    if force and KOKORO_DIR.exists():
        shutil.rmtree(KOKORO_DIR)

    if not KOKORO_DIR.exists():
        print_step("Cloning kokoro-fastapi...")
        run_command([
            "git", "clone", "--depth", "1",
            "https://github.com/remsky/kokoro-fastapi.git",
            str(KOKORO_DIR)
        ])

    venv_path = KOKORO_DIR / ".venv"
    if not venv_path.exists():
        print_step("Creating virtual environment...")
        run_command(["uv", "venv"], cwd=KOKORO_DIR)

    if system == "Darwin":
        start_script = "start-gpu_mac.sh"
    elif check_command("nvidia-smi"):
        start_script = "start-gpu.sh"
    else:
        start_script = "start-cpu.sh"

    script_path = KOKORO_DIR / start_script
    if not script_path.exists():
        print_error(f"Start script not found: {start_script}")
        return False

    print_success("Kokoro installed successfully")
    click.echo(f"  Start with: cd {KOKORO_DIR} && ./{start_script}")

    return True


def setup_launchd_service(name: str, script_path: Path, port: int):
    """Set up a macOS launchd service."""
    if platform.system() != "Darwin":
        return

    launchagents_dir = Path.home() / "Library" / "LaunchAgents"
    launchagents_dir.mkdir(parents=True, exist_ok=True)

    plist_name = f"com.samantha.{name}.plist"
    plist_path = launchagents_dir / plist_name

    log_dir = SAMANTHA_DIR / "logs" / name
    log_dir.mkdir(parents=True, exist_ok=True)

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.samantha.{name}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{script_path}</string>
    </array>
    <key>RunAtLoad</key>
    <false/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>{log_dir}/{name}.out.log</string>
    <key>StandardErrorPath</key>
    <string>{log_dir}/{name}.err.log</string>
    <key>WorkingDirectory</key>
    <string>{script_path.parent}</string>
</dict>
</plist>
"""

    try:
        subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
    except Exception:
        pass

    plist_path.write_text(plist_content)
    print_success(f"LaunchAgent created: {plist_name}")


def create_whisper_start_script() -> Path:
    """Create a start script for whisper-server."""
    bin_dir = WHISPER_DIR / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    script_path = bin_dir / "start-whisper-server.sh"

    script_content = f"""#!/bin/bash
WHISPER_DIR="{WHISPER_DIR}"
MODEL_NAME="${{SAMANTHA_WHISPER_MODEL:-base}}"
MODEL_PATH="$WHISPER_DIR/models/ggml-$MODEL_NAME.bin"
PORT="${{SAMANTHA_WHISPER_PORT:-2022}}"

if [ ! -f "$MODEL_PATH" ]; then
    echo "Model not found: $MODEL_PATH"
    FALLBACK=$(ls -1 "$WHISPER_DIR/models/" 2>/dev/null | grep "^ggml-.*\\.bin$" | head -1)
    if [ -n "$FALLBACK" ]; then
        MODEL_PATH="$WHISPER_DIR/models/$FALLBACK"
        echo "Using fallback: $MODEL_PATH"
    else
        echo "No models found"
        exit 1
    fi
fi

SERVER_BIN="$WHISPER_DIR/build/bin/whisper-server"
if [ ! -f "$SERVER_BIN" ]; then
    echo "whisper-server not found"
    exit 1
fi

exec "$SERVER_BIN" \\
    --host 0.0.0.0 \\
    --port "$PORT" \\
    --model "$MODEL_PATH" \\
    --inference-path /v1/audio/transcriptions \\
    --threads 8
"""

    script_path.write_text(script_content)
    script_path.chmod(0o755)

    return script_path


@click.group()
def cli():
    """Samantha - Voice assistant for Claude Code."""
    pass


@cli.command()
@click.option('-y', '--yes', is_flag=True, help='Run without prompts (auto-accept all)')
@click.option('-m', '--model', default='small',
              type=click.Choice(['tiny', 'base', 'small', 'medium', 'large-v2', 'large-v3']),
              help='Whisper model to download (default: small)')
@click.option('--force', is_flag=True, help='Force reinstall even if already installed')
def install(yes, model, force):
    """Install Whisper STT and Kokoro TTS services.

    This command installs the local voice services required for Samantha:

    \b
    - Whisper (speech-to-text) on port 2022
    - Kokoro (text-to-speech) on port 8880

    Both services are required for Samantha to work.
    The services run locally for privacy and low latency.

    \b
    Examples:
      samantha-install install              # Install both services
      samantha-install install -m base      # Use smaller/faster Whisper model
      samantha-install install --force      # Reinstall everything
    """
    print_logo()
    click.echo()

    click.echo("This will install:")
    click.echo(f"  • Whisper STT (model: {model}) - localhost:2022")
    click.echo("  • Kokoro TTS - localhost:8880")
    click.echo()

    if not yes:
        if not click.confirm("Continue with installation?", default=True):
            click.echo("Installation cancelled")
            return

    click.echo()
    success = True

    try:
        if not install_whisper(model=model, force=force):
            success = False
    except subprocess.CalledProcessError as e:
        print_error(f"Whisper installation failed: {e}")
        success = False
    except Exception as e:
        print_error(f"Whisper installation error: {e}")
        success = False

    click.echo()

    try:
        if not install_kokoro(force=force):
            success = False
    except subprocess.CalledProcessError as e:
        print_error(f"Kokoro installation failed: {e}")
        success = False
    except Exception as e:
        print_error(f"Kokoro installation error: {e}")
        success = False

    click.echo()

    if platform.system() == "Darwin":
        whisper_script = create_whisper_start_script()
        setup_launchd_service("whisper", whisper_script, 2022)

    click.echo()
    click.echo("━" * 50)

    if success:
        print_success("Installation complete!")
        click.echo("━" * 50)
        click.echo()
        click.echo("Start services manually:")
        click.echo(f"  Whisper: {WHISPER_DIR}/bin/start-whisper-server.sh")
        script = "start-gpu_mac.sh" if platform.system() == "Darwin" else "start-cpu.sh"
        click.echo(f"  Kokoro:  cd {KOKORO_DIR} && ./{script}")
        click.echo()
        click.echo("Or on macOS, enable as services:")
        click.echo("  launchctl load ~/Library/LaunchAgents/com.samantha.whisper.plist")
        click.echo()
        click.echo("Add Samantha to Claude Code:")
        click.echo("  claude mcp add samantha -- samantha")
    else:
        print_error("Installation completed with errors")
        click.echo("━" * 50)
        sys.exit(1)


@cli.command()
def status():
    """Check status of voice services."""
    import httpx

    click.echo("Checking voice services...\n")

    services = [
        ("Whisper STT", "http://localhost:2022/health", WHISPER_DIR / "build" / "bin" / "whisper-server"),
        ("Kokoro TTS", "http://localhost:8880/health", KOKORO_DIR / "main.py"),
    ]

    for name, url, install_path in services:
        installed = install_path.exists() if install_path else False

        if not installed:
            print_warning(f"{name}: Not installed")
            continue

        try:
            response = httpx.get(url, timeout=2.0)
            if response.status_code == 200:
                print_success(f"{name}: Running")
            else:
                print_warning(f"{name}: Unhealthy (status {response.status_code})")
        except httpx.ConnectError:
            click.echo(f"  {name}: Installed but not running")
        except Exception as e:
            print_error(f"{name}: Error - {e}")

    click.echo()
    click.echo("Installation paths:")
    click.echo(f"  Whisper: {WHISPER_DIR}")
    click.echo(f"  Kokoro:  {KOKORO_DIR}")


@cli.command()
@click.argument('model', default='base',
                type=click.Choice(['tiny', 'base', 'small', 'medium', 'large-v2', 'large-v3']))
def download_model(model):
    """Download an additional Whisper model."""
    if not WHISPER_DIR.exists():
        print_error("Whisper not installed. Run 'samantha install' first.")
        sys.exit(1)

    download_whisper_model(model)


def main():
    cli()


if __name__ == "__main__":
    main()
