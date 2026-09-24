# How Sayso works

Every section here exists because something broke in a way that looked like
something else. If you are changing this code, the warnings are the valuable
part.

## The pieces

| File | Job |
|---|---|
| `cuda_paths.py` | Puts the pip-installed cuBLAS/cuDNN DLLs on the search path. Must import before `faster_whisper`. |
| `config.py` | Defaults, the vocabulary, the corrections, and the `config.json` merge. |
| `audio.py` | Microphone capture at 16 kHz mono, peak normalisation, live level. |
| `transcribe.py` | Whisper wrapper, lazy load, idle unload. |
| `postprocess.py` | Name spelling only. Never grammar, order or phrasing. |
| `typer.py` | `SendInput` unicode typing. Never touches the clipboard. |
| `focus.py` | Remembers the window you were typing in, and restores it. |
| `hotkey.py` | The global dictation key. |
| `widget.py` | The floating bar, drawn as a layered window. |
| `history.py` | Dictations kept on disk as JSON Lines. |
| `single.py` | One copy at a time, and the start-with-Windows entry. |
| `app.py` | State machine: one dictation at a time, on a worker thread. |
| `gui.py` | The window. |
| `glass.py`, `theme.py` | The frosted backdrop and panels, and the styling. |
| `selftest.py` | Synthesises speech, scores the word error rate, tests typing. |

## Accuracy is a stack, not a setting

Removing any of these costs real accuracy:

- **`large-v3`**, float16. `large-v3-turbo` is ~4x faster and about a point
  worse.
- **Beam search** (`beam_size=5`, `best_of=5`) rather than greedy decoding.
- **Temperature fallback** 0.0 → 1.0: a pass that looks like a hallucination,
  judged by compression ratio and log probability, is retried hotter.
- **`condition_on_previous_text=True`**. Whisper works in 30-second windows,
  and without the previous text it restarts cold at every boundary, which
  wrecks grammar across the joins. The loop this can cause is caught by the
  temperature fallback and the compression ratio check.
- **A forced language**, so a short "yep" is never detected as Welsh.
- **Silero VAD** trims silence. This is what stops Whisper inventing text
  during dead air.
- **Peak normalisation** before decoding, capped at 8x. A desk mic at arm's
  length records well below full scale and Whisper is measurably worse on
  quiet audio.
- **Vocabulary biasing** through `hotwords` only, never `initial_prompt`. The
  single biggest win on domain-specific speech, and the prompt route silently
  truncates long dictations: see the trap below.

## The traps

### A key binding can fail in complete silence

**Right Alt is AltGr on most layouts**, and pynput reports it as `alt_gr`
there and `alt_r` elsewhere. Bind one, receive the other, and the listener
starts, the key gets pressed, and *nothing happens* — no error, no log line.
`_SAME_KEY` in `hotkey.py` is what stops that, and it is why `_matches`
compares against a set rather than a single key.

Windows also auto-repeats a held key, and a repeat arriving just after a
dictation ends starts a phantom one. `_REPEAT_GUARD_SECONDS` handles it.

### The floating bar steals focus no matter what you do

`WS_EX_NOACTIVATE` gets most of the way, but **it is not enough**: Tk activates
its own toplevel on a button press whatever the style says. So `focus.py`
polls for the foreground window, remembers the last one that is not ours, and
`app._handle` restores it immediately before typing. Remove that call and
dictation via the bar breaks completely and silently.

Two details in there that look optional and are not:

- `restore()` releases Alt before `SetForegroundWindow`. Windows refuses that
  call from a process that does not already own the foreground, and releasing
  a modifier is the documented way to earn the right.
- `_no_activate()` uses `GetAncestor(hwnd, GA_ROOT)`, **not** `GetParent`.
  `GetParent` on a Tk toplevel can hand back the main window, and marking that
  no-activate stops the main window taking focus at all.

### Colour keying cannot anti-alias

