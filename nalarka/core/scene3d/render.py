"""Scene 3D renderer: draws beat functions with three.js in headless Chromium and encodes the frames with ffmpeg.

three.js (r149) is downloaded once into a cache folder and injected into the page; the page itself has no network."""
import base64
import shutil
import subprocess
import urllib.request
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Sequence, Tuple, Union

from .runtime import CORE_JS, INSTALL_HINT, Scene3DUnavailable

RENDERER_JS = Path(__file__).with_name("renderer.js")
THREE_VERSION = "0.149.0"
THREE_URL = f"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/build/three.min.js"
CHROMIUM_ARGS = ["--ignore-gpu-blocklist", "--enable-unsafe-swiftshader"]

Beat = Tuple[str, Optional[str], float, float]          # (function name, code, start s, end s)


def ensure_three(cache_dir: Union[str, Path], url: str = THREE_URL, log: Callable[[str], None] = lambda s: None) -> str:
    """The three.js source text, downloaded once and cached."""
    path = Path(cache_dir) / f"three-{THREE_VERSION}.min.js"
    if path.exists():
        return path.read_text(encoding="utf-8")
    log(f"Downloading three.js {THREE_VERSION} once from {url} ...")
    try:
        with urllib.request.urlopen(url, timeout=30) as r:          # noqa: S310 - pinned https URL, cached afterwards
            text = r.read().decode("utf-8")
    except Exception as err:
        raise Scene3DUnavailable(f"could not download three.js ({type(err).__name__}: {err}). Download {url} and save it as {path}") from err
    if "REVISION" not in text:
        raise Scene3DUnavailable("the downloaded file does not look like three.js")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


TRANSITION_S = 0.4        # cross-fade length at the start of every shot except the first


def frame_plan(beats: Sequence[Beat], fps: int, total_frames: int, transition_s: float = TRANSITION_S
               ) -> List[Tuple[int, float, Optional[float], float]]:
    """For every global frame: (beat index, time since that beat started, time of the previous beat or None, fade 0..1).
    The previous beat keeps animating during the fade, so nothing freezes. The last beat holds to the end."""
    out: List[Tuple[int, float, Optional[float], float]] = []
    bi = 0
    for f in range(total_frames):
        now = f / fps
        while bi + 1 < len(beats) and now >= beats[bi + 1][2] - 1e-9:
            bi += 1
        start, end = beats[bi][2], beats[bi][3]
        t = max(0.0, min(now - start, max(0.0, end - start - 1.0 / fps)))
        age = now - start
        if bi > 0 and transition_s > 0 and age < transition_s:
            x = max(0.0, min(1.0, age / transition_s))
            out.append((bi, t, now - beats[bi - 1][2], x * x * (3 - 2 * x)))      # smoothstep
        else:
            out.append((bi, t, None, 1.0))
    return out


class Scene3DRenderer:
    def __init__(self, three_js: str, size: Tuple[int, int] = (1080, 1920), quality: float = 0.92):
        self.three_js, self.size, self.quality = three_js, size, quality
        self._pw = self._browser = self._page = None

    def __enter__(self) -> "Scene3DRenderer":
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as err:
            raise Scene3DUnavailable(INSTALL_HINT) from err
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch(args=CHROMIUM_ARGS)
        except Exception as err:
            self._pw.stop()
            self._pw = None
            raise Scene3DUnavailable(f"{INSTALL_HINT} ({type(err).__name__})") from err
        w, h = self.size
        self._page = self._browser.new_page(viewport={"width": w, "height": h})
        self._page.set_content("<!doctype html><html><body style='margin:0'></body></html>")
        self._errors: List[str] = []
        self._page.on("pageerror", lambda e: self._errors.append(str(e)))
        self._page.route("**/*", lambda route: route.abort())
        self._page.add_script_tag(content=self.three_js)
        self._page.add_script_tag(content=CORE_JS.read_text(encoding="utf-8").replace("if (typeof module !== 'undefined') module.exports = V3;", ""))
        self._page.add_script_tag(content=RENDERER_JS.read_text(encoding="utf-8"))
        ok = self._page.evaluate("([w, h]) => { try { window.__r3 = R3.create(THREE, { width: w, height: h }); return ''; } catch (e) { return String(e && e.message || e); } }", [w, h])
        if ok:
            self.__exit__()
            raise Scene3DUnavailable(f"Chromium could not start WebGL here: {ok}")
        return self

    def __exit__(self, *exc) -> bool:
        for closer in (lambda: self._browser and self._browser.close(), lambda: self._pw and self._pw.stop()):
            try:
                closer()
            except Exception:
                pass
        self._pw = self._browser = self._page = None
        return False

    def load_beat(self, name: str, code: str, dur: float, keep_prev: bool = False) -> dict:
        res = self._page.evaluate("([c, n, d, k]) => { try { return { ok: true, info: window.__r3.loadBeat(c, n, d, k) }; } catch (e) { return { ok: false, error: String(e && e.message || e) }; } }", [code, name, float(dur), bool(keep_prev)])
        if not res["ok"]:
            raise ValueError(res["error"])
        return res["info"]

    def frame_jpeg(self, t: float) -> bytes:
        b64 = self._page.evaluate("(t) => { window.__r3.frame(t); return window.__r3.jpeg(%s); }" % self.quality, float(t))
        return base64.b64decode(b64)

    def frame_mix_jpeg(self, t_prev: float, t: float, mix: float) -> bytes:
        b64 = self._page.evaluate("([a, b, m]) => { window.__r3.frameMix(a, b, m); return window.__r3.jpeg(%s); }" % self.quality, [float(t_prev), float(t), float(mix)])
        return base64.b64decode(b64)

    def info(self) -> dict:
        return self._page.evaluate("() => window.__r3.info()")

    def iter_frames(self, beats: Sequence[Beat], fps: int, total_frames: int,
                    should_stop: Optional[Callable[[], bool]] = None, transition_s: float = TRANSITION_S) -> Iterator[bytes]:
        missing = [b[0] for b in beats if not b[1]]
        if missing:
            raise ValueError("these beats have no 3D code yet: " + ", ".join(missing))
        loaded, fading = -1, False
        for f, (bi, t, t_prev, mix) in enumerate(frame_plan(beats, fps, total_frames, transition_s)):
            if should_stop is not None and should_stop():
                raise InterruptedError("3D render cancelled.")
            if bi != loaded:
                name, code, start, end = beats[bi]
                self.load_beat(name, code, max(end - start, 1.0 / fps), keep_prev=bi > 0 and transition_s > 0)
                loaded, fading = bi, bi > 0 and transition_s > 0
            if t_prev is not None:
                yield self.frame_mix_jpeg(t_prev, t, mix)
            else:
                if fading:
                    self._page.evaluate("() => window.__r3.dropPrev()")
                    fading = False
                yield self.frame_jpeg(t)


