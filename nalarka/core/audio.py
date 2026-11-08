"""Audio duration probing and Faster-Whisper word-level timestamp alignment."""
import time
import subprocess
from typing import List, Optional


def get_audio_duration(audio_path: str, log_fn) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", audio_path]
    dur = float(subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT).strip())
    log_fn(f"[FFPROBE] Exact voiceover duration: {dur:.3f}s")
    return dur


def extract_word_timestamps(audio_path: str, whisper_size: str, log_fn, language: Optional[str] = None) -> List[dict]:
    """`language` ('id'/'en') skips Whisper's auto-detect to guarantee accurate alignment."""
    from faster_whisper import WhisperModel
    log_fn(f"[WHISPER] Loading Faster-Whisper model ('{whisper_size}', int8 CPU, language={language or 'auto'})...")
    t0 = time.time()
    whisper_model = WhisperModel(whisper_size, device="cpu", compute_type="int8")
    segments, _ = whisper_model.transcribe(audio_path, word_timestamps=True, language=language)

    words = []
    for seg in segments:
        if seg.words:
            for w in seg.words:
                words.append({
                    "word": w.word.strip(),
                    "start": round(w.start + 1.0, 3),
                    "end": round(w.end + 1.0, 3)
                })
    log_fn(f"[WHISPER] Extracted {len(words)} word-level timestamps in {round(time.time() - t0, 2)}s.")
    return words
