"""Run the offscreen tests:   python tests/run_all.py [-k substring]

Plain Python, no pytest. Every function named test_* in tests/test_*.py is run;
a failed assert (or any exception) is a FAIL, `harness.Skip` is a SKIP. Needs the
environment of the app (PySide6, pandas, openpyxl) and TEST_Files/CTR creator.
The first test that needs the Generator loads the pricebooks and SAGE (~15 s).
"""
import importlib.util
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import harness  # noqa: E402  (sets up the sandbox first)


def main(argv):
    pattern = argv[argv.index("-k") + 1] if "-k" in argv else ""
    passed = failed = skipped = 0
    for path in sorted(HERE.glob("test_*.py")):
        spec = importlib.util.spec_from_file_location(path.stem, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for name in sorted(n for n in dir(mod) if n.startswith("test_")):
            if pattern and pattern not in f"{path.stem}.{name}":
                continue
            t0 = time.time()
            try:
                getattr(mod, name)()
                status = "PASS"
                passed += 1
            except harness.Skip as exc:
                status = f"SKIP ({exc})"
                skipped += 1
            except BaseException:
                status = "FAIL"
                failed += 1
                traceback.print_exc()
            print(f"{status:<6} {path.stem}.{name}  [{time.time() - t0:.1f}s]", flush=True)
    print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
