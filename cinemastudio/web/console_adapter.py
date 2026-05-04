"""Route rich Console output from pipeline modules into the JobRunner emitter.

The CLI pipeline writes progress with `console.log(...)` / `console.print(...)`.
For the web UI we temporarily replace those Console instances with a shim that
forwards each line to the job's log callback.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Callable

from cinemastudio.pipeline import generate as gen_mod
from cinemastudio.pipeline import moodboard as mb_mod

_TAG_RE = re.compile(r"\[/?[^\]]+\]")


def _strip(value: object) -> str:
    text = " ".join(str(v) for v in (value if isinstance(value, tuple) else (value,)))
    return _TAG_RE.sub("", text).strip()


class _ForwardConsole:
    def __init__(self, emit: Callable[[str, str], None]):
        self._emit = emit

    def log(self, *args, **_kwargs) -> None:
        message = _strip(args)
        if message:
            self._emit("info", message)

    def print(self, *args, **_kwargs) -> None:
        message = _strip(args)
        if message:
            self._emit("info", message)


@contextmanager
def capture_pipeline_logs(emit: Callable[[str, str], None]):
    """Temporarily replace pipeline module consoles so logs route to `emit`."""
    forward = _ForwardConsole(emit)
    saved = {
        "moodboard": mb_mod.console,
        "generate": gen_mod.console,
    }
    mb_mod.console = forward
    gen_mod.console = forward
    try:
        yield
    finally:
        mb_mod.console = saved["moodboard"]
        gen_mod.console = saved["generate"]
