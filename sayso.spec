# PyInstaller spec for Sayso.
#
# Built as a folder rather than a single file on purpose: the CUDA libraries
# are well over a gigabyte, and a one-file exe unpacks all of that into a temp
# directory on every single launch. A folder starts instantly.
#
#   build.cmd     writes dist\Sayso\Sayso.exe

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

# ctranslate2 links cuBLAS and cuDNN at load time, and those DLLs live inside
# the nvidia-* wheels rather than anywhere on PATH. cuda_paths.register() adds
# them back at runtime; this puts them in the build in the first place.
binaries = []
for package in ("ctranslate2", "nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_nvrtc"):
    binaries += collect_dynamic_libs(package)

# faster_whisper ships the Silero voice-activity model as package data. Without
# it, silence trimming fails at the first dictation rather than at build time.
datas = collect_data_files("faster_whisper")
datas += collect_data_files("onnxruntime")

hiddenimports = [
    "pystray._win32",          # chosen at runtime, so PyInstaller cannot see it
    "PIL._tkinter_finder",
    "onnxruntime",
    "tokenizers",
    "sounddevice",
    "pynput.keyboard._win32",
    "pynput.mouse._win32",
]

a = Analysis(
    # Sayso.py, not sayso/__main__.py: PyInstaller runs its entry file as
    # a loose top-level script, so aiming it at the module inside the package
    # breaks every relative import in the tree.
    ["Sayso.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Nothing here is used: torch is not a dependency (ctranslate2 replaces it),
    # and the rest come in via transitive imports we never call.
    excludes=["torch", "torchvision", "matplotlib", "scipy", "pandas",
              "pytest", "IPython", "notebook"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Sayso",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # no terminal window behind the app
    icon="build_tools/sayso.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Sayso",
)
