import av
import numpy as np
import webrtcvad
import csv
import json
from pathlib import Path

# Audio configuration
SAMPLE_RATE = 16000

# Pitch analysis uses overlapping 40 ms frames.
FRAME_LENGTH = 640
HOP_LENGTH = 320

MIN_PITCH = 75
MAX_PITCH = 350

# WebRTC VAD configuration
VAD_FRAME_MS = 20
VAD_FRAME_SAMPLES = int(SAMPLE_RATE * VAD_FRAME_MS / 1000)

VAD_MODE = 1

# Speech segmentation configuration
MERGE_GAP_MS = 240
PAUSE_THRESHOLD_SECONDS = 0.25
MIN_RATE_ACTIVE_SECONDS = 10

# YIN pitch estimation configuration
YIN_THRESHOLD = 0.15
YIN_MAX_THRESHOLD = 0.35

DIAGNOSTICS_DIR = Path(__file__).resolve().parent

DIAGNOSTICS_CSV_PATH = (
    DIAGNOSTICS_DIR / "voice_diagnostics_latest.csv"
)

DIAGNOSTICS_JSON_PATH = (
    DIAGNOSTICS_DIR / "voice_diagnostics_latest.json"
)

def decode_audio(audio_path: str) -> np.ndarray:
    """
    Decode an audio recording into mono, 16 kHz, float32 samples.
    """

    audio_chunks = []

    with av.open(audio_path) as container:

        audio_streams = [
            stream for stream in container.streams
            if stream.type == "audio"
        ]

        if not audio_streams:
            raise ValueError("No audio stream found.")

        resampler = av.AudioResampler(
            format="s16",
            layout="mono",
            rate=SAMPLE_RATE
        )

        for frame in container.decode(audio=0):

            converted_frames = resampler.resample(frame)

            for converted in converted_frames:
                audio_chunks.append(
                    converted.to_ndarray().reshape(-1)
                )

        # Flush remaining resampled audio.
        for converted in resampler.resample(None):
            audio_chunks.append(
                converted.to_ndarray().reshape(-1)
            )

    if not audio_chunks:
        raise ValueError("Could not decode audio.")

    samples = np.concatenate(audio_chunks).astype(np.float32)

    return samples / 32768.0


