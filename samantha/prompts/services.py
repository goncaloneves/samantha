"""Samantha voice mode prompts."""

from samantha.server import mcp


@mcp.prompt()
def start() -> str:
    """Start Samantha voice mode with wake word detection and TTS."""
    return "Use the samantha_start tool to start Samantha voice mode."


@mcp.prompt()
def stop() -> str:
    """Stop Samantha voice mode."""
    return "Use the samantha_stop tool to stop Samantha voice mode."