The bar was first drawn with `-transparentcolor`, and it looked blurry.
Anti-aliased edge pixels blend toward black and then have to be forced opaque,
which rings the shape with a dark halo.

It is now a layered window: each frame is rendered with PIL at 3x, shrunk, and
handed to `UpdateLayeredWindow` as **premultiplied** BGRA. Anything else
fringes.

**Declare your GDI prototypes.** Without them ctypes assumes every argument is
a C int, the 64-bit `HBITMAP` is truncated, `SelectObject` fails with "int too
long to convert", and the window silently never paints while everything else
reports success.

### cuDNN and cuBLAS are not on PATH

They ship inside the `nvidia-*` wheels and live in site-packages.
`cuda_paths.register()` has to run *before* `faster_whisper` is imported,
which is why `transcribe.py` has an import below a function call with a
`noqa` on it. It handles both shapes: site-packages in a checkout,
`sys._MEIPASS` and the executable's folder when frozen.

### A windowed PyInstaller build has no stdout

`sys.stdout` is `None`, so the first `print()` anywhere kills the app on
launch. `capture_output()` points both streams at a file before argparse runs,
because argparse writes to stderr too.

PyInstaller also runs its entry file as a loose top-level script, so aiming it
at `sayso/__main__.py` breaks every relative import in the tree. That is what
`Sayso.py` is for.

### Settings must not live beside the executable

`build.cmd` deletes `dist\` outright, and a frozen app's `__file__` points
into a temporary unpack directory. Both would lose your settings. Everything
writable is in `%LOCALAPPDATA%\Sayso`, shared by the exe and the command line
so a key rebound in the window is the key the CLI uses.

## Testing it

`--self-test` synthesises speech with the Windows voice, transcribes it,
scores the word error rate, and types a line of punctuation and symbols into a
real focused text box. Synthetic speech is harder for Whisper than a real
person, so treat the rate it reports as a floor rather than a prediction.

**A word of warning about writing tests for this.** Three separate test
harnesses produced confident false failures while this was built:

- `SendInput` returns 0 and sets no obvious error when Windows refuses the
  injection. Always check the return value.
- A hand-rolled `INPUT` struct failed with error 87 while the app's own
  struct worked fine. Import the real ones.
- Windows 11 Notepad's window belongs to a different process than the one you
  launch, so looking it up by PID finds nothing.

Print the return values and the window handles before believing a negative
result.

## The glass

Tk has no blur, no gradients, and no alpha between widgets, so the look is not
widget styling. The whole backdrop is composited in PIL and pushed to a canvas
as one image, with text and bars drawn as canvas items on top where Tk still
renders fonts crisply.

**The blur is free.** The aurora is drawn at about a tenth scale and upscaled
with LANCZOS. That produces a soft gradient far faster than blurring at full
size, and is the only reason it can animate: a full frame costs about 45 ms, so
it drifts on its own slower clock while the numbers and the level meter run at
30 fps.

**One aurora for the whole window**, cropped per canvas, so the colour runs
continuously from the rail into the content instead of stopping at the seam.
A scrolled canvas moves its items but not the window, so panels are frosted
where they currently appear rather than where they were placed.

### Translucent panels and opaque widgets do not mix

A frosted panel lets the drifting aurora through, so no fixed colour can match
it. Any widget placed on one therefore reads as a grey chip floating on top,
because Tk cannot make a widget translucent.

So there are two kinds of panel:

- **Home** is fully translucent. Nothing opaque sits on it: the hero text, the
  stat numbers and the meter are all canvas items.
- **Settings** uses flat panels with a glass rim (`frost(..., solid=...)`) and
  tints every widget to exactly that colour. The glass still reads from the
  backdrop showing between the panels.

`theme.GLASS` and `theme.GLASS_RGB` must stay identical, and that is the whole
reason they exist.

### Colour is easy to overdo

The first pass blended the blobs at 0.5 and looked like a light theme. The
blobs are deep and desaturated, and blended at 0.28, because the frosted
panels lighten everything again on top.
