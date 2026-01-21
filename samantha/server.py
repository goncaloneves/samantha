#!/usr/bin/env python
"""Samantha MCP Server - Voice assistant with wake word detection."""

import os
import platform

if platform.system() == "Darwin":
    homebrew_paths = ["/opt/homebrew/bin", "/usr/local/bin"]
    current_path = os.environ.get("PATH", "")
    paths_to_add = [p for p in homebrew_paths if p not in current_path]
    if paths_to_add:
        os.environ["PATH"] = ":".join(paths_to_add) + ":" + current_path

from fastmcp import FastMCP

mcp = FastMCP("samantha")

from . import tools
from . import prompts
from . import resources


def main():
    """Run the Samantha MCP server."""
    import sys
    import warnings
    from .logging_setup import setup_logging
    from .version import __version__

    warnings.filterwarnings("ignore", category=SyntaxWarning, module="pydub.utils")
    warnings.filterwarnings("ignore", message="'audioop' is deprecated", category=DeprecationWarning)

    logger = setup_logging()
    logger.info(f"Starting Samantha v{__version__}")

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
