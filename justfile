# MR vs CTR Comparator — task runner
# Install: https://just.systems

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
build:
    uv run pyinstaller app.spec

# Build the installer .exe after building the app  ── requires Inno Setup on PATH
installer: build
    iscc setup.iss

# Remove build artefacts
clean:
    rm -rf build dist __pycache__ .pytest_cache Output
