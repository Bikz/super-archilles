#!/usr/bin/env python
"""Local text-to-speech for the sales-coaching demo.

Usage:
    ~/tts-venv/bin/python ~/copilot/scripts/tts.py "text" out.wav [--voice NAME]

Voices:
    Ming-3.0-tts-0.5B (GPU, best quality, 44.1 kHz resampled to --sr):
        US_James, US_Emma, US_Stella, UK_George, UK_Charlotte
    Kokoro-82M int8 ONNX (CPU fallback, 24 kHz), prefix "kokoro:":
        kokoro:am_michael, kokoro:af_heart, kokoro:bm_george, ... (28 English)

Import:
    from tts import speak          # sys.path must include ~/copilot/scripts
    wav_bytes = speak("Pause. Ask about their current process first.")

Env overrides: TTS_DEVICE (default cuda:0), TTS_SR (default 24000).
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import time

import numpy as np
import soundfile as sf

HOME = os.path.expanduser("~")
MING_REPO = os.path.join(HOME, "tts-models", "Ming-3.0-tts")
MING_WEIGHTS = os.path.join(HOME, "tts-models", "Ming-3.0-tts-0.5b")
KOKORO_DIR = os.path.join(HOME, "tts-models", "kokoro")

MING_VOICES = ("US_James", "US_Emma", "US_Stella", "UK_George", "UK_Charlotte")
DEFAULT_VOICE = "US_James"
DEFAULT_SR = int(os.environ.get("TTS_SR", "24000"))

_ming = None
_kokoro = None


def _load_ming():
    global _ming
    if _ming is None:
        import torch
        import torchaudio

        # torchaudio 2.9 routes load/save through torchcodec, which we do not
        # install. Ming only needs them for reference clips and output_path;
        # route them through soundfile instead.
        def _load(path, *a, **k):
            data, sr = sf.read(path, dtype="float32", always_2d=True)
            return torch.from_numpy(data.T.copy()), sr

        def _save(path, wav, sample_rate, *a, **k):
            sf.write(path, wav.detach().float().cpu().numpy().T, sample_rate)

        torchaudio.load = _load
        torchaudio.save = _save
        if MING_REPO not in sys.path:
            sys.path.insert(0, MING_REPO)
        import warnings

        warnings.filterwarnings("ignore")
        from loguru import logger

        logger.remove()
        from ming_tts import MingTTS

        _ming = MingTTS(
            MING_WEIGHTS,
            device=os.environ.get("TTS_DEVICE", "cuda:0"),
            text_normalization=False,  # needs pynini (conda only); skip
        )
    return _ming


def _load_kokoro():
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro

        _kokoro = Kokoro(
            os.path.join(KOKORO_DIR, "kokoro-v1.0.int8.onnx"),
            os.path.join(KOKORO_DIR, "voices-v1.0.bin"),
        )
    return _kokoro


def _resample(audio: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out:
        return audio
    import torch
    import torchaudio.functional as F

    return F.resample(torch.from_numpy(audio), sr_in, sr_out).numpy()


def synth(text: str, voice: str = DEFAULT_VOICE, sr: int = DEFAULT_SR):
    """Return (float32 mono samples, sample_rate)."""
    if voice.startswith("kokoro:"):
        audio, src_sr = _load_kokoro().create(
            text, voice=voice.split(":", 1)[1], speed=1.0, lang="en-us"
        )
        audio = np.asarray(audio, dtype=np.float32)
    else:
        if voice not in MING_VOICES:
            raise ValueError(f"unknown voice {voice!r}; use one of {MING_VOICES} or kokoro:NAME")
        import torch

        m = _load_ming()
        with torch.inference_mode():
            wav = m.synthesize(
                task="IP", text=text, controls={"ip": voice}, use_zero_spk_emb=True
            )
        audio = wav.detach().float().cpu().numpy().reshape(-1)
        src_sr = m.sample_rate
    audio = _resample(audio, src_sr, sr)
    peak = float(np.abs(audio).max()) if audio.size else 0.0
    if peak > 1e-4:  # Ming output is quiet (peak ~0.2-0.35); normalize to -1 dBFS
        audio = audio * (0.89 / peak)
    return np.clip(audio, -1.0, 1.0).astype(np.float32), sr


def speak(text: str, voice: str = DEFAULT_VOICE, sr: int = DEFAULT_SR) -> bytes:
    """Synthesize text and return a mono 16-bit PCM WAV file as bytes."""
    audio, sr = synth(text, voice, sr)
    buf = io.BytesIO()
    sf.write(buf, audio, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text")
    ap.add_argument("out")
    ap.add_argument("--voice", default=DEFAULT_VOICE)
    ap.add_argument("--sr", type=int, default=DEFAULT_SR, choices=(16000, 24000))
    args = ap.parse_args()
    t0 = time.time()
    data = speak(args.text, args.voice, args.sr)
    with open(args.out, "wb") as f:
        f.write(data)
    print(f"wrote {args.out} ({args.voice}, {args.sr} Hz) in {time.time() - t0:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
