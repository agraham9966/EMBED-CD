r"""Run the whole suite under QGIS's own Python.

The engine uses osgeo.gdal now, which lives in QGIS and not in the dev venv — so QGIS's
interpreter is the only place the tests can run, and conveniently it is also the interpreter
the plugin actually runs under. One environment, no divergence.

    "C:\Program Files\QGIS 4.0.1\bin\python-qgis.bat" run_tests.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PYTHONPATH"] = HERE + os.pathsep + os.environ.get("PYTHONPATH", "")

# AEF_LIVE hits the real bucket. Off by default (network), but the port proved why it matters:
# every offline fixture is a local file, so nothing but a live read can check URL handling.
tests = sorted(f for f in os.listdir(os.path.join(HERE, "tests"))
               if f.startswith("test_ae_") and f.endswith(".py"))
if len(sys.argv) > 1:
    tests = [t for t in tests if any(a in t for a in sys.argv[1:])]

failed = []
for t in tests:
    r = subprocess.run([sys.executable, os.path.join(HERE, "tests", t)],
                       capture_output=True, text=True, cwd=HERE)
    tail = [ln for ln in (r.stdout or "").strip().splitlines() if ln.strip()]
    print(f"{t:28s} {'PASS' if r.returncode == 0 else 'FAIL'}   {tail[-1][:60] if tail else ''}")
    if r.returncode != 0:
        failed.append(t)
        print((r.stdout or "")[-1500:])
        print((r.stderr or "")[-1500:])
print(f"\n{len(tests) - len(failed)}/{len(tests)} suites passed")
sys.exit(1 if failed else 0)
