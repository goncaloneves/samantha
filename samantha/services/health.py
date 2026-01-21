"""Health check and service management for Samantha."""

import asyncio
import logging
import platform
import shutil
import subprocess

try:
    import httpx
except ImportError:
    httpx = None

from samantha.config import SAMANTHA_DIR

logger = logging.getLogger("samantha")


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
