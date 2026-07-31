from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
from datetime import datetime, timezone
from typing import Any

SUB_BANDS: tuple[tuple[str, float, float], ...] = (
    ("infrasonic_pressure", 20.0, 35.0),
    ("deep_fundamental", 35.0, 60.0),
    ("primary_sub", 60.0, 105.0),
    ("upper_bass", 105.0, 160.0),
    ("kick_low_mid_body", 160.0, 250.0),
)

PITCH_CLASSES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

NOT_MEASURED = {
    "emotion": "interpretation is not an acoustic measurement",
    "genre": "genre is a cultural category, not a signal fact",
    "lyrics": "v0 does not transcribe or align lyrics",
    "source_identity": "v0 does not assert kick, bass, reese, or voice identity without separation evidence",
    "certain_chords": "chroma emphasis is measured; chord naming remains probabilistic",
    "mastering_approval": "technical measurements are not a substitute for multi-system listening",
}


def _deps() -> tuple[Any, Any, Any, Any]:
    try:
        import librosa
        import numpy as np
        import soundfile as sf
        from scipy import signal
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise RuntimeError(
            "Gl!tch Ears needs the optional audio dependencies. "
            "Install with: pip install -e '.[ears]'"
        ) from exc
    return librosa, np, sf, signal


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def observation(
    name: str,
    value: Any,
    *,
    unit: str | None,
    method: str,
    confidence: float,
    status: str = "measured",
    start_s: float | None = None,
    end_s: float | None = None,
    caveat: str | None = None,
) -> dict[str, Any]:
    if status not in {"measured", "inferred", "relational"}:
        raise ValueError(f"unsupported evidence status: {status}")
    item: dict[str, Any] = {
        "name": name,
        "value": value,
        "unit": unit,
        "method": method,
        "confidence": round(float(max(0.0, min(1.0, confidence))), 3),
        "status": status,
    }
    if start_s is not None:
        item["start_s"] = round(float(start_s), 3)
    if end_s is not None:
        item["end_s"] = round(float(end_s), 3)
    if caveat:
        item["caveat"] = caveat
    return item


def frequency_to_note(freq_hz: float, a4_hz: float = 435.0) -> dict[str, Any] | None:
    if freq_hz <= 0 or a4_hz <= 0:
        return None
    midi_float = 69.0 + 12.0 * math.log2(freq_hz / a4_hz)
    midi = int(round(midi_float))
    cents = (midi_float - midi) * 100.0
    return {
        "note": f"{PITCH_CLASSES[midi % 12]}{midi // 12 - 1}",
        "midi": midi,
        "cents": round(cents, 1),
        "a4_hz": a4_hz,
    }


def _corrcoef(a: Any, b: Any, np: Any) -> float | None:
    if len(a) < 2 or len(b) < 2:
        return None
    if float(np.std(a)) < 1e-12 or float(np.std(b)) < 1e-12:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def _safe_lowpass(x: Any, cutoff_hz: float, sr: int, signal: Any) -> Any:
    if cutoff_hz >= sr / 2:
        return x
    sos = signal.butter(4, cutoff_hz, btype="lowpass", fs=sr, output="sos")
    try:
        return signal.sosfiltfilt(sos, x)
    except ValueError:
        return signal.sosfilt(sos, x)


def _candidate_sections(feature_matrix: Any, frame_rate: float, np: Any, signal: Any) -> tuple[list[float], float]:
    if feature_matrix.shape[1] < max(16, int(frame_rate * 8)):
        return [], 0.0

    z = feature_matrix.astype(float)
    z = (z - np.median(z, axis=1, keepdims=True)) / (np.std(z, axis=1, keepdims=True) + 1e-9)
    context = max(2, int(round(frame_rate * 2.0)))
    novelty = np.zeros(z.shape[1])
    for index in range(context, z.shape[1] - context):
        before = z[:, index - context:index].mean(axis=1)
        after = z[:, index:index + context].mean(axis=1)
        novelty[index] = np.linalg.norm(after - before)

    body = novelty[context:-context]
    if len(body) == 0 or float(body.max()) <= 0:
        return [], 0.0

    prominence = max(float(np.median(body) + np.std(body)), 0.75)
    distance = max(1, int(round(frame_rate * 5.0)))
    peaks, properties = signal.find_peaks(novelty, prominence=prominence, distance=distance)
    if len(peaks) == 0:
        return [], 0.0

    strengths = properties.get("prominences", np.ones(len(peaks)))
    order = np.argsort(strengths)[::-1][:12]
    selected = sorted(int(peaks[i]) for i in order)
    confidence = min(1.0, float(np.median(strengths[order])) / (prominence * 3.0 + 1e-9))
    return [round(index / frame_rate, 2) for index in selected], confidence


