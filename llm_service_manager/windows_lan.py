"""The Windows entry owns all input; Linux never injects a guessed WSL name."""

from . import __version__

SCRIPT_URL = f"https://raw.githubusercontent.com/gdrencn/llm-service-manager/v{__version__}/scripts/windows-bootstrap.ps1"


def command():
    return f"irm '{SCRIPT_URL}' | iex"