def encode_silent_video(frames: Iterator[bytes], out_path: Union[str, Path], fps: int, size: Tuple[int, int],
                        ffmpeg: str = "ffmpeg", crf: int = 18) -> int:
    """Pipes JPEG frames into ffmpeg (H.264, yuv420p, constant frame rate). Returns the number of frames written."""
    exe = shutil.which(ffmpeg)
    if not exe:
        raise Scene3DUnavailable("ffmpeg was not found on PATH")
    w, h = size
    cmd = [exe, "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(fps), "-c:v", "mjpeg", "-i", "-",
           "-vf", f"scale={w}:{h}:flags=lanczos:in_range=pc:out_range=tv,format=yuv420p", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-color_range", "tv",
           "-preset", "veryfast", "-crf", str(crf),
           "-r", str(fps), "-fps_mode", "cfr", "-movflags", "+faststart", str(out_path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    n = 0
    try:
        for jpg in frames:
            proc.stdin.write(jpg)
            n += 1
        proc.stdin.close()
    except BaseException:
        proc.kill()
        for stream in (proc.stdin, proc.stderr):
            try:
                stream.close()
            except Exception:
                pass
        proc.wait()
        raise
    err = proc.stderr.read().decode("utf-8", "replace")
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg failed: {err[-400:]}")
    return n


def encode_with_audio(frames: Iterator[bytes], out_path: Union[str, Path], fps: int, size: Tuple[int, int], total_dur: int,
                      vo_path: str, bgm_path: Optional[str] = None, log_path: Optional[Union[str, Path]] = None,
                      on_frame: Optional[Callable[[int], None]] = None, crf: int = 18) -> int:
    """JPEG frames + voiceover (+ music) -> final MP4 with the same audio mix as the flat video type.
    Exactly total_dur * fps frames are expected; the container is cut at total_dur seconds."""
    import os
    from ..render import build_audio_filter
    exe = shutil.which("ffmpeg")
    if not exe:
        raise Scene3DUnavailable("ffmpeg was not found on PATH")
    w, h = size
    total_frames = total_dur * fps
    has_bgm = bool(bgm_path and os.path.exists(bgm_path))
    cmd = [exe, "-y", "-f", "image2pipe", "-framerate", str(fps), "-c:v", "mjpeg", "-i", "-", "-i", str(vo_path)]
    if has_bgm:
        cmd += ["-stream_loop", "-1", "-i", str(bgm_path)]
    cmd += ["-filter_complex", f"[0:v]scale={w}:{h}:flags=lanczos:in_range=pc:out_range=tv,format=yuv420p[vout];" + build_audio_filter(total_dur, has_bgm),
            "-map", "[vout]", "-map", "[aout]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-color_range", "tv", "-preset", "medium" if total_dur <= 90 else "veryfast", "-crf", str(crf),
            "-r", str(fps), "-fps_mode", "cfr", "-frames:v", str(total_frames),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", str(total_dur), "-movflags", "+faststart", str(out_path)]
    log_f = open(log_path, "w", encoding="utf-8") if log_path else subprocess.DEVNULL
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=log_f)
    n = 0
    try:
        for jpg in frames:
            proc.stdin.write(jpg)
            n += 1
            if on_frame is not None:
                on_frame(n)
        proc.stdin.close()
    except BaseException:
        proc.kill()
        try:
            proc.stdin.close()
        except Exception:
            pass
        proc.wait()
        raise
    finally:
        if log_path:
            log_f.close()
    if proc.wait() != 0:
        tail = ""
        if log_path:
            try:
                tail = Path(log_path).read_text(encoding="utf-8", errors="replace")[-400:]
            except OSError:
                pass
        raise RuntimeError(f"ffmpeg failed while encoding the 3D video. {tail}")
    return n
