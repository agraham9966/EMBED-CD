"""Guard the import rule that a released version already got wrong.

The plugin must reach the engine through `.engine`, which imports the VENDORED copy sitting
inside the plugin package. A top-level `from embed_me import ...` survives in
sys.modules across a plugin upgrade — QGIS only purges the plugin's own package — so the new
version's UI ends up calling the previous version's engine. In 0.7.1 that shipped as a current
`classify.py` calling a stale `head.py`: "unexpected keyword argument 'pool'", a message that
blames the new code for the old code still being resident.

This is a plain text scan on purpose: it needs no QGIS, so it runs in the normal suite where a
mistake gets caught, rather than in the QGIS-only tests that are easy to skip.
Run: python tests/test_em_plugin_imports.py
"""
import os
import py_compile
import re

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(os.path.dirname(HERE), "plugin", "embed_me_qgis")
TOP_LEVEL = re.compile(r"^\s*(from\s+embed_me(\.|\s+import)|import\s+embed_me\b)",
                       re.MULTILINE)


def test_plugin_reaches_the_engine_only_through_engine_py():
    offenders = []
    for name in sorted(os.listdir(PLUGIN)):
        if not name.endswith(".py") or name == "engine.py":
            continue
        text = open(os.path.join(PLUGIN, name), encoding="utf-8").read()
        for m in TOP_LEVEL.finditer(text):
            line = text[:m.start()].count("\n") + 1
            offenders.append(f"{name}:{line}: {m.group(0).strip()}")
    assert not offenders, (
        "these import the engine top-level, which goes stale across a plugin upgrade — "
        "use `from .engine import ...` instead:\n  " + "\n  ".join(offenders))
    print(f"ok no top-level engine imports in {PLUGIN.split(os.sep)[-1]}")


def test_engine_py_tries_the_vendored_copy_first():
    """Order matters: the relative import has to be the one that wins when both are possible."""
    text = open(os.path.join(PLUGIN, "engine.py"), encoding="utf-8").read()
    rel = text.index("from .embed_me import")
    top = text.index("from embed_me import")
    assert rel < top, "engine.py must try the vendored subpackage BEFORE the top-level fallback"
    assert "except ImportError" in text
    print("ok engine.py prefers the vendored copy, falls back to the repo root for dev mode")


def test_release_vendors_the_engine_next_to_the_plugin():
    """`.engine`'s relative import only resolves if make_release actually puts the engine
    inside the plugin package."""
    script = open(os.path.join(os.path.dirname(HERE), "scripts", "make_release.py"),
                  encoding="utf-8").read()
    assert 'stage_plugin / engine_name' in script, \
        "make_release must copy the engine INTO the staged plugin folder"
    assert '"change": ("embed_me_qgis", "embed_me")' in script
    print("ok make_release vendors the engine inside the plugin package")


def test_every_plugin_file_parses():
    """The suite never imports the UI files — they need Qt — so nothing caught a syntax
    error in dock.py and 8/8 passed with the plugin unloadable. Compiling is enough to
    catch that, and needs no Qt."""
    bad = []
    for name in sorted(os.listdir(PLUGIN)):
        if name.endswith(".py"):
            try:
                py_compile.compile(os.path.join(PLUGIN, name), doraise=True)
            except py_compile.PyCompileError as exc:
                bad.append(f"{name}: {exc.msg.strip().splitlines()[-1]}")
    assert not bad, "plugin files do not parse:\n  " + "\n  ".join(bad)
    print(f"ok all {len([n for n in os.listdir(PLUGIN) if n.endswith('.py')])} plugin files parse")


if __name__ == "__main__":
    test_every_plugin_file_parses()
    test_plugin_reaches_the_engine_only_through_engine_py()
    test_engine_py_tries_the_vendored_copy_first()
    test_release_vendors_the_engine_next_to_the_plugin()
    print("all ok")
