# MR vs CTR Comparator — task runner
# Install: https://just.systems

# `just` runs recipes through `sh` by default, which native Windows (as
# opposed to WSL) doesn't have on PATH — needed for `build`/`installer`,
# which must run on Windows since PyInstaller/Inno Setup target the OS
# they run on. Route Windows recipe execution through PowerShell instead.
set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

# Run the PySide6 desktop app
run:
    uv run python app.py

# Run the legacy Streamlit app (kept for reference only — use `just run` instead)
legacy:
    uv run streamlit run legacy_streamlit.py

# Set up the dev environment (installs all deps including dev extras)
setup:
    uv sync --all-extras

# Build the standalone .exe  ── run this from Windows (not WSL)
# Deliberately doesn't go through `uv run` — this folder's .venv was created
# by `uv sync` on the WSL/Linux side (for `just run`/`just setup`), and it
# contains a Unix symlink (lib64 -> lib) that uv on native Windows can't
# rebuild over the \\wsl$ UNC path. Requires PySide6/pandas/openpyxl/
# pyinstaller installed globally on Windows: `pip install PySide6 pandas
# openpyxl pyinstaller`.
build:
    python -m PyInstaller app.spec

# Build the installer .exe after building the app.
# Inno Setup's installer doesn't reliably add ISCC.exe to PATH, so try PATH
# first, then fall back to its standard default install location.
installer: build
    if (Get-Command iscc -ErrorAction SilentlyContinue) { iscc setup.iss } else { & "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" setup.iss }

# Remove build artefacts
clean:
    rm -rf build dist __pycache__ .pytest_cache Output
