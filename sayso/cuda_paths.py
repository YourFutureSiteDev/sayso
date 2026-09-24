"""Put the pip-installed CUDA DLLs on the search path.

ctranslate2 links against cuBLAS and cuDNN at load time. On Windows those DLLs
ship inside the nvidia-* wheels rather than on PATH, so they have to be added
before faster_whisper is imported or the model load dies with a bare
"Library cublas64_12.dll is not found" error.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_SUBDIRS = ("cublas", "cuda_nvrtc", "cudnn")


def _roots() -> list[Path]:
    """Every place the NVIDIA DLLs could be, dev checkout or frozen exe."""
    here: list[Path] = []
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        # PyInstaller flattens package data next to the executable.
        here += [Path(bundle), Path(bundle) / "nvidia", Path(sys.executable).parent]
    else:
        site_packages = Path(sys.executable).parent.parent / "Lib" / "site-packages"
        here += [site_packages / "nvidia"]
    return [p for p in here if p.is_dir()]


def register() -> list[Path]:
    """Add every bundled NVIDIA bin directory to the DLL search path."""
    added: list[Path] = []
    for root in _roots():
        candidates = [root] + [root / name / sub
                               for name in _SUBDIRS for sub in ("bin", "lib")]
        for candidate in candidates:
            if not candidate.is_dir() or candidate in added:
                continue
            try:
                os.add_dll_directory(str(candidate))
            except OSError:
                continue
            os.environ["PATH"] = f"{candidate}{os.pathsep}" + os.environ.get("PATH", "")
            added.append(candidate)
    return added
