"""Synthetic ambient BGM loop generator."""
import wave
import numpy as np

BGM_LOOP_SECONDS = 32


def generate_synthetic_ambient_bgm(output_wav: str, duration_sec: int, bpm: int, log_fn) -> str:
    """
    Writes a seamless 32s ambient loop (every component completes whole cycles in the loop length).
    The render pipeline loops it with `-stream_loop -1`, so memory use no longer grows with video length.
    `duration_sec` is kept for API compatibility.
    """
    sr = 48000
    L = BGM_LOOP_SECONDS
    t = np.arange(sr * L, dtype=np.float64) / sr

    def q(f: float) -> float:  # snap to a whole number of cycles per loop
        return max(1.0 / L, round(f * L) / L)

    freqs = [110.0, 164.81, 220.0, 261.63, 329.63, 392.00]
    amps = [0.22, 0.16, 0.18, 0.14, 0.10, 0.08]
    signal_l = np.zeros_like(t)
    signal_r = np.zeros_like(t)

    for idx, (f, a) in enumerate(zip(freqs, amps)):
        mod = 1.0 + 0.15 * np.sin(2.0 * np.pi * q(0.12 + idx * 0.04) * t)
        detune = 1.002 if idx % 2 == 0 else 0.998
        signal_l += a * mod * np.sin(2.0 * np.pi * q(f) * t)
        signal_r += a * mod * np.sin(2.0 * np.pi * q(f * detune) * t + 0.4)

    beat_freq = q(bpm / 60.0)
    beat_env = np.power(0.5 * (1.0 + np.sin(2.0 * np.pi * beat_freq * t - np.pi / 2.0)), 4.0)
    pulse = 0.15 * beat_env * np.sin(2.0 * np.pi * q(82.41) * t)
    signal_l += pulse
    signal_r += pulse

    peak = max(np.max(np.abs(signal_l)), np.max(np.abs(signal_r)), 1e-6)
    stereo = np.column_stack(((signal_l / peak) * 0.45, (signal_r / peak) * 0.45))
    pcm16 = np.int16(np.clip(stereo * 32767, -32768, 32767))

    with wave.open(output_wav, "w") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm16.tobytes())
    log_fn(f"[BGM] Seamless {L}s ambient loop written to {output_wav} (looped to video length by ffmpeg).")
    return output_wav
