#!/usr/bin/env python3
"""Turn a plain TTS WAV into Johnny's synthetic-robot voice.

A small, dependency-light DSP chain (numpy + stdlib `wave` only) applied as a
post-process on top of any base TTS (we use Kokoro). The goal is an eager,
metallic, lightly-digital robot character.

Usage:
    python3 johnnify.py in.wav out.wav [--preset subtle|johnny|heavy]
"""
from __future__ import annotations
import argparse
import wave
import numpy as np


def read_wav(path: str) -> tuple[np.ndarray, int]:
    with wave.open(path, "rb") as w:
        ch, sw, sr, nf = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(nf)
    if sw != 2:
        raise ValueError(f"expected 16-bit PCM, got sampwidth={sw}")
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch == 2:
        x = x.reshape(-1, 2).mean(axis=1)
    return x, sr


def write_wav(path: str, x: np.ndarray, sr: int) -> None:
    x = np.clip(x, -1.0, 1.0)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((x * 32767.0).astype(np.int16).tobytes())


def resample(x: np.ndarray, factor: float) -> np.ndarray:
    """factor > 1 → higher pitch + slightly faster (gives an eager, youthful read)."""
    n = max(1, int(len(x) / factor))
    idx = np.linspace(0, len(x) - 1, n)
    return np.interp(idx, np.arange(len(x)), x).astype(np.float32)


def ring_mod(x: np.ndarray, sr: int, fc: float, mix: float) -> np.ndarray:
    """Amplitude-modulate by a carrier → the metallic/electronic timbre."""
    t = np.arange(len(x)) / sr
    carrier = np.sin(2 * np.pi * fc * t).astype(np.float32)
    return (1.0 - mix) * x + mix * (x * carrier)


def comb(x: np.ndarray, sr: int, delay_ms: float, fb: float) -> np.ndarray:
    """Short feedback comb → mechanical resonance ('servo' colour)."""
    d = max(1, int(sr * delay_ms / 1000.0))
    y = x.copy()
    for i in range(d, len(x)):
        y[i] += fb * y[i - d]
    return y


def bitcrush(x: np.ndarray, bits: int, hold: int) -> np.ndarray:
    """Quantise amplitude (bits) + sample-and-hold (hold) → digital grit."""
    levels = float(2 ** bits)
    xq = np.round(x * levels) / levels
    if hold > 1:
        idx = (np.arange(len(xq)) // hold) * hold
        xq = xq[np.clip(idx, 0, len(xq) - 1)]
    return xq


def chorus(x: np.ndarray, sr: int, depth_ms: float, rate: float, mix: float) -> np.ndarray:
    """Detuned modulated delay → a stacked, synthetic feel."""
    t = np.arange(len(x))
    mod = (depth_ms / 1000.0 * sr) * (0.5 + 0.5 * np.sin(2 * np.pi * rate * t / sr))
    y = np.interp(t - mod, t, x).astype(np.float32)
    return (1.0 - mix) * x + mix * y


def normalize(x: np.ndarray, peak: float = 0.95) -> np.ndarray:
    m = float(np.max(np.abs(x)))
    return x * (peak / m) if m > 0 else x


PRESETS = {
    # pitch, ring(fc,mix), comb(ms,fb), bitcrush(bits,hold), chorus(depth,rate,mix)
    "subtle": dict(pitch=1.03, ring=(70, 0.10), comb=(3.0, 0.15), crush=(8, 1), chorus=(1.5, 1.0, 0.18)),
    "johnny": dict(pitch=1.06, ring=(78, 0.18), comb=(4.0, 0.25), crush=(7, 2), chorus=(2.0, 1.2, 0.25)),
    "heavy":  dict(pitch=1.09, ring=(95, 0.30), comb=(5.5, 0.35), crush=(6, 3), chorus=(2.5, 1.6, 0.32)),
}


def johnnify(x: np.ndarray, sr: int, preset: str = "johnny") -> np.ndarray:
    p = PRESETS[preset]
    x = resample(x, p["pitch"])
    x = ring_mod(x, sr, p["ring"][0], p["ring"][1])
    x = comb(x, sr, p["comb"][0], p["comb"][1])
    x = bitcrush(x, p["crush"][0], p["crush"][1])
    x = chorus(x, sr, p["chorus"][0], p["chorus"][1], p["chorus"][2])
    return normalize(x)


def main() -> None:
    ap = argparse.ArgumentParser(description="Apply Johnny's robot-voice DSP to a WAV.")
    ap.add_argument("infile")
    ap.add_argument("outfile")
    ap.add_argument("--preset", choices=list(PRESETS), default="johnny")
    a = ap.parse_args()
    x, sr = read_wav(a.infile)
    write_wav(a.outfile, johnnify(x, sr, a.preset), sr)
    print(f"wrote {a.outfile}  (preset={a.preset}, {len(x)} samples @ {sr} Hz)")


if __name__ == "__main__":
    main()
