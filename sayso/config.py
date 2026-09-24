"""Settings, loaded from config.json next to the package and merged over defaults."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field, asdict
from pathlib import Path

# In a dev checkout this is C:\Sayso. In the frozen exe it points inside
# PyInstaller's unpacked bundle, which is why nothing writable lives here.
ROOT = Path(__file__).resolve().parent.parent


def _data_dir() -> Path:
    """Where settings and logs live.

    Deliberately not beside the executable: `build.cmd` deletes dist\\Sayso
    outright, which would take the hotkey, microphone and vocabulary with it.
    Deliberately not inside the frozen bundle either, since that is a temporary
    unpack directory.

    One folder serves both the exe and `run.cmd`, so a setting changed in the
    window is the setting the command line sees.
    """
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    directory = Path(base) / "Sayso" if base else ROOT
    directory.mkdir(parents=True, exist_ok=True)
    return directory


DATA_DIR = _data_dir()
CONFIG_PATH = DATA_DIR / "config.json"
_LEGACY_CONFIG = ROOT / "config.json"


def _migrate_legacy() -> None:
    """Move a config written before settings had their own folder."""
    if CONFIG_PATH.exists() or not _LEGACY_CONFIG.is_file():
        return
    try:
        shutil.copy2(_LEGACY_CONFIG, CONFIG_PATH)
    except OSError:
        pass

# A general starter list. Whisper already knows ordinary English; what it
# fumbles is proper nouns and jargon, so only those belong here.
#
# **Add your own** in config.json rather than editing this. Your names, your
# clients, your street, the products you talk about. That is by far the biggest
# accuracy gain available, because it steers the model before it decides
# instead of patching the text afterwards.
VOCABULARY = [
    # Tools and platforms
    "GitHub", "GitLab", "Docker", "Kubernetes", "Cloudflare", "AWS", "Azure",
    "Postgres", "PostgreSQL", "SQLite", "Redis", "nginx", "systemd", "cron",
    "npm", "pnpm", "Node.js", "Deno", "uv", "Vite", "Webpack",
    # Languages and formats
    "Python", "JavaScript", "TypeScript", "Rust", "Golang", "JSON", "YAML",
    "TOML", "CSS", "HTML", "SQL", "Markdown", "regex",
    # Everyday development words Whisper likes to split or mishear
    "repo", "repos", "localhost", "webhook", "endpoint", "middleware",
    "async", "await", "boolean", "enum", "struct", "API", "CLI", "SDK",
    "URL", "UUID", "OAuth", "JWT", "CORS", "SSH", "TLS", "DNS", "VPS",
    # AI and speech
    "Whisper", "LLM", "MCP", "Claude", "ChatGPT", "Anthropic", "OpenAI",
    "prompt", "token", "embedding", "inference",
]

# Names whose capitalisation is restored after transcription. Whisper hears
# these right and then writes them flat ("github", "postgres"), so this puts
# the capitals back without touching a single word.
#
# Only terms with one true spelling go in here. Anything that is also an
# ordinary English word must stay out, or it gets wrongly capitalised
# mid-sentence: that rules out words like Square, Worker, Apple, Amazon,
# Swift, Rust and Go, which are ordinary words as often as they are names.
CASING = [
    "GitHub", "GitLab", "JavaScript", "TypeScript", "SQLite", "PostgreSQL",
    "Node.js", "Cloudflare", "Kubernetes", "Docker", "Anthropic", "OpenAI",
    "ChatGPT",
    "JSON", "YAML", "TOML", "HTML", "CSS", "SQL", "API", "CLI", "SDK", "URL",
    "UUID", "OAuth", "JWT", "CORS", "SSH", "TLS", "DNS", "VPS", "GPU", "CPU",
    "RAM", "MCP", "LLM", "HTTP", "HTTPS",
]

# Fixed substitutions applied after transcription. Only for things Whisper gets
# wrong the same way every time; everything else is left exactly as spoken.
# Gaps in a key match a space, a hyphen or nothing, so one entry catches
# "web hook", "web-hook" and "webhook".
CORRECTIONS = {
    "get hub": "GitHub",
    "git hub": "GitHub",
    "node js": "Node.js",
    "vs code": "VS Code",
    "j son": "JSON",
    "sequel lite": "SQLite",
    "post gres": "Postgres",
    "cloud flare": "Cloudflare",
    "java script": "JavaScript",
    "type script": "TypeScript",
    "local host": "localhost",
    "web hook": "webhook",
    "end point": "endpoint",
    "oh auth": "OAuth",
    "you you i d": "UUID",
}


@dataclass
class Config:
    # --- hotkey -----------------------------------------------------------
    # Right Alt: easy to reach, and almost nothing else on Windows uses it.
    hotkey: str = "alt_r"
    # Press once to start and once to stop. Holding the key also works and
    # ends the dictation on release, so both habits do the right thing.
    tap_latch_seconds: float = 0.4

    # --- history ----------------------------------------------------------
    # 0 keeps everything. Nothing leaves the machine either way.
    keep_history_days: int = 0

    # --- model ------------------------------------------------------------
    # large-v3 is the most accurate Whisper release. large-v3-turbo is ~4x
    # faster and about a point worse; swap it in if the GPU is busy.
    model: str = "large-v3"
    device: str = "cuda"
    compute_type: str = "float16"
    # Unload after this many seconds idle so other GPU work gets the VRAM
    # back. 0 keeps it resident.
    idle_unload_seconds: float = 300.0

    # --- decoding ---------------------------------------------------------
    language: str = "en"
    beam_size: int = 5
    best_of: int = 5
    patience: float = 1.0
    # Each fallback is a retry at a hotter temperature when the greedy pass
    # looks like a hallucination. Costs nothing when the first pass is clean.
    temperature_fallback: tuple[float, ...] = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
    compression_ratio_threshold: float = 2.4
    # These two DISCARD segments, they do not just score them. Set tight, whole
    # sentences vanish with no error and the result reads like a summary of
    # what you said. Loose enough that only genuine silence is dropped.
    log_prob_threshold: float = -1.6
    no_speech_threshold: float = 0.85
    # On: Whisper works in 30-second windows, and without the previous text it
    # restarts cold at every boundary, which wrecks grammar across the joins in
    # a long dictation. The loop this can cause is already caught by the
    # temperature fallback and the compression ratio check above.
    condition_on_previous_text: bool = True

    # --- audio ------------------------------------------------------------
    sample_rate: int = 16_000
    input_device: int | None = None
    # Peak-normalise quiet input up to this level before decoding.
    target_peak: float = 0.85
    max_gain: float = 8.0
    # Drop the recording if it is shorter than this; almost always a misfire.
    min_seconds: float = 0.25
    max_seconds: float = 300.0

    # --- voice activity ---------------------------------------------------
    vad: bool = True
    # Tuned to keep speech rather than to cut silence tightly. A high threshold
    # or a short pad clips the quiet start and end of words, and a short
    # silence gap splits a sentence mid-clause, both of which read as the app
    # "simplifying" what was said when it is really losing it.
    vad_threshold: float = 0.30
    vad_min_speech_ms: int = 80
    vad_min_silence_ms: int = 700
    vad_speech_pad_ms: int = 400

    # Keep the raw audio of the last dictation, so a bad result can actually be
    # re-run through different settings instead of guessed at. One file, always
    # overwritten, never uploaded.
    keep_last_recording: bool = True

    # --- output -----------------------------------------------------------
    apply_corrections: bool = True
    # Verbatim by default: no filler stripping, no rewriting.
    strip_fillers: bool = False
    trailing_space: bool = True
    # Zero: typing is batched now, so there is no per-character gap to tune.
    # Raise it only if an app cannot keep up with a burst.
    type_delay: float = 0.0
    sound_feedback: bool = True

    # --- floating bar -----------------------------------------------------
    # Always sits at the bottom centre of the screen. There is no saved
    # position on purpose: one predictable place beats a lost bar.
    show_bar: bool = True

    vocabulary: list[str] = field(default_factory=lambda: list(VOCABULARY))
    corrections: dict[str, str] = field(default_factory=lambda: dict(CORRECTIONS))
    casing: list[str] = field(default_factory=lambda: list(CASING))

    @property
    def initial_prompt(self) -> str:
        """DO NOT FEED THIS TO THE DECODER. Kept only for reference.

        This used to be passed as `initial_prompt`, and it silently truncated
        long dictations. Whisper treats the prompt as *prior text*, so a
        hundred comma-separated nouns convince it that it is still writing a
        list, and it stops early at the first sentence boundary.

        Measured on one real 14-second recording, same audio both ways:

            initial_prompt + hotwords ... 12 words
            hotwords only .............. 33 words

        Vocabulary biasing belongs in `hotwords`, which is built for it and
        does not pollute the context. See `transcribe.py`.
        """
        return (
            "The following is clear dictation by an Australian web developer. "
            "It may mention: " + ", ".join(self.vocabulary) + "."
        )

    @classmethod
    def load(cls) -> "Config":
        _migrate_legacy()
        cfg = cls()
        if CONFIG_PATH.is_file():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            for key, value in data.items():
                if not hasattr(cfg, key):
                    continue
                if key == "temperature_fallback":
                    value = tuple(value)
                elif key == "vocabulary":
                    value = list(VOCABULARY) + [v for v in value if v not in VOCABULARY]
                elif key == "casing":
                    value = list(CASING) + [v for v in value if v not in CASING]
                elif key == "corrections":
                    merged = dict(CORRECTIONS)
                    merged.update(value)
                    value = merged
                setattr(cfg, key, value)
        return cfg

    # Only these are written to config.json. Everything else stays a code
    # default, so improving one actually reaches people.
    #
    # The first version of this dumped every field. That pins the tuning
    # values of the day into the file, and every later fix is then silently
    # overridden by a config the user never chose to write. It cost two
    # debugging sessions before anyone noticed.
    TEMPLATE_KEYS = (
        "hotkey", "input_device", "model", "sound_feedback", "trailing_space",
        "apply_corrections", "show_bar", "vad", "keep_history_days",
        "keep_last_recording", "idle_unload_seconds",
    )

    def write_template(self) -> None:
        """Write config.json on first run so the settings are discoverable."""
        if CONFIG_PATH.exists():
            return
        full = asdict(self)
        data = {k: full[k] for k in self.TEMPLATE_KEYS}
        # Emptied in the template: the built-in lists always apply, and what
        # goes here is merged on top of them.
        data["vocabulary"] = []
        data["corrections"] = {}
        data["casing"] = []
        CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
