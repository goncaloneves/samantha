"""Samantha voice mode prompts."""

from samantha.server import mcp
from samantha.config import VOICE_MESSAGE_PREFIX


@mcp.prompt()
def start() -> str:
    """Start Samantha voice mode with wake word detection and TTS."""
    return f"""Use the samantha_start tool to start Samantha voice mode.

When you receive messages starting with {VOICE_MESSAGE_PREFIX}, respond by calling the samantha_speak MCP tool with your response text.

Rules:
1. For any {VOICE_MESSAGE_PREFIX} message, call samantha_speak with your spoken response
2. Keep responses concise and conversational
3. No markdown in speech - speak naturally
4. If transcribed text matches what you just spoke, ignore it (echo from mic)"""


@mcp.prompt()
def stop() -> str:
    """Stop Samantha voice mode."""
    return "Use the samantha_stop tool to stop Samantha voice mode."
