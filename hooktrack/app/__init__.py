"""HookTrack. Loads a local .env (if present) before anything reads the environment; real env vars win."""
import os as _os
from pathlib import Path as _Path

_env = _Path(__file__).parent.parent / ".env"
if _env.is_file():
    for _line in _env.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            _v = _v.strip()
            if _v and _k.strip() not in _os.environ:
                _os.environ[_k.strip()] = _v
