"""Runs the Scene 3D dry-run validator inside headless Chromium (Playwright).

Each beat is checked in its own Web Worker, so an endless loop is cut off after a timeout and the page
keeps working. All network requests from the page are blocked. Playwright is only needed for Scene 3D;
the rest of the app runs without it."""
from pathlib import Path
from typing import Callable, Dict, Optional

CORE_JS = Path(__file__).with_name("v3_core.js")
INSTALL_HINT = "Scene 3D needs Playwright with Chromium. Install once:  pip install playwright  and  playwright install chromium"

_BOOT = """(coreSrc) => {
  const worker = coreSrc + "\\nself.onmessage = (e) => { try { postMessage({ ok: true, result: V3.validate(e.data.code, e.data.name, e.data.opts) }); }"
    + " catch (err) { postMessage({ ok: false, error: String((err && err.message) || err) }); } };";
  const url = URL.createObjectURL(new Blob([worker], { type: 'text/javascript' }));
  window.__v3run = (job, ms) => new Promise((resolve) => {
    const w = new Worker(url);
    const timer = setTimeout(() => { w.terminate(); resolve({ timeout: true }); }, ms);
    w.onmessage = (e) => { clearTimeout(timer); w.terminate(); resolve(e.data); };
    w.onerror = (e) => { clearTimeout(timer); w.terminate(); resolve({ ok: false, error: String(e.message || 'worker error') }); };
    w.postMessage(job);
  });
  return true;
}"""


class Scene3DUnavailable(RuntimeError):
    """Playwright or Chromium is missing."""


class Scene3DRuntime:
    def __init__(self, timeout_s: float = 8.0):
        self.timeout_s = timeout_s
        self._pw = self._browser = self._page = None

    def __enter__(self) -> "Scene3DRuntime":
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as err:
            raise Scene3DUnavailable(INSTALL_HINT) from err
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch()
        except Exception as err:
            self._pw.stop()
            self._pw = None
            raise Scene3DUnavailable(f"{INSTALL_HINT} ({type(err).__name__})") from err
        self._page = self._browser.new_page()
        self._page.set_content("<!doctype html><html><body></body></html>")
        self._page.route("**/*", lambda route: route.abort())          # the page has no network, whatever the code tries
        self._page.evaluate(_BOOT, CORE_JS.read_text(encoding="utf-8").replace("if (typeof module !== 'undefined') module.exports = V3;", ""))
        return self

    def __exit__(self, *exc) -> bool:
        for closer in (lambda: self._browser and self._browser.close(), lambda: self._pw and self._pw.stop()):
            try:
                closer()
            except Exception:
                pass
        self._pw = self._browser = self._page = None
        return False

    def validate(self, name: str, code: str, dur: float, aspect: float = 9 / 16) -> Dict[str, object]:
        """{ok, errors[], warnings[], stats}. Never raises for bad code, only for a missing browser."""
        if self._page is None:
            raise Scene3DUnavailable("the Scene 3D runtime is not started")
        job = {"name": name, "code": code, "opts": {"dur": float(dur), "aspect": float(aspect)}}
        res = self._page.evaluate("([j, ms]) => window.__v3run(j, ms)", [job, int(self.timeout_s * 1000)])
        if res.get("timeout"):
            return {"ok": False, "errors": [f"{name} ran for more than {self.timeout_s:.0f} s (an endless loop or far too much work); simplify it"],
                    "warnings": [], "stats": {}}
        if not res.get("ok"):
            return {"ok": False, "errors": [f"validator error: {res.get('error')}"], "warnings": [], "stats": {}}
        return res["result"]

    def validator(self) -> Callable[[str, str, float], dict]:
        return lambda name, code, dur: self.validate(name, code, dur)