def detect_speech_frames(
    samples: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """
    Use WebRTC VAD to classify 20 ms frames as speech or non-speech.

    Returns:
        speech_mask: Boolean speech detection for each frame.
        rms_values: RMS energy for each corresponding frame.
    """

    frame_count = len(samples) // VAD_FRAME_SAMPLES

    if frame_count == 0:
        return (
            np.zeros(0, dtype=bool),
            np.zeros(0, dtype=np.float32)
        )

    usable_samples = samples[
        :frame_count * VAD_FRAME_SAMPLES
    ]

    # WebRTC VAD requires signed 16-bit PCM.
    pcm16 = np.round(
        np.clip(usable_samples, -1.0, 1.0) * 32767
    ).astype("<i2")

    vad = webrtcvad.Vad(VAD_MODE)

    speech_decisions = []
    rms_values = []

    for index in range(frame_count):

        start = index * VAD_FRAME_SAMPLES
        end = start + VAD_FRAME_SAMPLES

        pcm_frame = pcm16[start:end]
        audio_frame = samples[start:end]

        is_speech = vad.is_speech(
            pcm_frame.tobytes(),
            SAMPLE_RATE
        )

        rms = float(
            np.sqrt(np.mean(audio_frame ** 2))
        )

        speech_decisions.append(is_speech)
        rms_values.append(rms)

    return (
        np.asarray(speech_decisions, dtype=bool),
        np.asarray(rms_values, dtype=np.float32)
    )


def merge_short_silence_gaps(
    speech_mask: np.ndarray
) -> np.ndarray:
    """
    Merge very short silence gaps between detected speech frames.

    This prevents tiny detection gaps between syllables
    from being counted as meaningful pauses.
    """

    result = speech_mask.copy()

    max_gap_frames = int(
        MERGE_GAP_MS / VAD_FRAME_MS
    )

    index = 0

    while index < len(result):

        if result[index]:
            index += 1
            continue

        gap_start = index

        while index < len(result) and not result[index]:
            index += 1

        gap_length = index - gap_start

        # Only merge gaps surrounded by speech.
        if (
            gap_start > 0
            and index < len(result)
            and gap_length <= max_gap_frames
        ):
            result[gap_start:index] = True

    return result


def detect_pauses(
    speech_mask: np.ndarray
) -> list[float]:
    """
    Detect internal pauses between speech regions.

    Leading and trailing recording silence is ignored.
    """

    active_indices = np.flatnonzero(speech_mask)

    if len(active_indices) < 2:
        return []

    first_active = int(active_indices[0])
    last_active = int(active_indices[-1])

    pauses = []
    index = first_active

    while index <= last_active:

        if speech_mask[index]:
            index += 1
            continue

        gap_start = index

        while (
            index <= last_active
            and not speech_mask[index]
        ):
            index += 1

        gap_frames = index - gap_start

        gap_seconds = (
            gap_frames * VAD_FRAME_MS / 1000
        )

        if gap_seconds >= PAUSE_THRESHOLD_SECONDS:
            pauses.append(float(gap_seconds))

    return pauses

def estimate_pitch_with_diagnostics(
    frame: np.ndarray
) -> tuple[float | None, float | None, str]:
    """
    Estimate fundamental frequency using YIN.

    Also returns the CMND value and estimation branch
    for diagnostics without changing pitch-selection logic.
    """

    signal = frame.astype(np.float64)
    signal -= np.mean(signal)

    frame_rms = np.sqrt(np.mean(signal ** 2))

    if frame_rms < 1e-4:
        return None, None, "low_energy"

    min_lag = int(np.ceil(SAMPLE_RATE / MAX_PITCH))
    max_lag = int(np.floor(SAMPLE_RATE / MIN_PITCH))

    difference = np.zeros(max_lag + 1)

    for lag in range(1, max_lag + 1):
        delta = signal[:-lag] - signal[lag:]
        difference[lag] = np.dot(delta, delta)

    cumulative = np.cumsum(difference[1:])

    cmnd = np.ones(max_lag + 1)
    taus = np.arange(1, max_lag + 1)

    cmnd[1:] = (
        difference[1:] * taus
        / np.maximum(cumulative, 1e-12)
    )

    search_region = cmnd[min_lag:max_lag + 1]

    candidates = np.flatnonzero(
        search_region < YIN_THRESHOLD
    )

    if len(candidates) > 0:

        branch = "first_dip"

        best_lag = min_lag + int(candidates[0])

        while (
            best_lag < max_lag
            and cmnd[best_lag + 1] < cmnd[best_lag]
        ):
            best_lag += 1

    else:

        branch = "fallback"

        best_lag = min_lag + int(
            np.argmin(search_region)
        )

        if cmnd[best_lag] > YIN_MAX_THRESHOLD:
            return None, float(cmnd[best_lag]), "fallback_rejected"

    yin_cmnd = float(cmnd[best_lag])

    if 1 < best_lag < max_lag:

        left = cmnd[best_lag - 1]
        middle = cmnd[best_lag]
        right = cmnd[best_lag + 1]

        denominator = left - 2 * middle + right

        if abs(denominator) > 1e-9:
            adjustment = (
                0.5 * (left - right) / denominator
            )
            adjustment = np.clip(adjustment, -1, 1)
        else:
            adjustment = 0.0

    else:
        adjustment = 0.0

    refined_lag = best_lag + adjustment
    pitch = SAMPLE_RATE / refined_lag

    if MIN_PITCH <= pitch <= MAX_PITCH:
        return float(pitch), yin_cmnd, branch

    return None, yin_cmnd, f"{branch}_out_of_range"

def estimate_pitch(frame: np.ndarray) -> float | None:
    """
    Preserve the original estimate_pitch interface.
    """

    pitch, _, _ = estimate_pitch_with_diagnostics(frame)

    return pitch


def save_voice_diagnostics(
    samples: np.ndarray,
    raw_speech_mask: np.ndarray,
    speech_mask: np.ndarray,
    rms_values: np.ndarray,
    pitch_by_frame: list,
    yin_cmnd_by_frame: list,
    pitch_branch_by_frame: list
) -> None:

    def to_dbfs(value):
        return float(
            20 * np.log10(max(float(value), 1e-8))
        )

    # Convert frame RMS values into dBFS.
    rms_dbfs = np.array([
        to_dbfs(value) for value in rms_values
    ])

    speech_levels = rms_dbfs[raw_speech_mask]
    noise_levels = rms_dbfs[~raw_speech_mask]

    pitch_values = [
        value for value in pitch_by_frame
        if value is not None
    ]

    pitch_median = (
        float(np.median(pitch_values))
        if pitch_values else None
    )

    # Identify unusually high pitch estimates.
    pitch_outliers = []

    if pitch_median is not None:

        for index, pitch in enumerate(pitch_by_frame):

            if pitch is None or pitch <= pitch_median * 1.6:
                continue

            pitch_outliers.append({
                "time_seconds": round(
                    (
                        index * HOP_LENGTH
                        + FRAME_LENGTH / 2
                    ) / SAMPLE_RATE,
                    4
                ),
                "pitch_hz": round(float(pitch), 2),
                "yin_cmnd": yin_cmnd_by_frame[index],
                "branch": pitch_branch_by_frame[index]
            })

    # Save frame-by-frame CSV.
    with DIAGNOSTICS_CSV_PATH.open(
        "w", newline="", encoding="utf-8"
    ) as csv_file:

        writer = csv.writer(csv_file)

        writer.writerow([
            "vad_frame_index",
            "vad_start_seconds",
            "vad_center_seconds",
            "vad_raw",
            "vad_merged",
            "rms_dbfs",
            "pitch_window_start_seconds",
            "pitch_window_center_seconds",
            "pitch_hz",
            "yin_cmnd",
            "pitch_branch"
        ])

        for index in range(len(raw_speech_mask)):

            pitch_window_exists = (
                index * HOP_LENGTH + FRAME_LENGTH
                <= len(samples)
            )

            pitch_start = (
                index * HOP_LENGTH / SAMPLE_RATE
            )

            pitch_center = (
                pitch_start
                + FRAME_LENGTH / (2 * SAMPLE_RATE)
            )

            writer.writerow([
                index,
                round(index * VAD_FRAME_MS / 1000, 4),
                round(
                    (index + 0.5) * VAD_FRAME_MS / 1000,
                    4
                ),
                int(raw_speech_mask[index]),
                int(speech_mask[index]),
                round(float(rms_dbfs[index]), 2),
                round(pitch_start, 4)
                    if pitch_window_exists else "",
                round(pitch_center, 4)
                    if pitch_window_exists else "",
                pitch_by_frame[index]
                    if pitch_window_exists else "",
                yin_cmnd_by_frame[index]
                    if pitch_window_exists else "",
                pitch_branch_by_frame[index]
                    if pitch_window_exists else ""
            ])

    # Inspect recording boundaries.
    edge_samples = int(SAMPLE_RATE * 0.1)

    first_100ms = samples[:edge_samples]
    last_100ms = samples[-edge_samples:]

    first_100ms_dbfs = to_dbfs(
        np.sqrt(np.mean(first_100ms ** 2))
    )

    last_100ms_dbfs = to_dbfs(
        np.sqrt(np.mean(last_100ms ** 2))
    )

    # Count YIN estimation branches.
    branch_counts = {
        branch: pitch_branch_by_frame.count(branch)
        for branch in set(pitch_branch_by_frame)
        if branch is not None
    }

    pitch_candidate_count = sum(
        branch is not None
        for branch in pitch_branch_by_frame
    )

    valid_pitch_count = len(pitch_values)

    summary = {
        "duration_seconds": round(
            len(samples) / SAMPLE_RATE, 2
        ),
        "vad_frame_count": len(raw_speech_mask),
        "raw_speech_frame_count": int(
            np.sum(raw_speech_mask)
        ),
        "merged_speech_frame_count": int(
            np.sum(speech_mask)
        ),
        "noise_floor_dbfs": round(
            float(np.median(noise_levels)), 2
        ) if len(noise_levels) else None,
        "median_speech_level_dbfs": round(
            float(np.median(speech_levels)), 2
        ) if len(speech_levels) else None,
        "speech_to_noise_difference_db": round(
            float(
                np.median(speech_levels)
                - np.median(noise_levels)
            ), 2
        ) if len(speech_levels) and len(noise_levels) else None,
        "first_100ms_dbfs": round(
            first_100ms_dbfs, 2
        ),
        "last_100ms_dbfs": round(
            last_100ms_dbfs, 2
        ),
        "pitch_candidate_window_count": pitch_candidate_count,
        "valid_pitch_frame_count": valid_pitch_count,
        "voiced_pitch_fraction": round(
            valid_pitch_count / pitch_candidate_count, 4
        ) if pitch_candidate_count else None,
        "pitch_median_hz": round(
            pitch_median, 2
        ) if pitch_median is not None else None,
        "yin_branch_counts": branch_counts,
        "pitch_outliers_above_1_6x_median": pitch_outliers
    }

    with DIAGNOSTICS_JSON_PATH.open(
        "w", encoding="utf-8"
    ) as json_file:

        json.dump(summary, json_file, indent=4)

def analyze_voice(
    audio_path: str,
    transcription: str
) -> dict:
    """
    Extract approximate vocal characteristics:

    - Speech activity
    - Conversational speaking rate
    - Articulation rate
    - Fundamental pitch
    - Vocal energy
    - Internal pauses
    """

    samples = decode_audio(audio_path)

    duration = len(samples) / SAMPLE_RATE

    if duration < 0.2:
        raise ValueError(
            "Recording is too short for voice analysis."
        )

    # 1. Detect speech using WebRTC VAD.
    raw_speech_mask, rms_values = detect_speech_frames(
        samples
    )

    if len(raw_speech_mask) == 0:
        raise ValueError("Insufficient audio data.")

    # Preserve the original VAD decisions for energy and pitch.
    # Use the smoothed mask for duration and pause calculations.
    speech_mask = merge_short_silence_gaps(
        raw_speech_mask
    )

    # 2. Calculate speech duration.
    active_frames = int(np.sum(speech_mask))

    active_seconds = (
        active_frames * VAD_FRAME_MS / 1000
    )

    active_indices = np.flatnonzero(speech_mask)

    if len(active_indices) > 0:

        speech_span_seconds = (
            (int(active_indices[-1]) -
             int(active_indices[0]) + 1)
            * VAD_FRAME_MS / 1000
        )

    else:
        speech_span_seconds = 0.0

    # 3. Calculate speaking rates.
    word_count = (
        len(transcription.split())
        if transcription.strip()
        else 0
    )

    # Includes internal pauses but excludes leading and trailing recording silence.
    speaking_rate = (
        word_count / speech_span_seconds * 60
        if (
            active_seconds >= MIN_RATE_ACTIVE_SECONDS
            and speech_span_seconds > 0
            and word_count > 0
        )
        else None
    )

    # Uses estimated active speech time.
    articulation_rate = (
        word_count / active_seconds * 60
        if (
            active_seconds >= MIN_RATE_ACTIVE_SECONDS
            and active_seconds > 0
            and word_count > 0
        )
        else None
    )

    # 4. Estimate pitch from 40 ms frames.
    pitch_values = []

    pitch_by_frame = [None] * len(raw_speech_mask)
    yin_cmnd_by_frame = [None] * len(raw_speech_mask)
    pitch_branch_by_frame = [None] * len(raw_speech_mask)

    pitch_frame_starts = range(
        0,
        len(samples) - FRAME_LENGTH + 1,
        HOP_LENGTH
    )

    vad_frames_per_pitch_frame = (
        FRAME_LENGTH // VAD_FRAME_SAMPLES
    )

    for start in pitch_frame_starts:

        vad_start = start // VAD_FRAME_SAMPLES

        vad_end = min(
            vad_start + vad_frames_per_pitch_frame,
            len(raw_speech_mask)
        )

        if not np.any(raw_speech_mask[vad_start:vad_end]):
            continue

        frame = samples[start:start + FRAME_LENGTH]

        pitch, yin_cmnd, branch = estimate_pitch_with_diagnostics(frame)

        pitch_by_frame[vad_start] = pitch
        yin_cmnd_by_frame[vad_start] = yin_cmnd
        pitch_branch_by_frame[vad_start] = branch

        if pitch is not None:
            pitch_values.append(pitch)


    # Save frame-level diagnostic data.
    try:
        save_voice_diagnostics(
            samples,
            raw_speech_mask,
            speech_mask,
            rms_values,
            pitch_by_frame,
            yin_cmnd_by_frame,
            pitch_branch_by_frame
        )

        print("Voice diagnostics saved successfully.")

    except Exception as error:
        print("Voice diagnostics error:", error)


    # 5. Calculate vocal energy using actual VAD-positive frames.
    active_rms = rms_values[raw_speech_mask]

    if len(active_rms) > 0:

        mean_rms = float(
            np.sqrt(np.mean(active_rms ** 2))
        )

        mean_energy_dbfs = 20 * np.log10(
            max(mean_rms, 1e-8)
        )

        energy_db = 20 * np.log10(
            np.maximum(active_rms, 1e-8)
        )

        energy_variation = float(
            np.std(energy_db)
        )

    else:
        mean_energy_dbfs = None
        energy_variation = None

    # 6. Calculate pitch statistics.
    if pitch_values:

        pitch_array = np.asarray(pitch_values)

        pitch_mean = float(np.mean(pitch_array))
        pitch_median = float(np.median(pitch_array))
        pitch_variation = float(np.std(pitch_array))

        pitch_p10 = float(
            np.percentile(pitch_array, 10)
        )

        pitch_p90 = float(
            np.percentile(pitch_array, 90)
        )

    else:
        pitch_mean = None
        pitch_median = None
        pitch_variation = None
        pitch_p10 = None
        pitch_p90 = None

    # 7. Detect internal pauses.
    pauses = detect_pauses(speech_mask)

    return {
        "duration_seconds": round(duration, 2),

        "speech_span_seconds": round(
            speech_span_seconds, 2
        ),

        "speech_active_seconds": round(
            float(active_seconds), 2
        ),

        "speaking_rate_wpm": (
            round(float(speaking_rate), 1)
            if speaking_rate is not None else None
        ),

        "articulation_rate_wpm": (
            round(float(articulation_rate), 1)
            if articulation_rate is not None else None
        ),

        "pitch_mean_hz": (
            round(pitch_mean, 1)
            if pitch_mean is not None else None
        ),

        "pitch_median_hz": (
            round(pitch_median, 1)
            if pitch_median is not None else None
        ),

        "pitch_variation_hz": (
            round(pitch_variation, 1)
            if pitch_variation is not None else None
        ),

        "pitch_range_hz": {
            "p10": round(pitch_p10, 1),
            "p90": round(pitch_p90, 1)
        } if pitch_p10 is not None else None,

        "mean_vocal_energy_dbfs": (
            round(mean_energy_dbfs, 1)
            if mean_energy_dbfs is not None else None
        ),

        "energy_variation_db": (
            round(energy_variation, 1)
            if energy_variation is not None else None
        ),

        "pause_count": len(pauses),

        "average_pause_seconds": (
            round(float(np.mean(pauses)), 2)
            if pauses else 0
        ),

        "longest_pause_seconds": (
            round(max(pauses), 2)
            if pauses else 0
        )
    }
