"""Loads config.py and exposes settings. Handles first-run prompting."""
from __future__ import annotations

import sys
from pathlib import Path

from rich.console import Console
from rich.prompt import Prompt

CONFIG_PATH = Path(__file__).parent / "config.py"
EXAMPLE_CONFIG_PATH = Path(__file__).parent / "config.example.py"
console = Console()


def ensure_config_file() -> Path:
    """Make sure config.py exists. Bootstrap from config.example.py if not."""
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(EXAMPLE_CONFIG_PATH.read_text())
    return CONFIG_PATH


def _load_config_module():
    import importlib

    ensure_config_file()
    if "cinemastudio.config" in sys.modules:
        importlib.reload(sys.modules["cinemastudio.config"])
    from cinemastudio import config

    return config


def write_keys(ai_auto_key: str | None = None, anthropic_key: str | None = None) -> None:
    cfg = _load_config_module()
    new_ai_auto = ai_auto_key if ai_auto_key is not None else cfg.AI_AUTO_API_KEY
    new_anthropic = anthropic_key if anthropic_key is not None else cfg.ANTHROPIC_API_KEY

    text = CONFIG_PATH.read_text()
    text = _replace_assignment(text, "AI_AUTO_API_KEY", new_ai_auto)
    text = _replace_assignment(text, "ANTHROPIC_API_KEY", new_anthropic)
    CONFIG_PATH.write_text(text)


def _replace_assignment(source: str, name: str, value: str) -> str:
    safe_value = value.replace("\\", "\\\\").replace('"', '\\"')
    new_line = f'{name} = "{safe_value}"'
    out_lines = []
    replaced = False
    for line in source.splitlines():
        if line.startswith(f"{name} ="):
            out_lines.append(new_line)
            replaced = True
        else:
            out_lines.append(line)
    if not replaced:
        out_lines.append(new_line)
    return "\n".join(out_lines) + ("\n" if source.endswith("\n") else "")


def ensure_keys(require_anthropic: bool = True) -> None:
    """Prompt for any missing keys and persist them to config.py."""
    cfg = _load_config_module()
    changed = False

    if not cfg.AI_AUTO_API_KEY:
        console.print(
            "\n[bold cyan]ai-auto.io API key not set yet.[/bold cyan]\n"
            "You can find it in your ai-auto.io account dashboard."
        )
        key = Prompt.ask("Paste ai-auto.io API key", password=True).strip()
        if not key:
            console.print("[red]No key provided. Aborting.[/red]")
            sys.exit(1)
        write_keys(ai_auto_key=key)
        changed = True
        console.print("[green]Saved to cinemastudio/config.py (gitignored).[/green]")

    if require_anthropic and not _load_config_module().ANTHROPIC_API_KEY:
        console.print(
            "\n[bold cyan]Anthropic API key not set yet.[/bold cyan]\n"
            "Used for script + shot-list generation. Get one at https://console.anthropic.com/"
        )
        key = Prompt.ask("Paste Anthropic API key", password=True).strip()
        if not key:
            console.print("[red]No key provided. Aborting.[/red]")
            sys.exit(1)
        write_keys(anthropic_key=key)
        changed = True
        console.print("[green]Saved to cinemastudio/config.py (gitignored).[/green]")

    if changed:
        console.print(
            "[dim]Tip: never share config.py or paste your keys into chats/screenshots.[/dim]"
        )


def get():
    return _load_config_module()