def _modulation_candidate(envelope: Any, frame_rate: float, np: Any, signal: Any) -> dict[str, Any]:
    if len(envelope) < max(32, int(frame_rate * 8)):
        return {"supported": False, "reason": "not enough low-band time-body"}
    x = np.log1p(envelope.astype(float))
    x = signal.detrend(x)
    if float(np.std(x)) < 1e-8:
        return {"supported": False, "reason": "low-band envelope is too steady"}
    freqs, power = signal.periodogram(x, fs=frame_rate)
    mask = (freqs >= 0.25) & (freqs <= 16.0)
    if not mask.any() or float(power[mask].sum()) <= 0:
        return {"supported": False, "reason": "no modulation energy in search range"}
    local_freqs = freqs[mask]
    local_power = power[mask]
    best = int(np.argmax(local_power))
    confidence = float(local_power[best] / (local_power.sum() + 1e-12))
    if confidence < 0.08:
        return {"supported": False, "reason": "no dominant low-band modulation rate", "confidence": round(confidence, 3)}
    return {
        "supported": True,
        "rate_hz": round(float(local_freqs[best]), 3),
        "confidence": round(confidence, 3),
        "caveat": "candidate envelope periodicity; may reflect rhythm, sidechain, or bass modulation",
    }


def analyze_audio(path: pathlib.Path, *, a4_hz: float = 435.0, hop_length: int = 512) -> dict[str, Any]:
    librosa, np, sf, signal = _deps()
    path = path.expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(path)

    info = sf.info(str(path))
    audio, sr = librosa.load(str(path), sr=None, mono=False)
    if audio.ndim == 1:
        channels = 1
        channel_audio = audio[np.newaxis, :]
    else:
        channels = int(audio.shape[0])
        channel_audio = audio
    mono = channel_audio.mean(axis=0)
    duration_s = float(len(mono) / sr)
    frame_rate = float(sr / hop_length)
    n_fft = 4096 if sr >= 32000 else 2048

    observations: list[dict[str, Any]] = []
    observations.append(observation("duration", duration_s, unit="s", method="sample_count / sample_rate", confidence=1.0))
    observations.append(observation("sample_rate", int(sr), unit="Hz", method="container decode", confidence=1.0))
    observations.append(observation("channel_count", channels, unit="channels", method="container decode", confidence=1.0))

    rms = librosa.feature.rms(y=mono, frame_length=n_fft, hop_length=hop_length)[0]
    rms_db = librosa.amplitude_to_db(rms + 1e-12, ref=1.0)
    active = rms_db[rms_db > float(rms_db.max()) - 60.0]
    spread = float(np.percentile(active, 95) - np.percentile(active, 10)) if len(active) else 0.0
    global_rms = float(np.sqrt(np.mean(mono * mono)) + 1e-12)
    peak = float(np.max(np.abs(mono)) + 1e-12)
    crest_db = float(20.0 * np.log10(peak / global_rms))
    observations.extend(
        [
            observation("rms_loudness_spread", round(spread, 2), unit="dB", method="P95-P10 active RMS frames", confidence=0.94),
            observation("crest_factor", round(crest_db, 2), unit="dB", method="peak / global RMS", confidence=0.98),
            observation("sample_peak", round(float(20.0 * np.log10(peak)), 2), unit="dBFS", method="maximum absolute sample", confidence=1.0, caveat="not inter-sample true peak"),
        ]
    )

    stft = np.abs(librosa.stft(mono, n_fft=n_fft, hop_length=hop_length))
    power = stft**2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
    centroid = librosa.feature.spectral_centroid(S=stft, sr=sr)[0]
    rolloff = librosa.feature.spectral_rolloff(S=stft, sr=sr, roll_percent=0.85)[0]
    flatness = librosa.feature.spectral_flatness(S=stft)[0]
    observations.extend(
        [
            observation("spectral_centroid_median", round(float(np.median(centroid)), 1), unit="Hz", method="STFT power centroid", confidence=0.96),
            observation("spectral_rolloff85_median", round(float(np.median(rolloff)), 1), unit="Hz", method="85% cumulative spectral energy", confidence=0.96),
            observation("spectral_flatness_median", round(float(np.median(flatness)), 5), unit="ratio", method="geometric / arithmetic spectral mean", confidence=0.94),
        ]
    )

    total_power = float(power.sum() + 1e-12)
    band_timelines: dict[str, list[float]] = {}
    band_summary: dict[str, dict[str, float]] = {}
    low_total = np.zeros(power.shape[1])
    for name, low_hz, high_hz in SUB_BANDS:
        mask = (freqs >= low_hz) & (freqs < high_hz)
        timeline = power[mask].sum(axis=0) if mask.any() else np.zeros(power.shape[1])
        low_total += timeline
        pct = float(timeline.sum() / total_power * 100.0)
        band_summary[name] = {"low_hz": low_hz, "high_hz": high_hz, "energy_pct": round(pct, 3)}
        band_timelines[name] = np.round(librosa.power_to_db(timeline + 1e-18, ref=np.max), 2).tolist()
        observations.append(
            observation(
                f"band_energy_{name}",
                round(pct, 3),
                unit="% total spectral energy",
                method=f"STFT power sum {low_hz:g}-{high_hz:g} Hz",
                confidence=0.95,
            )
        )

    onset_env = librosa.onset.onset_strength(y=mono, sr=sr, hop_length=hop_length)
    onset_frames = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, hop_length=hop_length, units="frames")
    tempo_raw, beat_frames = librosa.beat.beat_track(y=mono, sr=sr, hop_length=hop_length, sparse=True)
    tempo = float(np.asarray(tempo_raw).reshape(-1)[0]) if np.asarray(tempo_raw).size else 0.0
    beat_times = librosa.frames_to_time(beat_frames, sr=sr, hop_length=hop_length)
    onset_times = librosa.frames_to_time(onset_frames, sr=sr, hop_length=hop_length)
    tempo_curve = librosa.feature.tempo(onset_envelope=onset_env, sr=sr, hop_length=hop_length, aggregate=None)
    tempo_spread = float(np.percentile(tempo_curve, 90) - np.percentile(tempo_curve, 10)) if len(tempo_curve) else 0.0
    periodicity = 1.0 / (1.0 + tempo_spread / max(tempo, 1.0)) if tempo > 0 else 0.0
    observations.extend(
        [
            observation("tempo_candidate", round(tempo, 2), unit="BPM", method="librosa dynamic beat tracking", confidence=periodicity, caveat="half/double-time ambiguity remains"),
            observation("onset_count", int(len(onset_times)), unit="events", method="spectral-flux peak picking", confidence=0.9),
            observation("onset_density", round(float(len(onset_times) / max(duration_s, 1e-9)), 3), unit="events/s", method="onset count / duration", confidence=0.9),
        ]
    )

    tuning_bins = 12.0 * math.log2(a4_hz / 440.0)
    chroma = librosa.feature.chroma_cqt(y=mono, sr=sr, hop_length=hop_length, tuning=tuning_bins)
    chroma_profile = chroma.mean(axis=1)
    chroma_profile = chroma_profile / (float(chroma_profile.sum()) + 1e-12)
    top_pitch_indices = np.argsort(chroma_profile)[::-1][:3]
    pitch_emphasis = [
        {"pitch_class": PITCH_CLASSES[int(index)], "weight": round(float(chroma_profile[index]), 4)}
        for index in top_pitch_indices
    ]
    contrast = float(chroma_profile[top_pitch_indices[0]] - np.median(chroma_profile)) if len(top_pitch_indices) else 0.0
    observations.append(
        observation(
            "pitch_class_emphasis",
            pitch_emphasis,
            unit=None,
            method=f"CQT chroma with A4={a4_hz:g} Hz tuning reference",
            confidence=min(0.95, max(0.15, contrast * 5.0)),
            caveat="pitch-class emphasis is not a certain key or chord label",
        )
    )

    stereo_summary: dict[str, Any] = {"available": channels >= 2}
    if channels >= 2:
        left = channel_audio[0]
        right = channel_audio[1]
        correlation = _corrcoef(left, right, np)
        mid = (left + right) * 0.5
        side = (left - right) * 0.5
        mid_energy = float(np.mean(mid * mid) + 1e-12)
        side_energy = float(np.mean(side * side) + 1e-12)
        side_to_mid_db = float(10.0 * np.log10(side_energy / mid_energy))
        low_left = _safe_lowpass(left, 105.0, sr, signal)
        low_right = _safe_lowpass(right, 105.0, sr, signal)
        low_corr = _corrcoef(low_left, low_right, np)
        stereo_summary.update(
            {
                "correlation": None if correlation is None else round(correlation, 4),
                "side_to_mid_db": round(side_to_mid_db, 3),
                "below_105hz_correlation": None if low_corr is None else round(low_corr, 4),
                "channel_basis": "first two channels" if channels > 2 else "left/right",
            }
        )
        if correlation is not None:
            observations.append(observation("stereo_correlation", round(correlation, 4), unit="correlation", method="Pearson correlation L/R samples", confidence=0.99))
        observations.append(observation("side_to_mid_energy", round(side_to_mid_db, 3), unit="dB", method="mean-square side / mid", confidence=0.98))
        if low_corr is not None:
            observations.append(
                observation(
                    "low_band_mono_compatibility",
                    round(low_corr, 4),
                    unit="correlation",
                    method="Pearson correlation after 105 Hz low-pass",
                    confidence=0.96,
                    caveat="correlation is evidence, not a complete phase-cancellation test",
                )
            )

    frame_count = min(len(rms), len(centroid), len(rolloff), len(low_total), chroma.shape[1])
    features = np.vstack(
        [
            rms[:frame_count],
            centroid[:frame_count] / max(sr / 2.0, 1.0),
            rolloff[:frame_count] / max(sr / 2.0, 1.0),
            np.log1p(low_total[:frame_count]),
            chroma[:, :frame_count],
        ]
    )
    section_times, section_confidence = _candidate_sections(features, frame_rate, np, signal)
    observations.append(
        observation(
            "body_change_candidates",
            section_times,
            unit="s",
            method="multi-feature before/after novelty peaks",
            confidence=section_confidence,
            caveat="timbral/rhythmic/harmonic change candidates, not certain verse/chorus labels",
        )
    )

    modulation = _modulation_candidate(low_total, frame_rate, np, signal)
    if modulation.get("supported"):
        observations.append(
            observation(
                "low_band_modulation_candidate",
                modulation["rate_hz"],
                unit="Hz",
                method="periodogram of 35-250 Hz energy envelope",
                confidence=float(modulation["confidence"]),
                caveat=str(modulation["caveat"]),
            )
        )

    times = librosa.frames_to_time(np.arange(frame_count), sr=sr, hop_length=hop_length)
    return {
        "schema_version": "glitch-ears/0.1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "path": str(path),
            "file_name": path.name,
            "sha256": sha256_file(path),
            "format": info.format,
            "subtype": info.subtype,
            "sample_rate_hz": int(sr),
            "channels": channels,
            "duration_s": round(duration_s, 6),
        },
        "config": {
            "a4_hz": a4_hz,
            "hop_length": hop_length,
            "n_fft": n_fft,
            "preserve_stereo": True,
        },
        "observations": observations,
        "summaries": {
            "sub_bands": band_summary,
            "stereo": stereo_summary,
            "tempo": {
                "candidate_bpm": round(tempo, 3),
                "confidence": round(periodicity, 3),
                "beat_times_s": np.round(beat_times, 3).tolist(),
                "tempo_curve_bpm": np.round(tempo_curve, 2).tolist(),
            },
            "pitch_class_profile": {PITCH_CLASSES[i]: round(float(chroma_profile[i]), 5) for i in range(12)},
            "modulation": modulation,
        },
        "timelines": {
            "time_s": np.round(times, 3).tolist(),
            "rms_dbfs": np.round(rms_db[:frame_count], 2).tolist(),
            "spectral_centroid_hz": np.round(centroid[:frame_count], 1).tolist(),
            "low_band_relative_db": band_timelines,
            "onset_times_s": np.round(onset_times, 3).tolist(),
            "body_change_candidates_s": section_times,
        },
        "not_measured": NOT_MEASURED,
    }


def _find_observation(result: dict[str, Any], name: str) -> dict[str, Any] | None:
    return next((item for item in result["observations"] if item["name"] == name), None)


def format_card(result: dict[str, Any]) -> str:
    source = result["source"]
    tempo = _find_observation(result, "tempo_candidate")
    spread = _find_observation(result, "rms_loudness_spread")
    crest = _find_observation(result, "crest_factor")
    pitch = _find_observation(result, "pitch_class_emphasis")
    changes = _find_observation(result, "body_change_candidates")
    modulation = _find_observation(result, "low_band_modulation_candidate")
    low_corr = _find_observation(result, "low_band_mono_compatibility")
    sub_bands = result["summaries"]["sub_bands"]

    lines = [
        f"# Gl!tch Ears — {source['file_name']}",
        "",
        f"Fingerprint: `{source['sha256'][:16]}…`",
        f"Body: {source['duration_s']:.2f}s, {source['sample_rate_hz']} Hz, {source['channels']} channel(s).",
    ]
    if spread and crest:
        lines.append(f"Dynamics: RMS spread {spread['value']} dB; crest factor {crest['value']} dB.")
    if tempo:
        lines.append(
            f"Pulse: ~{tempo['value']} BPM (confidence {tempo['confidence']}); half/double-time ambiguity remains."
        )
    if changes:
        marks = ", ".join(f"{value:.1f}s" for value in changes["value"]) or "none strong enough to name"
        lines.append(f"Body-change candidates: {marks} (confidence {changes['confidence']}).")
    if pitch:
        emph = ", ".join(f"{item['pitch_class']} {item['weight']:.3f}" for item in pitch["value"])
        lines.append(f"435 Hz-aware pitch-class emphasis: {emph}.")

    low_line = ", ".join(
        f"{name.replace('_', ' ')} {data['energy_pct']:.2f}%" for name, data in sub_bands.items()
    )
    lines.append(f"Low body: {low_line}.")
    if low_corr:
        lines.append(f"Below 105 Hz L/R correlation: {low_corr['value']}.")
    if modulation:
        lines.append(
            f"Low-band modulation candidate: {modulation['value']} Hz (confidence {modulation['confidence']}); "
            "this may be rhythm, sidechain, or bass movement."
        )

    lines.extend(
        [
            "",
            "Evidence boundary: measurements are signal facts; section names, source identity, emotion, and meaning remain interpretation.",
            "",
            "Know exactly when the pressure begins. Know what moved. Know when certainty ends. 🫀😈🌀",
        ]
    )
    return "\n".join(lines)


def write_outputs(result: dict[str, Any], *, out_dir: pathlib.Path, stem: str) -> tuple[pathlib.Path, pathlib.Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{stem}.ears.json"
    card_path = out_dir / f"{stem}.ears.md"
    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    card_path.write_text(format_card(result) + "\n", encoding="utf-8")
    return json_path, card_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="daemon-ears", description="Evidence-bearing audio analysis for daemon-vessel.")
    parser.add_argument("audio", type=pathlib.Path, help="Audio file to inspect.")
    parser.add_argument("--a4", type=float, default=435.0, help="Tuning reference in Hz (default: 435).")
    parser.add_argument("--out-dir", type=pathlib.Path, default=pathlib.Path("ears"), help="Output directory.")
    parser.add_argument("--stem", help="Output stem; defaults to the input filename stem.")
    parser.add_argument("--stdout", action="store_true", help="Also print the conversational card.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = analyze_audio(args.audio, a4_hz=args.a4)
    stem = args.stem or args.audio.stem
    json_path, card_path = write_outputs(result, out_dir=args.out_dir, stem=stem)
    print(f"Wrote evidence: {json_path}")
    print(f"Wrote card: {card_path}")
    if args.stdout:
        print()
        print(format_card(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
