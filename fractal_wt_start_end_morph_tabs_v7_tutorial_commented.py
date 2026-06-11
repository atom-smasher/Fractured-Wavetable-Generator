#!/usr/bin/env python3
"""
fractal_wt_live_generation_windows.py

A small Linux-friendly real-time version of Carl Hudson / tonysnail's
Fractal Wavetable Generator idea.

Controls:
  - Pot 1: 0..4095
  - Pot 2: 0..4095
  - Pot 3: 0..4095
  - Wavetable type: 0..9
  - Frequency: 20.000..20000.000 Hz
  - Note + octave selectors for pitch
  - FM self-modulation: 0..255
  - PWM/self phase-width warp: 0..255
  - AM self-modulation: 0..255
  - Source/Result window start/end generation controls: 0..4096
  - Source/Result window start/end generation controls: 0..4096
  - Single-cycle waveform visualiser
  - Volume: 0..1

This version adds numerical safety around the pot1/pot2 interaction. The
original recurrence can grow very quickly in wavetable types 0, 2, and 3.
The safety clips the internal iter2 multiplier rather than moving the sliders,
then keeps non-finite tables out of the audio callback so the oscillator cannot
get poisoned by NaN/Inf values.
"""
# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Reader’s map for this tutorial-commented copy: the file is intentionally layered.
# TUTORIAL: Early code defines the original single-engine generator and live oscillator. Later
# TUTORIAL: sections override selected functions/classes to add generation-stage windows,
# TUTORIAL: visualiser modes, bounded FM pitch range, stream recovery, 12-bit pots, START/END
# TUTORIAL: morph export, richer JSON metadata, bulk export, and finally v7 scroll/progress
# TUTORIAL: fixes.
# TUTORIAL: In Python, a later def with the same name replaces the earlier global name.
# TUTORIAL: Likewise, assignments such as App._draw_waveform = some_function replace a class
# TUTORIAL: method at runtime. This file keeps those layers visible, so it reads like an
# TUTORIAL: annotated development history rather than a clean-room rewrite.
# TUTORIAL: For learning, follow three paths separately: (1) wavetable generation: PatchSettings
# TUTORIAL: → generate_wavetable → finalise/normalise; (2) live audition: Tk controls →
# TUTORIAL: SharedState → WavetableOscillator/AudioEngine callback; (3) export: START/END
# TUTORIAL: patches → _render_morph_audio → _write_wav_mono + JSON sidecar.
# TUTORIAL: Comments marked TUTORIAL were added for explanation only. The executable lines from
# TUTORIAL: v7 are preserved exactly in this copy.
# TUTORIAL: ------------------------------------------------------------------------

from __future__ import annotations

import argparse
import math
import queue
import threading
import tkinter as tk
from dataclasses import dataclass

import numpy as np
import sounddevice as sd

# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Global constants define the operating limits of the program. They are deliberately
# TUTORIAL: near the top because DSP code, UI code, and export code all need to agree on table
# TUTORIAL: size, sample rate limits, and safety thresholds.
# TUTORIAL: Changing a value here can affect many later functions, so treat this block as the
# TUTORIAL: instrument’s technical specification.
# TUTORIAL: ------------------------------------------------------------------------
TABLE_SIZE = 256
DEFAULT_SAMPLE_RATE = 48_000
DEFAULT_FREQUENCY = 110.0
MIN_FREQUENCY = 20.0
MAX_FREQUENCY = 20_000.0
A4_FREQUENCY = 440.0
NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
WORK_DTYPE = np.float64
EPSILON = 1e-20
# Internal interaction limit for the original iter2 recurrence.
# The risky thing is not either pot by itself, but this multiplier:
#     seed0 * seed1 == (pot1 / 128) * (pot2 / 128)
# 255 * 181 is safe; 255 * 182 is the observed cliff.
SAFE_ITER2_PRODUCT = 255 * 181
SAFE_ITER2_MULTIPLIER = SAFE_ITER2_PRODUCT / float(128 * 128)
# Types that build or combine the iter2 recurrence need the interaction clamp.
# Type 1 is iter3-only and was observed not to hit the pot1/pot2 cliff.
WT_TYPES_USING_ITER2 = {0, 2, 3, 4, 5, 6, 7, 8, 9}
WAVETABLE_TYPE_MAX = 9
WINDOW_STEPS = 4096.0
MIN_WINDOW_STEPS = 16.0  # One 256-table sample. Prevents zero-length windows.
PREVIEW_FRAMES = 2048
OUTPUT_SILENCE_PEAK = 1e-4
OUTPUT_SILENCE_RMS = 1e-5
OUTPUT_CLIP_PEAK = 1.0
# Prevent self-FM from freezing the oscillator when the modulation signal drives
# instantaneous frequency to exactly zero.
MIN_FM_PHASE_SCALE = 0.02

# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Human-readable descriptions for wavetable modes. The numeric IDs are what the engine
# TUTORIAL: stores and exports; the descriptions are what the user sees while learning what each
# TUTORIAL: mode does.
# TUTORIAL: Keeping the labels close to the numeric IDs helps prevent UI text, JSON metadata,
# TUTORIAL: and generator logic from drifting apart.
# TUTORIAL: ------------------------------------------------------------------------
WAVETABLE_DESCRIPTIONS = {
    0: "Type 0: iter2 only — 3-step recurrence, driven by the Pot 1 × Pot 2 interaction.",
    1: "Type 1: iter3 only — 9-step recurrence, more shaped by the Pot 1 × Pot 3 interaction.",
    2: "Type 2: splice — first half iter3, second half iter2, using every second source sample.",
    3: "Type 3: splice — first half iter2, second half iter3, using every second source sample.",
    4: "Type 4: SUM — normalise iter2 and iter3, then add them together.",
    5: "Type 5: DIFF — normalise iter2 and iter3, then subtract iter3 from iter2.",
    6: "Type 6: MULTIPLY — normalise iter2 and iter3, then multiply them sample-by-sample.",
    7: "Type 7: DIVIDE — normalise iter2 and iter3, then divide iter2 by a protected iter3 magnitude.",
    8: "Type 8: OR — convert iter2 and iter3 to 8-bit tables, bitwise OR, then convert back.",
    9: "Type 9: XOR — convert iter2 and iter3 to 8-bit tables, bitwise XOR, then convert back.",
}


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Small guard function: any value entering a control path should be clipped to the
# TUTORIAL: range the rest of the engine expects. This prevents a UI entry, JSON import, or
# TUTORIAL: randomiser from sending impossible values downstream.
# TUTORIAL: Notice that the function returns an int. Keeping the type stable matters because Tk
# TUTORIAL: variables, array indexes, and WAV export settings often assume integers.
# TUTORIAL: ------------------------------------------------------------------------
def clamp_int(x: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(x)))


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Float version of the range guard. Several later override layers replace this name
# TUTORIAL: with a non-finite-safe version; Python looks up globals at call time, so old
# TUTORIAL: functions automatically use the safer later definition.
# TUTORIAL: ------------------------------------------------------------------------
def clamp_float(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(x)))


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Core wavetable reader. The table has discrete samples, but an oscillator phase is
# TUTORIAL: continuous, so this interpolates between adjacent table entries instead of snapping
# TUTORIAL: to the nearest sample.
# TUTORIAL: The bit mask assumes TABLE_SIZE is a power of two. With a 256-sample table, wrapping
# TUTORIAL: can use & 255 instead of a slower modulo on every audio sample.
# TUTORIAL: ------------------------------------------------------------------------
def read_table_linear(table: np.ndarray, phase: float) -> float:
    """Read a wavetable at phase 0..1 with linear interpolation."""
    phase = phase % 1.0
    index = phase * TABLE_SIZE
    i0 = int(index) & (TABLE_SIZE - 1)
    i1 = (i0 + 1) & (TABLE_SIZE - 1)
    frac = index - int(index)
    return float(table[i0] + (table[i1] - table[i0]) * frac)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Maps a normal phase, 0..1, into a selected source/result window. Earlier versions
# TUTORIAL: used this in playback; later versions move the windows into generation so they
# TUTORIAL: reshape the table rather than gate the live oscillator.
# TUTORIAL: ------------------------------------------------------------------------
def apply_phase_window(phase: float, start_steps: float, length_steps: float) -> float:
    """Map phase 0..1 into a start/length window on a 0..4096 scale."""
    start = (float(start_steps) % WINDOW_STEPS) / WINDOW_STEPS
    length = clamp_float(float(length_steps), MIN_WINDOW_STEPS, WINDOW_STEPS) / WINDOW_STEPS
    return (start + (phase % 1.0) * length) % 1.0


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Converts a user-facing start/end pair into start/length. This is easier for phase
# TUTORIAL: mapping: phase = start + phase * length.
# TUTORIAL: A start greater than end is treated as a wrapped window, so the user can select a
# TUTORIAL: region crossing the cycle boundary.
# TUTORIAL: ------------------------------------------------------------------------
def clamp_window_pair(start: float, end: float, label: str) -> tuple[float, float, list[str]]:
    """Return safe start+length on the 0..4096 window scale plus notes.

    0→4096 is treated as a full-cycle window. Equal start/end elsewhere is
    treated as a zero-length request and expanded to MIN_WINDOW_STEPS. If end
    is lower than start, the window wraps around the end of the cycle.
    """

    requested_start = float(start)
    requested_end = float(end)
    safe_start = clamp_float(requested_start, 0.0, WINDOW_STEPS)
    safe_end = clamp_float(requested_end, 0.0, WINDOW_STEPS)
    notes: list[str] = []

    if safe_start != requested_start:
        notes.append(f"{label} start {requested_start:.0f}→{safe_start:.0f}")
    if safe_end != requested_end:
        notes.append(f"{label} end {requested_end:.0f}→{safe_end:.0f}")

    if safe_start == 0.0 and safe_end == WINDOW_STEPS:
        length = WINDOW_STEPS
    elif safe_end == safe_start:
        length = 0.0
    elif safe_end > safe_start:
        length = safe_end - safe_start
    else:
        notes.append(f"{label} start>end; wrapped/clamped {safe_start:.0f}→{safe_end:.0f}")
        length = (WINDOW_STEPS - safe_start) + safe_end

    if length < MIN_WINDOW_STEPS:
        notes.append(f"{label} window {length:.0f}→{MIN_WINDOW_STEPS:.0f} steps")
        length = MIN_WINDOW_STEPS

    return safe_start, length, notes


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Renders a short silent preview to check whether the current settings would produce
# TUTORIAL: finite, audible audio. This is a safety probe, not something the user hears.
# TUTORIAL: It mirrors the audio callback closely enough to catch silent windows, NaN/Inf
# TUTORIAL: values, or full-scale clipping before those values reach PortAudio.
# TUTORIAL: ------------------------------------------------------------------------
def preview_output_stats(
    table: np.ndarray,
    *,
    volume: float,
    frequency: float,
    fm: float,
    pwm: float,
    am: float,
    before_start: float,
    before_length: float,
    after_start: float,
    after_length: float,
    sample_rate: float,
    frames: int = PREVIEW_FRAMES,
) -> tuple[bool, float, float]:
    """Render a short silent preview and return finite/peak/RMS safety stats.

    This mirrors the audio callback closely enough to detect window/modulation
    combinations that collapse to silence, produce non-finite output, or exceed
    full-scale before they reach the real-time audio stream.
    """

    if not table_is_usable(table):
        table = fallback_table()

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
    # TUTORIAL: samples, and table generation can sometimes hit extreme math.
    # TUTORIAL: ------------------------------------------------------------------------
    table = np.nan_to_num(table.astype(np.float32), nan=0.0, posinf=1.0, neginf=-1.0)
    volume = clamp_float(volume, 0.0, 1.0)
    base_phase_inc = clamp_float(frequency, MIN_FREQUENCY, MAX_FREQUENCY) / float(sample_rate)
    fm_index = clamp_float(fm, 0.0, 255.0) / 255.0 * 4.0
    pwm_depth = clamp_float(pwm, 0.0, 255.0) / 255.0 * 0.48
    am_depth = clamp_float(am, 0.0, 255.0) / 255.0
    before_start = clamp_float(before_start, 0.0, WINDOW_STEPS)
    before_length = clamp_float(before_length, MIN_WINDOW_STEPS, WINDOW_STEPS)
    after_start = clamp_float(after_start, 0.0, WINDOW_STEPS)
    after_length = clamp_float(after_length, MIN_WINDOW_STEPS, WINDOW_STEPS)

    phase = 0.0
    out = np.empty(frames, dtype=np.float64)
    for i in range(frames):
        math_phase = apply_phase_window(phase, before_start, before_length)
        mod_sample = read_table_linear(table, math_phase)

        am_gain = (1.0 - am_depth) + am_depth * (0.5 + 0.5 * mod_sample)
        am_gain = clamp_float(am_gain, 0.0, 1.5)
        mod_signal = clamp_float(mod_sample * am_gain, -1.0, 1.0)

        width = clamp_float(0.5 + pwm_depth * mod_signal, 0.02, 0.98)
        if math_phase < width:
            read_phase = (math_phase / width) * 0.5
        else:
            read_phase = 0.5 + ((math_phase - width) / (1.0 - width)) * 0.5

        read_phase = apply_phase_window(read_phase, after_start, after_length)
        sample = read_table_linear(table, read_phase)
        out[i] = sample * volume * am_gain

        fm_scale = clamp_float(1.0 + fm_index * mod_signal, MIN_FM_PHASE_SCALE, 8.0)
        phase_inc = base_phase_inc * fm_scale
        phase += min(phase_inc, 0.49)
        if phase >= 1.0:
            phase -= math.floor(phase)

    finite = bool(np.all(np.isfinite(out)))
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
    # TUTORIAL: samples, and table generation can sometimes hit extreme math.
    # TUTORIAL: ------------------------------------------------------------------------
    out = np.nan_to_num(out, nan=0.0, posinf=1.0, neginf=-1.0)
    peak = float(np.max(np.abs(out)))
    rms = float(np.sqrt(np.mean(out * out)))
    return finite, peak, rms


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function clamp_frequency: small named steps make the signal path, UI path,
# TUTORIAL: and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def clamp_frequency(value: float) -> tuple[float, list[str]]:
    requested = float(value)
    clipped = clamp_float(requested, MIN_FREQUENCY, MAX_FREQUENCY)
    notes: list[str] = []
    if clipped != requested:
        notes.append(f"frequency {requested:.3f}→{clipped:.3f} Hz")
    return clipped, notes


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: The frequency slider is logarithmic. Musically, pitch is perceived roughly by
# TUTORIAL: ratios, so a log slider gives useful resolution at low frequencies and still reaches
# TUTORIAL: 20 kHz.
# TUTORIAL: ------------------------------------------------------------------------
def frequency_to_slider_value(frequency: float) -> float:
    frequency = clamp_float(frequency, MIN_FREQUENCY, MAX_FREQUENCY)
    return math.log(frequency / MIN_FREQUENCY) / math.log(MAX_FREQUENCY / MIN_FREQUENCY)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Inverse of frequency_to_slider_value. Keeping these two functions paired makes the
# TUTORIAL: slider and numeric entry stay consistent.
# TUTORIAL: ------------------------------------------------------------------------
def slider_value_to_frequency(slider_value: float) -> float:
    slider_value = clamp_float(slider_value, 0.0, 1.0)
    return MIN_FREQUENCY * ((MAX_FREQUENCY / MIN_FREQUENCY) ** slider_value)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: MIDI-style note conversion. A4 is the anchor at 440 Hz, and every semitone is a
# TUTORIAL: factor of the twelfth root of two.
# TUTORIAL: ------------------------------------------------------------------------
def note_to_frequency(note_name: str, octave: int) -> float:
    note_index = NOTE_NAMES.index(note_name)
    midi_note = (int(octave) + 1) * 12 + note_index
    return A4_FREQUENCY * (2.0 ** ((midi_note - 69) / 12.0))


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Finds the closest equal-tempered note for a typed frequency. The UI uses this as
# TUTORIAL: feedback only; it does not quantise the user’s frequency unless the user selects a
# TUTORIAL: note/octave.
# TUTORIAL: ------------------------------------------------------------------------
def nearest_note_for_frequency(frequency: float) -> tuple[str, int]:
    frequency = clamp_float(frequency, MIN_FREQUENCY, MAX_FREQUENCY)
    midi_note = int(round(69 + 12 * math.log2(frequency / A4_FREQUENCY)))
    note_index = midi_note % 12
    octave = (midi_note // 12) - 1
    return NOTE_NAMES[note_index], octave


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Validates the core patch controls and also reports safety notes. The important
# TUTORIAL: detail is that risky interactions are clipped internally while leaving the visible
# TUTORIAL: slider positions alone.
# TUTORIAL: That distinction matters for learning: the user can see which values they requested,
# TUTORIAL: while the engine can still protect itself from numeric blow-ups.
# TUTORIAL: ------------------------------------------------------------------------
def clamp_control_values(
    pot1: int,
    pot2: int,
    pot3: int,
    wt_type: int,
    volume: float,
) -> tuple[int, int, int, int, float, list[str]]:
    """Return range-checked controls plus human-readable safety notes.

    The ordinary controls stay as requested wherever possible. The important
    safety is now internal: for wavetable types that use the risky iter2 path,
    the pot1×pot2 *interaction multiplier* is clipped inside generate_wavetable
    rather than pushing one of the sliders/effective pot values down.
    """

    requested = (int(pot1), int(pot2), int(pot3), int(wt_type), float(volume))

    p1 = clamp_int(requested[0], 1, 255)
    p2 = clamp_int(requested[1], 0, 255)
    p3 = clamp_int(requested[2], 0, 255)
    typ = clamp_int(requested[3], 0, WAVETABLE_TYPE_MAX)
    vol = clamp_float(requested[4], 0.0, 1.0)

    notes: list[str] = []

    if p1 != requested[0]:
        notes.append(f"pot1 {requested[0]}→{p1}")
    if p2 != requested[1]:
        notes.append(f"pot2 {requested[1]}→{p2}")
    if p3 != requested[2]:
        notes.append(f"pot3 {requested[2]}→{p3}")
    if typ != requested[3]:
        notes.append(f"type {requested[3]}→{typ}")
    if vol != requested[4]:
        notes.append(f"volume {requested[4]:.2f}→{vol:.2f}")

    # This does not change p1 or p2. It just tells the UI that the generator
    # will clip the internal iter2 multiplier derived from p1×p2.
    if typ in WT_TYPES_USING_ITER2 and p1 * p2 > SAFE_ITER2_PRODUCT:
        requested_mult = (p1 * p2) / float(128 * 128)
        notes.append(
            f"iter2 multiplier clipped {requested_mult:.3f}→{SAFE_ITER2_MULTIPLIER:.3f}"
        )

    return p1, p2, p3, typ, vol, notes


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Emergency oscillator table. If generation collapses to silence or non-finite values,
# TUTORIAL: the audio system needs a known-good waveform rather than crashing or poisoning the
# TUTORIAL: callback.
# TUTORIAL: ------------------------------------------------------------------------
def fallback_table() -> np.ndarray:
    """A harmless square-wave oscillator table used only if generation fails."""
    table = np.empty(TABLE_SIZE, dtype=np.float32)
    table[: TABLE_SIZE // 2] = 1.0
    table[TABLE_SIZE // 2 :] = -1.0
    return table


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Minimal sanity check for a generated table: correct shape, finite values, and not
# TUTORIAL: completely silent. Many higher-level functions call this before trusting a table.
# TUTORIAL: ------------------------------------------------------------------------
def table_is_usable(table: np.ndarray) -> bool:
    return (
        table.shape == (TABLE_SIZE,)
        and np.all(np.isfinite(table))
        and float(np.max(np.abs(table))) > EPSILON
    )


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Turns an arbitrary raw vector into a centred, peak-normalised -1..+1 wavetable. DC
# TUTORIAL: removal and peak scaling here make generated tables behave like oscillator sources.
# TUTORIAL: ------------------------------------------------------------------------
def normalise_bipolar(x: np.ndarray) -> np.ndarray:
    """Return x as a DC-centred, peak-normalised bipolar float64 table."""
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
    # TUTORIAL: samples, and table generation can sometimes hit extreme math.
    # TUTORIAL: ------------------------------------------------------------------------
    y = np.nan_to_num(x.astype(WORK_DTYPE), nan=0.0, posinf=0.0, neginf=0.0)
    y -= float(np.mean(y))
    peak = float(np.max(np.abs(y)))
    if (not math.isfinite(peak)) or peak <= EPSILON:
        return np.zeros(TABLE_SIZE, dtype=WORK_DTYPE)
    return y / peak


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function bipolar_to_u8: small named steps make the signal path, UI path, and
# TUTORIAL: export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def bipolar_to_u8(x: np.ndarray) -> np.ndarray:
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Clip the signal/control range after math that might overshoot. This keeps audio
    # TUTORIAL: buffers and tables bounded.
    # TUTORIAL: ------------------------------------------------------------------------
    y = np.clip(x, -1.0, 1.0)
    return np.round((y + 1.0) * 127.5).astype(np.uint8)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function u8_to_bipolar: small named steps make the signal path, UI path, and
# TUTORIAL: export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def u8_to_bipolar(x: np.ndarray) -> np.ndarray:
    return (x.astype(WORK_DTYPE) / 127.5) - 1.0


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Final cleanup step for generated tables. Think of this as the boundary between
# TUTORIAL: fractal math and audio-safe wavetable data.
# TUTORIAL: ------------------------------------------------------------------------
def finalise_raw_table(raw: np.ndarray) -> np.ndarray:
    """Universal final safety and audio conversion for all wavetable modes."""
    table = normalise_bipolar(raw)
    if not table_is_usable(table.astype(np.float32)):
        return fallback_table()
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
    # TUTORIAL: samples, and table generation can sometimes hit extreme math.
    # TUTORIAL: ------------------------------------------------------------------------
    table = np.nan_to_num(table, nan=0.0, posinf=1.0, neginf=-1.0)
    return table.astype(np.float32)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: The core generator. It builds the iter2 and iter3 recurrence arrays, then combines
# TUTORIAL: or selects them according to wavetable type.
# TUTORIAL: Later in the file this function name is redefined to add generation-stage windows
# TUTORIAL: and 12-bit pot behaviour. That layered style is why this script works like a visible
# TUTORIAL: development history.
# TUTORIAL: ------------------------------------------------------------------------
def generate_wavetable(pot1: int, pot2: int, pot3: int, wt_type: int) -> np.ndarray:
    """Generate one 256-sample bipolar wavetable.

    This follows the original C algorithm closely, with practical changes:
      1. Guard against divide-by-zero parameter combinations.
      2. Use float64 internally so the pot1/pot2 recurrence does not overflow.
      3. Convert the original 0..256-ish output table to centred float audio.
    """

    pot1 = clamp_int(pot1, 1, 255)
    pot2 = clamp_int(pot2, 0, 255)
    pot3 = clamp_int(pot3, 0, 255)
    wt_type = clamp_int(wt_type, 0, WAVETABLE_TYPE_MAX)

    max_val = 0.0
    max_val2 = 0.0
    temp = np.zeros(TABLE_SIZE, dtype=WORK_DTYPE)
    iter2 = np.zeros(TABLE_SIZE, dtype=WORK_DTYPE)
    iter3 = np.zeros(TABLE_SIZE, dtype=WORK_DTYPE)

    # Original maps pot values to 0.0..about 2.0.
    seed0 = (float(pot1) / 256.0) * 2.0
    seed1 = (float(pot2) / 256.0) * 2.0
    seed2 = (float(pot3) / 256.0) * 2.0

    # First iteration.
    # temp[1] is the dangerous pot1×pot2 interaction. For risky wave types,
    # clip that internal multiplier instead of changing either slider value.
    iter2_multiplier = seed1 * seed0
    if wt_type in WT_TYPES_USING_ITER2 and iter2_multiplier > SAFE_ITER2_MULTIPLIER:
        iter2_multiplier = SAFE_ITER2_MULTIPLIER

    temp[0] = seed0 * seed0
    temp[1] = iter2_multiplier
    temp[2] = seed2 * seed0

    # Second iteration. This is the pot1/pot2-sensitive part.
    iter2[0:3] = temp[0:3]
    for i in range(3, TABLE_SIZE):
        iter2[i] = iter2[i - 3] * iter2[1]

    # Third iteration.
    iter3[0:9] = iter2[0:9]
    for i in range(9, TABLE_SIZE):
        iter3[i] = iter3[i - 9] * iter3[2]

    if wt_type == 0:
        raw = iter2

    elif wt_type == 1:
        raw = iter3

    elif wt_type in (2, 3):
        # Original hybrid/splice modes. They use every second source sample and
        # normalise the two halves separately before the final audio cleanup.
        skip = 0
        for j in range(TABLE_SIZE):
            if wt_type == 2:
                # A<>B: first half iter3, second half iter2.
                if j < 128:
                    temp[j] = iter3[skip]
                    max_val = max(max_val, float(temp[j]))
                else:
                    temp[j] = iter2[skip - 256]
                    max_val2 = max(max_val2, float(temp[j]))
            else:
                # B><A: first half iter2, second half iter3.
                if j < 128:
                    temp[j] = iter2[skip]
                    max_val = max(max_val, float(temp[j]))
                else:
                    temp[j] = iter3[skip - 256]
                    max_val2 = max(max_val2, float(temp[j]))
            skip += 2

        out = np.zeros(TABLE_SIZE, dtype=WORK_DTYPE)
        if math.isfinite(max_val) and max_val > EPSILON:
            out[:128] = (temp[:128] / max_val) * 256.0
        if math.isfinite(max_val2) and max_val2 > EPSILON:
            out[128:] = (temp[128:] / max_val2) * 256.0
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
        # TUTORIAL: samples, and table generation can sometimes hit extreme math.
        # TUTORIAL: ------------------------------------------------------------------------
        raw = (np.nan_to_num(out, nan=0.0, posinf=256.0, neginf=0.0) / 128.0) - 1.0

    else:
        # New combination modes. Normalise each internal source first so the
        # operation describes the relationship between shapes, not just scale.
        a = normalise_bipolar(iter2)
        b = normalise_bipolar(iter3)

        if wt_type == 4:       # SUM
            raw = a + b
        elif wt_type == 5:     # DIFF
            raw = a - b
        elif wt_type == 6:     # MULTIPLY
            raw = a * b
        elif wt_type == 7:     # DIVIDE, protected against tiny denominators
            raw = a / np.maximum(np.abs(b), 0.05)
        elif wt_type == 8:     # bitwise OR
            raw = u8_to_bipolar(bipolar_to_u8(a) | bipolar_to_u8(b))
        elif wt_type == 9:     # bitwise XOR
            raw = u8_to_bipolar(bipolar_to_u8(a) ^ bipolar_to_u8(b))
        else:
            raw = iter2

    return finalise_raw_table(raw)


@dataclass
# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Dataclass shared between the Tk GUI thread and the real-time audio callback. The
# TUTORIAL: lock is critical: GUI updates and audio reads must not modify/read the same values
# TUTORIAL: halfway through an update.
# TUTORIAL: ------------------------------------------------------------------------
class SharedState:
    target_table: np.ndarray
    target_volume: float
    target_frequency: float
    target_fm: float
    target_pwm: float
    target_am: float
    target_before_start: float
    target_before_length: float
    target_after_start: float
    target_after_length: float
    reset_requested: bool
    lock: threading.Lock


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Real-time oscillator object used by the sounddevice callback. It owns phase and
# TUTORIAL: smoothed current values, while SharedState provides the target values set by the
# TUTORIAL: GUI.
# TUTORIAL: ------------------------------------------------------------------------
class WavetableOscillator:
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function __init__: small named steps make the signal path, UI path, and
    # TUTORIAL: export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def __init__(self, state: SharedState, sample_rate: int):
        self.state = state
        self.sample_rate = float(sample_rate)
        self.phase = 0.0
        self.current_table = state.target_table.copy()
        self.current_volume = 0.0
        self.current_frequency = float(state.target_frequency)
        self.current_fm = float(state.target_fm)
        self.current_pwm = float(state.target_pwm)
        self.current_am = float(state.target_am)
        self.current_before_start = float(state.target_before_start)
        self.current_before_length = float(state.target_before_length)
        self.current_after_start = float(state.target_after_start)
        self.current_after_length = float(state.target_after_length)

        # Higher = faster morph to new value. These are per audio block, not per sample.
        self.table_morph = 0.08
        self.volume_morph = 0.02
        self.frequency_morph = 0.08
        self.modulation_morph = 0.10
        self.window_morph = 0.12

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Audio callbacks must be fast and predictable. They fill the output buffer, avoid
    # TUTORIAL: blocking work, and should never rely on slow UI operations or file I/O.
    # TUTORIAL: ------------------------------------------------------------------------
    def callback(self, outdata, frames, time, status):  # noqa: D401 - sounddevice API name
        if status:
            # Avoid printing every block if the system is struggling.
            pass

        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
        # TUTORIAL: callback may touch it at the same time.
        # TUTORIAL: ------------------------------------------------------------------------
        with self.state.lock:
            target_table = self.state.target_table.copy()
            target_volume = float(self.state.target_volume)
            target_frequency = float(self.state.target_frequency)
            target_fm = float(self.state.target_fm)
            target_pwm = float(self.state.target_pwm)
            target_am = float(self.state.target_am)
            target_before_start = float(self.state.target_before_start)
            target_before_length = float(self.state.target_before_length)
            target_after_start = float(self.state.target_after_start)
            target_after_length = float(self.state.target_after_length)
            reset_requested = self.state.reset_requested
            self.state.reset_requested = False

        if not table_is_usable(target_table):
            target_table = fallback_table()

        if reset_requested:
            # Hard reset the audio-side oscillator state.
            self.current_table = target_table.copy()
            self.current_volume = target_volume
            self.current_frequency = target_frequency
            self.current_fm = target_fm
            self.current_pwm = target_pwm
            self.current_am = target_am
            self.current_before_start = target_before_start
            self.current_before_length = target_before_length
            self.current_after_start = target_after_start
            self.current_after_length = target_after_length
            self.phase = 0.0
        else:
            # Smooth table, volume, pitch, and modulation changes to reduce zipper noise/clicking.
            self.current_table += (target_table - self.current_table) * self.table_morph
            self.current_volume += (target_volume - self.current_volume) * self.volume_morph
            self.current_frequency += (target_frequency - self.current_frequency) * self.frequency_morph
            self.current_fm += (target_fm - self.current_fm) * self.modulation_morph
            self.current_pwm += (target_pwm - self.current_pwm) * self.modulation_morph
            self.current_am += (target_am - self.current_am) * self.modulation_morph
            self.current_before_start += (target_before_start - self.current_before_start) * self.window_morph
            self.current_before_length += (target_before_length - self.current_before_length) * self.window_morph
            self.current_after_start += (target_after_start - self.current_after_start) * self.window_morph
            self.current_after_length += (target_after_length - self.current_after_length) * self.window_morph
            # TUTORIAL: ------------------------------------------------------------------------
            # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive
            # TUTORIAL: non-finite samples, and table generation can sometimes hit extreme math.
            # TUTORIAL: ------------------------------------------------------------------------
            self.current_table = np.nan_to_num(self.current_table, nan=0.0, posinf=1.0, neginf=-1.0)

        out = np.empty(frames, dtype=np.float32)
        base_phase_inc = self.current_frequency / self.sample_rate
        table = self.current_table
        phase = self.phase
        volume = self.current_volume

        # Self-modulation ranges. These are deliberately bounded so the
        # oscillator remains playable rather than turning into NaN/Inf feedback.
        fm_index = clamp_float(self.current_fm, 0.0, 255.0) / 255.0 * 4.0
        pwm_depth = clamp_float(self.current_pwm, 0.0, 255.0) / 255.0 * 0.48
        am_depth = clamp_float(self.current_am, 0.0, 255.0) / 255.0
        before_start = clamp_float(self.current_before_start, 0.0, WINDOW_STEPS)
        before_length = clamp_float(self.current_before_length, MIN_WINDOW_STEPS, WINDOW_STEPS)
        after_start = clamp_float(self.current_after_start, 0.0, WINDOW_STEPS)
        after_length = clamp_float(self.current_after_length, MIN_WINDOW_STEPS, WINDOW_STEPS)

        for i in range(frames):
            # Before-math windowing happens before the self-modulation math.
            # It changes the table region that FM/PWM/AM listen to and, with
            # the after-window at full range, the region that is heard.
            math_phase = apply_phase_window(phase, before_start, before_length)

            # The oscillator reads itself to create a bipolar modulation signal.
            # AM also shapes this modulation signal, so FM/PWM are less static
            # when AM is raised.
            mod_sample = read_table_linear(table, math_phase)

            # AM self-modulation is unipolar, 1.0 at depth 0 and 0..1 at full depth.
            am_gain = (1.0 - am_depth) + am_depth * (0.5 + 0.5 * mod_sample)
            am_gain = clamp_float(am_gain, 0.0, 1.5)
            mod_signal = clamp_float(mod_sample * am_gain, -1.0, 1.0)

            # PWM on arbitrary wavetables is implemented as phase-width warping:
            # the first and second halves of the cycle are stretched/compressed
            # around a self-modulated width point. At depth 0 this is neutral.
            width = clamp_float(0.5 + pwm_depth * mod_signal, 0.02, 0.98)
            if math_phase < width:
                read_phase = (math_phase / width) * 0.5
            else:
                read_phase = 0.5 + ((math_phase - width) / (1.0 - width)) * 0.5

            # After-math windowing happens after PWM/FM/AM have done their
            # phase-related work, so it can re-frame the final read region.
            read_phase = apply_phase_window(read_phase, after_start, after_length)
            sample = read_table_linear(table, read_phase)
            out[i] = sample * volume * am_gain

            # Self-FM: the waveform modulates its own instantaneous phase increment.
            # Negative increments are clamped away; very large increments are capped.
            fm_scale = clamp_float(1.0 + fm_index * mod_signal, MIN_FM_PHASE_SCALE, 8.0)
            phase_inc = base_phase_inc * fm_scale
            phase += min(phase_inc, 0.49)
            if phase >= 1.0:
                phase -= math.floor(phase)

        self.phase = phase
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
        # TUTORIAL: samples, and table generation can sometimes hit extreme math.
        # TUTORIAL: ------------------------------------------------------------------------
        out = np.nan_to_num(out, nan=0.0, posinf=1.0, neginf=-1.0)
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: Clip the signal/control range after math that might overshoot. This keeps
        # TUTORIAL: audio buffers and tables bounded.
        # TUTORIAL: ------------------------------------------------------------------------
        out = np.clip(out, -1.0, 1.0)
        outdata[:, 0] = out
        if outdata.shape[1] > 1:
            outdata[:, 1] = out


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Original single-screen Tk application. Later sections monkey-patch this class to add
# TUTORIAL: windows, visualiser modes, FM range, audio recovery, and eventually the tabbed
# TUTORIAL: interface.
# TUTORIAL: ------------------------------------------------------------------------
class App:
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function __init__: small named steps make the signal path, UI path, and
    # TUTORIAL: export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def __init__(self, root: tk.Tk, state: SharedState, initial_frequency: float, sample_rate: int):
        self.root = root
        self.state = state
        self.pending = False
        self.message_queue: queue.Queue[str] = queue.Queue()
        self.default_frequency = clamp_float(initial_frequency, MIN_FREQUENCY, MAX_FREQUENCY)
        self.sample_rate = float(sample_rate)
        self.suppress_frequency_events = False
        self.table_notes: list[str] = []
        self.frequency_notes: list[str] = []
        self.window_notes: list[str] = []

        root.title("Fractal Wavetable Live")
        # Wider default layout: the visualiser mode buttons and the FM range
        # selector need more horizontal room than the earlier 1220px window.
        root.geometry("1680x940")
        root.minsize(1500, 900)

        self.main_frame = tk.Frame(root)
        self.main_frame.pack(fill="both", expand=True)
        self.control_parent = tk.Frame(self.main_frame)
        self.control_parent.pack(side="left", fill="both", expand=True)

        # Keep the visualiser column a fixed width. Without this, long mode
        # labels / redraw text can make Tk recalculate the layout and jerk the
        # controller sliders sideways.
        self.wave_canvas_size = 520
        self.visual_panel_width = 780
        self.visual_parent = tk.Frame(self.main_frame, width=self.visual_panel_width)
        self.visual_parent.pack(side="right", fill="y", padx=(8, 12), pady=12)
        self.visual_parent.pack_propagate(False)

        self.visualizer_mode = tk.StringVar(value="base")

        tk.Label(
            self.visual_parent,
            text="Single-cycle waveform",
            anchor="center",
        ).pack(fill="x", pady=(0, 4))

        visual_mode_frame = tk.Frame(self.visual_parent)
        visual_mode_frame.pack(fill="x", pady=(0, 6))
        tk.Radiobutton(
            visual_mode_frame,
            text="Base table",
            variable=self.visualizer_mode,
            value="base",
            command=self._draw_current_waveform,
        ).pack(side="left")
        tk.Radiobutton(
            visual_mode_frame,
            text="Rendered FM/PWM/AM",
            variable=self.visualizer_mode,
            value="rendered",
            command=self._draw_current_waveform,
        ).pack(side="left", padx=(10, 0))
        tk.Radiobutton(
            visual_mode_frame,
            text="Slow FM/PWM/AM",
            variable=self.visualizer_mode,
            value="slow",
            command=self._draw_current_waveform,
        ).pack(side="left", padx=(10, 0))

        self.wave_canvas = tk.Canvas(
            self.visual_parent,
            width=self.wave_canvas_size,
            height=self.wave_canvas_size,
            bg="black",
            highlightthickness=1,
            highlightbackground="#555555",
        )
        self.wave_canvas.pack(anchor="n")
        self.wave_canvas.bind("<Configure>", lambda _event: self._draw_current_waveform())

        self.vars = {
            "pot1": tk.IntVar(value=2048),
            "pot2": tk.IntVar(value=4080),
            "pot3": tk.IntVar(value=160),
            "type": tk.IntVar(value=3),
            "fm": tk.IntVar(value=0),
            "pwm": tk.IntVar(value=0),
            "am": tk.IntVar(value=0),
            "before_start": tk.IntVar(value=0),
            "before_end": tk.IntVar(value=4096),
            "after_start": tk.IntVar(value=0),
            "after_end": tk.IntVar(value=4096),
            "volume": tk.DoubleVar(value=0.20),
        }

        self._add_slider("Pot 1", "pot1", 0, 4095, 1)
        self._add_slider("Pot 2", "pot2", 0, 4095, 1)
        self._add_slider("Pot 3", "pot3", 0, 4095, 1)
        self._add_slider("Wavetable type", "type", 0, WAVETABLE_TYPE_MAX, 1)
        self.type_description = tk.Label(self.control_parent, anchor="w", justify="left", wraplength=860, text="")
        self.type_description.pack(fill="x", padx=12, pady=(0, 4))
        self._update_type_description()
        self._add_frequency_controls(self.default_frequency)
        self._add_fm_modulation_controls()
        self._add_modulation_slider("PWM self", "pwm")
        self._add_modulation_slider("AM self", "am")
        self._add_window_slider("Source Window Start", "before_start")
        self._add_window_slider("Source Window End", "before_end")
        self._add_window_slider("Result Window Start", "after_start")
        self._add_window_slider("Result Window End", "after_end")
        self._add_slider("Volume", "volume", 0.0, 1.0, 0.01)

        button_frame = tk.Frame(self.control_parent)
        button_frame.pack(fill="x", padx=12, pady=(6, 0))

        reset_all_button = tk.Button(
            button_frame,
            text="Reset all",
            command=self.reset_all,
        )
        reset_all_button.pack(side="left")

        reset_generator_button = tk.Button(
            button_frame,
            text="Reset generator",
            command=self.reset_generator,
        )
        reset_generator_button.pack(side="left", padx=(8, 0))

        panic_button = tk.Button(
            button_frame,
            text="Panic audio",
            command=self.panic_audio,
        )
        panic_button.pack(side="left", padx=(8, 0))

        indicator_frame = tk.Frame(self.control_parent)
        indicator_frame.pack(fill="x", padx=12, pady=(8, 0))

        self.clip_light = tk.Label(
            indicator_frame,
            text="●",
            font=("TkDefaultFont", 18, "bold"),
            fg="#555555",
        )
        self.clip_light.pack(side="left")

        self.clip_text = tk.Label(
            indicator_frame,
            anchor="w",
            text="No clamp",
        )
        self.clip_text.pack(side="left", padx=(8, 0), fill="x", expand=True)

        self.status = tk.Label(self.control_parent, anchor="w", text="Move sliders while audio is running.")
        self.status.pack(fill="x", padx=12, pady=(8, 0))

        self.update_table()
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: Tk uses scheduled callbacks instead of long blocking loops. after(...) lets
        # TUTORIAL: the UI stay responsive.
        # TUTORIAL: ------------------------------------------------------------------------
        self.root.after(100, self._poll_messages)

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Export helper: _draw_current_waveform turns in-memory rendered audio/settings
    # TUTORIAL: into files on disk. This is separate from live playback by design.
    # TUTORIAL: ------------------------------------------------------------------------
    def _draw_current_waveform(self) -> None:
        if not hasattr(self, "wave_canvas"):
            return
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
        # TUTORIAL: callback may touch it at the same time.
        # TUTORIAL: ------------------------------------------------------------------------
        with self.state.lock:
            table = self.state.target_table.copy()
        self._draw_waveform(table)

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Export helper: _draw_waveform turns in-memory rendered audio/settings into files
    # TUTORIAL: on disk. This is separate from live playback by design.
    # TUTORIAL: ------------------------------------------------------------------------
    def _draw_waveform(self, table: np.ndarray) -> None:
        if not hasattr(self, "wave_canvas"):
            return

        canvas = self.wave_canvas
        width = max(int(canvas.winfo_width()), int(self.wave_canvas_size))
        height = max(int(canvas.winfo_height()), int(self.wave_canvas_size))
        pad = 18
        plot_w = max(width - 2 * pad, 1)
        plot_h = max(height - 2 * pad, 1)
        mid_y = pad + plot_h / 2.0
        amp = plot_h * 0.46

        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
        # TUTORIAL: samples, and table generation can sometimes hit extreme math.
        # TUTORIAL: ------------------------------------------------------------------------
        y = np.nan_to_num(np.asarray(table, dtype=np.float64), nan=0.0, posinf=1.0, neginf=-1.0)
        if y.shape != (TABLE_SIZE,):
            y = fallback_table().astype(np.float64)
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: Clip the signal/control range after math that might overshoot. This keeps
        # TUTORIAL: audio buffers and tables bounded.
        # TUTORIAL: ------------------------------------------------------------------------
        y = np.clip(y, -1.0, 1.0)

        canvas.delete("all")
        canvas.create_rectangle(pad, pad, pad + plot_w, pad + plot_h, outline="#444444")
        canvas.create_line(pad, mid_y, pad + plot_w, mid_y, fill="#333333")
        canvas.create_line(pad, pad, pad + plot_w, pad, fill="#222222")
        canvas.create_line(pad, pad + plot_h, pad + plot_w, pad + plot_h, fill="#222222")

        points: list[float] = []
        for i in range(TABLE_SIZE + 1):
            sample = float(y[i % TABLE_SIZE])
            x = pad + plot_w * (i / TABLE_SIZE)
            yy = mid_y - sample * amp
            points.extend((x, yy))
        canvas.create_line(*points, fill="#00d0ff", width=2, smooth=False)

        canvas.create_text(
            pad,
            height - 6,
            anchor="sw",
            fill="#999999",
            text="one cycle, final generated table",
        )

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: UI construction helper: _add_slider creates a reusable group of Tk controls.
    # TUTORIAL: Keeping these small avoids one enormous constructor.
    # TUTORIAL: ------------------------------------------------------------------------
    def _add_slider(self, label: str, key: str, lo: float, hi: float, resolution: float) -> None:
        frame = tk.Frame(self.control_parent)
        frame.pack(fill="x", padx=12, pady=4)
        tk.Label(frame, text=label, width=16, anchor="w").pack(side="left")
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: Tk Scale is the slider widget. In this app, slider callbacks push new values
        # TUTORIAL: into patch or audio state.
        # TUTORIAL: ------------------------------------------------------------------------
        slider = tk.Scale(
            frame,
            variable=self.vars[key],
            from_=lo,
            to=hi,
            resolution=resolution,
            orient="horizontal",
            length=430,
            command=lambda _value: self.schedule_update(),
        )
        slider.pack(side="left", fill="x", expand=True)

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: UI construction helper: _add_modulation_slider creates a reusable group of Tk
    # TUTORIAL: controls. Keeping these small avoids one enormous constructor.
    # TUTORIAL: ------------------------------------------------------------------------
    def _add_modulation_slider(self, label: str, key: str) -> None:
        frame = tk.Frame(self.control_parent)
        frame.pack(fill="x", padx=12, pady=4)
        tk.Label(frame, text=label, width=16, anchor="w").pack(side="left")
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: Tk Scale is the slider widget. In this app, slider callbacks push new values
        # TUTORIAL: into patch or audio state.
        # TUTORIAL: ------------------------------------------------------------------------
        slider = tk.Scale(
            frame,
            variable=self.vars[key],
            from_=0,
            to=255,
            resolution=1,
            orient="horizontal",
            length=430,
            command=lambda _value: self._push_modulation(source=key),
        )
        slider.pack(side="left", fill="x", expand=True)

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: UI construction helper: _add_window_slider creates a reusable group of Tk
    # TUTORIAL: controls. Keeping these small avoids one enormous constructor.
    # TUTORIAL: ------------------------------------------------------------------------
    def _add_window_slider(self, label: str, key: str) -> None:
        frame = tk.Frame(self.control_parent)
        frame.pack(fill="x", padx=12, pady=4)
        tk.Label(frame, text=label, width=16, anchor="w").pack(side="left")
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: Tk Scale is the slider widget. In this app, slider callbacks push new values
        # TUTORIAL: into patch or audio state.
        # TUTORIAL: ------------------------------------------------------------------------
        slider = tk.Scale(
            frame,
            variable=self.vars[key],
            from_=0,
            to=int(WINDOW_STEPS),
            resolution=1,
            orient="horizontal",
            length=430,
            command=lambda _value: self._push_windows(source=key),
        )
        slider.pack(side="left", fill="x", expand=True)

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function _push_modulation: small named steps make the signal path, UI
    # TUTORIAL: path, and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def _push_modulation(self, *, source: str = "modulation") -> None:
        fm = clamp_float(float(self.vars["fm"].get()), 0.0, 255.0)
        pwm = clamp_float(float(self.vars["pwm"].get()), 0.0, 255.0)
        am = clamp_float(float(self.vars["am"].get()), 0.0, 255.0)
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
        # TUTORIAL: callback may touch it at the same time.
        # TUTORIAL: ------------------------------------------------------------------------
        with self.state.lock:
            self.state.target_fm = fm
            self.state.target_pwm = pwm
            self.state.target_am = am
        if hasattr(self, "clip_light"):
            self._push_windows(source=source, update_status=False)
        if hasattr(self, "status"):
            self.status.config(text=f"MOD {source}  fm={fm:.0f}  pwm={pwm:.0f}  am={am:.0f}")

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function _push_windows: small named steps make the signal path, UI path,
    # TUTORIAL: and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def _push_windows(self, *, source: str = "window", update_status: bool = True) -> None:
        before_start_req = float(self.vars["before_start"].get())
        before_end_req = float(self.vars["before_end"].get())
        after_start_req = float(self.vars["after_start"].get())
        after_end_req = float(self.vars["after_end"].get())

        before_start, before_length, before_notes = clamp_window_pair(
            before_start_req, before_end_req, "before"
        )
        after_start, after_length, after_notes = clamp_window_pair(
            after_start_req, after_end_req, "after"
        )
        notes = before_notes + after_notes

        before_start, before_length, after_start, after_length, output_notes = self._apply_output_safety_to_windows(
            before_start, before_length, after_start, after_length
        )
        notes += output_notes

        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
        # TUTORIAL: callback may touch it at the same time.
        # TUTORIAL: ------------------------------------------------------------------------
        with self.state.lock:
            self.state.target_before_start = before_start
            self.state.target_before_length = before_length
            self.state.target_after_start = after_start
            self.state.target_after_length = after_length

        self.window_notes = notes
        if hasattr(self, "clip_light"):
            self._refresh_clip_indicator()
        if update_status and hasattr(self, "status"):
            self.status.config(
                text=(
                    f"WINDOW {source}  before={before_start_req:.0f}→{before_end_req:.0f} "
                    f"after={after_start_req:.0f}→{after_end_req:.0f}"
                )
            )

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function _apply_output_safety_to_windows: small named steps make the
    # TUTORIAL: signal path, UI path, and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def _apply_output_safety_to_windows(
        self,
        before_start: float,
        before_length: float,
        after_start: float,
        after_length: float,
    ) -> tuple[float, float, float, float, list[str]]:
        """Clamp effective windows if the current audio path becomes unsafe.

        This is deliberately based on the rendered result, not only slider sanity.
        Some sane-looking windows can still land on a near-flat part of a given
        wave+FM/PWM/AM combination. In that case, widen the effective windows
        until the preview is finite and audible. The sliders remain unchanged;
        the red indicator reports the intervention.
        """

        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
        # TUTORIAL: callback may touch it at the same time.
        # TUTORIAL: ------------------------------------------------------------------------
        with self.state.lock:
            table = self.state.target_table.copy()
            volume = float(self.state.target_volume)
            frequency = float(self.state.target_frequency)
            fm = float(self.state.target_fm)
            pwm = float(self.state.target_pwm)
            am = float(self.state.target_am)

        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: Helper function stats: small named steps make the signal path, UI path, and
        # TUTORIAL: export path easier to inspect and test.
        # TUTORIAL: ------------------------------------------------------------------------
        def stats(bs: float, bl: float, a_s: float, al: float) -> tuple[bool, float, float]:
            return preview_output_stats(
                table,
                volume=volume,
                frequency=frequency,
                fm=fm,
                pwm=pwm,
                am=am,
                before_start=bs,
                before_length=bl,
                after_start=a_s,
                after_length=al,
                sample_rate=self.sample_rate,
            )

        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: Helper function is_audible: small named steps make the signal path, UI path,
        # TUTORIAL: and export path easier to inspect and test.
        # TUTORIAL: ------------------------------------------------------------------------
        def is_audible(finite: bool, peak: float, rms: float) -> bool:
            if volume <= 1e-6:
                return True
            return finite and peak >= OUTPUT_SILENCE_PEAK and rms >= OUTPUT_SILENCE_RMS

        finite, peak, rms = stats(before_start, before_length, after_start, after_length)
        notes: list[str] = []

        if not is_audible(finite, peak, rms):
            candidates = [
                (before_start, before_length, 0.0, WINDOW_STEPS, "after window widened to full"),
                (0.0, WINDOW_STEPS, after_start, after_length, "before window widened to full"),
                (0.0, WINDOW_STEPS, 0.0, WINDOW_STEPS, "before+after windows widened to full"),
            ]
            for bs, bl, a_s, al, label in candidates:
                c_finite, c_peak, c_rms = stats(bs, bl, a_s, al)
                if is_audible(c_finite, c_peak, c_rms):
                    before_start, before_length, after_start, after_length = bs, bl, a_s, al
                    finite, peak, rms = c_finite, c_peak, c_rms
                    notes.append(f"silent/invalid output; {label}")
                    break
            else:
                before_start, before_length, after_start, after_length = 0.0, WINDOW_STEPS, 0.0, WINDOW_STEPS
                finite, peak, rms = stats(before_start, before_length, after_start, after_length)
                notes.append("silent/invalid output; full windows forced")

        if not finite:
            notes.append("non-finite output sanitised")
        if peak > OUTPUT_CLIP_PEAK:
            notes.append(f"audio peak clipped {peak:.2f}→1.00")

        return before_start, before_length, after_start, after_length, notes

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function _update_type_description: small named steps make the signal
    # TUTORIAL: path, UI path, and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def _update_type_description(self) -> None:
        if not hasattr(self, "type_description"):
            return
        wt_type = clamp_int(int(self.vars["type"].get()), 0, WAVETABLE_TYPE_MAX)
        self.type_description.config(text=WAVETABLE_DESCRIPTIONS.get(wt_type, "Unknown wavetable type."))

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: UI construction helper: _add_frequency_controls creates a reusable group of Tk
    # TUTORIAL: controls. Keeping these small avoids one enormous constructor.
    # TUTORIAL: ------------------------------------------------------------------------
    def _add_frequency_controls(self, initial_frequency: float) -> None:
        frame = tk.Frame(self.control_parent)
        frame.pack(fill="x", padx=12, pady=4)

        tk.Label(frame, text="Frequency", width=16, anchor="w").pack(side="left")

        self.frequency_slider_var = tk.DoubleVar(value=frequency_to_slider_value(initial_frequency))
        self.frequency_entry_var = tk.StringVar(value=f"{initial_frequency:.3f}")

        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: Tk Scale is the slider widget. In this app, slider callbacks push new values
        # TUTORIAL: into patch or audio state.
        # TUTORIAL: ------------------------------------------------------------------------
        slider = tk.Scale(
            frame,
            variable=self.frequency_slider_var,
            from_=0.0,
            to=1.0,
            resolution=0.0001,
            showvalue=False,
            orient="horizontal",
            length=430,
            command=self._frequency_slider_changed,
        )
        slider.pack(side="left", fill="x", expand=True)

        entry = tk.Entry(frame, textvariable=self.frequency_entry_var, width=10, justify="right")
        entry.pack(side="left", padx=(8, 0))
        entry.bind("<Return>", self._frequency_entry_committed)
        entry.bind("<FocusOut>", self._frequency_entry_committed)

        tk.Label(frame, text="Hz").pack(side="left", padx=(4, 0))

        note_frame = tk.Frame(self.control_parent)
        note_frame.pack(fill="x", padx=12, pady=4)

        tk.Label(note_frame, text="Note", width=16, anchor="w").pack(side="left")
        initial_note, initial_octave = nearest_note_for_frequency(initial_frequency)
        self.note_var = tk.StringVar(value=initial_note)
        self.octave_var = tk.StringVar(value=str(initial_octave))

        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: OptionMenu is used for compact dropdowns such as wavetable type, pitch
        # TUTORIAL: range, and export settings.
        # TUTORIAL: ------------------------------------------------------------------------
        note_menu = tk.OptionMenu(note_frame, self.note_var, *NOTE_NAMES, command=self._note_selection_changed)
        note_menu.config(width=4)
        note_menu.pack(side="left")

        tk.Label(note_frame, text="Octave").pack(side="left", padx=(10, 4))
        octave_values = [str(i) for i in range(0, 11)]
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: OptionMenu is used for compact dropdowns such as wavetable type, pitch
        # TUTORIAL: range, and export settings.
        # TUTORIAL: ------------------------------------------------------------------------
        octave_menu = tk.OptionMenu(note_frame, self.octave_var, *octave_values, command=self._note_selection_changed)
        octave_menu.config(width=4)
        octave_menu.pack(side="left")

        self.note_frequency_label = tk.Label(note_frame, anchor="w", text="")
        self.note_frequency_label.pack(side="left", padx=(12, 0), fill="x", expand=True)
        self._update_note_label(initial_frequency)

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function _frequency_slider_changed: small named steps make the signal
    # TUTORIAL: path, UI path, and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def _frequency_slider_changed(self, _value: str) -> None:
        if self.suppress_frequency_events:
            return
        frequency = slider_value_to_frequency(float(self.frequency_slider_var.get()))
        self._push_frequency(frequency, update_ui=True, hard_reset=False, source="slider")

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function _frequency_entry_committed: small named steps make the signal
    # TUTORIAL: path, UI path, and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def _frequency_entry_committed(self, _event=None) -> None:
        try:
            frequency = float(self.frequency_entry_var.get())
        except ValueError:
            # TUTORIAL: ------------------------------------------------------------------------
            # TUTORIAL: SharedState is read or written under a lock because the GUI thread and
            # TUTORIAL: audio callback may touch it at the same time.
            # TUTORIAL: ------------------------------------------------------------------------
            with self.state.lock:
                frequency = float(self.state.target_frequency)
            self._push_frequency(frequency, update_ui=True, hard_reset=False, source="entry")
            return
        self._push_frequency(frequency, update_ui=True, hard_reset=False, source="entry")

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function _note_selection_changed: small named steps make the signal path,
    # TUTORIAL: UI path, and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def _note_selection_changed(self, _value: str | None = None) -> None:
        if self.suppress_frequency_events:
            return
        frequency = note_to_frequency(self.note_var.get(), int(self.octave_var.get()))
        self._push_frequency(frequency, update_ui=True, hard_reset=False, source="note")

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function _set_frequency_widgets: small named steps make the signal path,
    # TUTORIAL: UI path, and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def _set_frequency_widgets(self, frequency: float, *, update_note_label: bool = True) -> None:
        self.suppress_frequency_events = True
        try:
            self.frequency_slider_var.set(frequency_to_slider_value(frequency))
            self.frequency_entry_var.set(f"{frequency:.3f}")
            if update_note_label:
                self._update_note_label(frequency)
        finally:
            self.suppress_frequency_events = False

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function _update_note_label: small named steps make the signal path, UI
    # TUTORIAL: path, and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def _update_note_label(self, frequency: float) -> None:
        note, octave = nearest_note_for_frequency(frequency)
        self.note_frequency_label.config(text=f"nearest: {note}{octave}  ({frequency:.3f} Hz)")

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function _push_frequency: small named steps make the signal path, UI
    # TUTORIAL: path, and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def _push_frequency(
        self,
        frequency: float,
        *,
        update_ui: bool,
        hard_reset: bool,
        source: str = "frequency",
    ) -> float:
        clipped_frequency, notes = clamp_frequency(frequency)

        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
        # TUTORIAL: callback may touch it at the same time.
        # TUTORIAL: ------------------------------------------------------------------------
        with self.state.lock:
            self.state.target_frequency = clipped_frequency
            if hard_reset:
                self.state.reset_requested = True

        if update_ui:
            self._set_frequency_widgets(clipped_frequency)

        self.frequency_notes = notes
        if hasattr(self, "clip_light"):
            self._push_windows(source=source, update_status=False)
            self._refresh_clip_indicator()
        if hasattr(self, "status"):
            self.status.config(text=f"FREQ {source}  frequency={clipped_frequency:.3f} Hz")
        return clipped_frequency

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function panic_audio: small named steps make the signal path, UI path,
    # TUTORIAL: and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def panic_audio(self) -> None:
        """Force the audio callback back to a known-good audible state.

        This is deliberately stronger than Reset generator: it does not rely on
        current sliders, modulation, or the current table. It pushes a square
        table, neutral modulation, full generation windows, and the default
        pitch/volume directly into the shared audio state.
        """
        table = fallback_table()
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
        # TUTORIAL: callback may touch it at the same time.
        # TUTORIAL: ------------------------------------------------------------------------
        with self.state.lock:
            self.state.target_table = table
            self.state.target_volume = 0.20
            self.state.target_frequency = self.default_frequency
            self.state.target_fm = 0.0
            self.state.target_pwm = 0.0
            self.state.target_am = 0.0
            self.state.target_before_start = 0.0
            self.state.target_before_length = WINDOW_STEPS
            self.state.target_after_start = 0.0
            self.state.target_after_length = WINDOW_STEPS
            self.state.reset_requested = True
        self.status.config(text="PANIC AUDIO  fallback square + neutral modulation pushed to audio engine")
        self.clip_light.config(fg="red")
        self.clip_text.config(text="CLAMP: panic audio reset used")
        self._draw_waveform(table)

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function reset_all: small named steps make the signal path, UI path, and
    # TUTORIAL: export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def reset_all(self) -> None:
        p1 = 2048
        p2 = 4080
        p3 = 160
        wt_type = 3
        fm = 0
        pwm = 0
        am = 0
        before_start = 0
        before_end = 4096
        after_start = 0
        after_end = 4096
        volume = 0.20
        frequency = self.default_frequency

        self.vars["pot1"].set(p1)
        self.vars["pot2"].set(p2)
        self.vars["pot3"].set(p3)
        self.vars["type"].set(wt_type)
        self.vars["fm"].set(fm)
        self.vars["pwm"].set(pwm)
        self.vars["am"].set(am)
        self.vars["before_start"].set(before_start)
        self.vars["before_end"].set(before_end)
        self.vars["after_start"].set(after_start)
        self.vars["after_end"].set(after_end)
        self.vars["volume"].set(volume)
        self._push_modulation(source="reset")
        self._push_windows(source="reset")
        self._update_type_description()
        self._set_frequency_widgets(frequency)
        self._push_frequency(frequency, update_ui=False, hard_reset=False, source="reset")

        ok, effective, notes = self._push_table(p1, p2, p3, wt_type, volume, hard_reset=True)
        if ok:
            self.status.config(text=self._status_text("RESET ALL", (p1, p2, p3, wt_type, volume), effective, notes))

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function reset_generator: small named steps make the signal path, UI
    # TUTORIAL: path, and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def reset_generator(self) -> None:
        requested = self._current_values()
        ok, effective, notes = self._push_table(*requested, hard_reset=True)
        if ok:
            self.status.config(text=self._status_text("GENERATOR RESET", requested, effective, notes))

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function _current_values: small named steps make the signal path, UI
    # TUTORIAL: path, and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def _current_values(self) -> tuple[int, int, int, int, float]:
        return (
            int(self.vars["pot1"].get()),
            int(self.vars["pot2"].get()),
            int(self.vars["pot3"].get()),
            int(self.vars["type"].get()),
            float(self.vars["volume"].get()),
        )

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function _refresh_clip_indicator: small named steps make the signal path,
    # TUTORIAL: UI path, and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def _refresh_clip_indicator(self) -> None:
        notes = self.table_notes + self.frequency_notes + self.window_notes
        if notes:
            self.clip_light.config(fg="red")
            self.clip_text.config(text="CLAMP: " + "; ".join(notes))
        else:
            self.clip_light.config(fg="#555555")
            self.clip_text.config(text="No clamp")

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function _status_text: small named steps make the signal path, UI path,
    # TUTORIAL: and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def _status_text(
        self,
        prefix: str,
        requested: tuple[int, int, int, int, float],
        effective: tuple[int, int, int, int, float],
        notes: list[str],
    ) -> str:
        req_p1, req_p2, req_p3, req_type, req_volume = requested
        eff_p1, eff_p2, eff_p3, eff_type, eff_volume = effective

        if notes:
            return (
                f"{prefix}  REQUEST p1={req_p1} p2={req_p2} p3={req_p3} "
                f"type={req_type} volume={req_volume:.2f}  |  "
                f"USED p1={eff_p1} p2={eff_p2} p3={eff_p3} "
                f"type={eff_type} volume={eff_volume:.2f}"
            )

        return (
            f"{prefix}  pot1={eff_p1}  pot2={eff_p2}  pot3={eff_p3}  "
            f"type={eff_type}  volume={eff_volume:.2f}"
        )

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function _push_table: small named steps make the signal path, UI path,
    # TUTORIAL: and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def _push_table(
        self,
        p1: int,
        p2: int,
        p3: int,
        wt_type: int,
        volume: float,
        *,
        hard_reset: bool,
    ) -> tuple[bool, tuple[int, int, int, int, float], list[str]]:
        eff_p1, eff_p2, eff_p3, eff_type, eff_volume, notes = clamp_control_values(
            p1, p2, p3, wt_type, volume
        )
        effective = (eff_p1, eff_p2, eff_p3, eff_type, eff_volume)

        table = generate_wavetable(eff_p1, eff_p2, eff_p3, eff_type)
        if not table_is_usable(table):
            notes = notes + ["unsafe table ignored"]
            self.table_notes = notes
            self._refresh_clip_indicator()
            self.status.config(
                text=f"UNSAFE TABLE IGNORED  pot1={p1}  pot2={p2}  pot3={p3}  type={wt_type}"
            )
            return False, effective, notes

        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
        # TUTORIAL: callback may touch it at the same time.
        # TUTORIAL: ------------------------------------------------------------------------
        with self.state.lock:
            self.state.target_table = table
            self.state.target_volume = eff_volume
            if hard_reset:
                self.state.reset_requested = True

        self.pending = False
        self.table_notes = notes
        if hasattr(self, "clip_light"):
            self._push_windows(source="table", update_status=False)
        else:
            self._refresh_clip_indicator()
        return True, effective, notes

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function schedule_update: small named steps make the signal path, UI
    # TUTORIAL: path, and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def schedule_update(self) -> None:
        self._update_type_description()
        if not self.pending:
            self.pending = True
            # TUTORIAL: ------------------------------------------------------------------------
            # TUTORIAL: Tk uses scheduled callbacks instead of long blocking loops. after(...)
            # TUTORIAL: lets the UI stay responsive.
            # TUTORIAL: ------------------------------------------------------------------------
            self.root.after_idle(self.update_table)

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function update_table: small named steps make the signal path, UI path,
    # TUTORIAL: and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def update_table(self) -> None:
        self.pending = False
        self._update_type_description()
        requested = self._current_values()
        ok, effective, notes = self._push_table(*requested, hard_reset=False)
        if ok:
            self.status.config(text=self._status_text("LIVE", requested, effective, notes))

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function _poll_messages: small named steps make the signal path, UI path,
    # TUTORIAL: and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def _poll_messages(self) -> None:
        while True:
            try:
                msg = self.message_queue.get_nowait()
            except queue.Empty:
                break
            self.status.config(text=msg)
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: Tk uses scheduled callbacks instead of long blocking loops. after(...) lets
        # TUTORIAL: the UI stay responsive.
        # TUTORIAL: ------------------------------------------------------------------------
        self.root.after(100, self._poll_messages)



# -----------------------------------------------------------------------------
# Window-as-generation-stage override
#
# In the first windowed version, before/after windows were applied inside the
# playback oscillator. That made legal-looking settings silent when the playback
# phase was forced to read a flat part of the table. This override moves the
# windows into wavetable generation instead:
#
#   before window: remap iter2/iter3 before the wavetable type combines them
#   after window:  remap the selected/combined raw table before final normalise
#
# The audio callback is left alone, but the GUI no longer writes non-full window
# values into SharedState, so the playback oscillator keeps full-cycle reading.
# -----------------------------------------------------------------------------

LAST_WAVETABLE_NOTES: list[str] = []


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function is_full_window: small named steps make the signal path, UI path, and
# TUTORIAL: export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def is_full_window(start_steps: float, length_steps: float) -> bool:
    return abs((float(start_steps) % WINDOW_STEPS)) <= 1e-9 and abs(float(length_steps) - WINDOW_STEPS) <= 1e-9


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function window_table_linear: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def window_table_linear(table: np.ndarray, start_steps: float, length_steps: float) -> np.ndarray:
    """Return a 256-sample table that scans a selected source window.

    The result is still a full 256-sample wavetable. The window does not mute
    playback; it remaps the source phase used to construct the table. Wraparound
    is allowed, so start=3000/end=1000 means the source region crosses 4096→0.
    """

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
    # TUTORIAL: samples, and table generation can sometimes hit extreme math.
    # TUTORIAL: ------------------------------------------------------------------------
    src = np.nan_to_num(table.astype(WORK_DTYPE), nan=0.0, posinf=0.0, neginf=0.0)
    start = (float(start_steps) % WINDOW_STEPS) / WINDOW_STEPS
    length = clamp_float(float(length_steps), MIN_WINDOW_STEPS, WINDOW_STEPS) / WINDOW_STEPS

    phases = (start + (np.arange(TABLE_SIZE, dtype=WORK_DTYPE) / TABLE_SIZE) * length) % 1.0
    indexes = phases * TABLE_SIZE
    i0 = np.floor(indexes).astype(np.int64) & (TABLE_SIZE - 1)
    i1 = (i0 + 1) & (TABLE_SIZE - 1)
    frac = indexes - np.floor(indexes)
    return src[i0] + (src[i1] - src[i0]) * frac


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function finalise_raw_table_checked: small named steps make the signal path,
# TUTORIAL: UI path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def finalise_raw_table_checked(raw: np.ndarray) -> np.ndarray:
    """Universal final safety and audio conversion, with UI notes."""
    global LAST_WAVETABLE_NOTES

    notes: list[str] = []
    raw_arr = np.asarray(raw, dtype=WORK_DTYPE)
    if raw_arr.shape != (TABLE_SIZE,):
        notes.append(f"raw table shape {raw_arr.shape}→fallback square")
        LAST_WAVETABLE_NOTES += notes
        return fallback_table()

    if not np.all(np.isfinite(raw_arr)):
        notes.append("non-finite table values sanitised")
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
    # TUTORIAL: samples, and table generation can sometimes hit extreme math.
    # TUTORIAL: ------------------------------------------------------------------------
    y = np.nan_to_num(raw_arr, nan=0.0, posinf=0.0, neginf=0.0)

    y -= float(np.mean(y))
    peak = float(np.max(np.abs(y)))
    if (not math.isfinite(peak)) or peak <= EPSILON:
        notes.append("table collapsed to silence; fallback square used")
        LAST_WAVETABLE_NOTES += notes
        return fallback_table()

    y /= peak
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
    # TUTORIAL: samples, and table generation can sometimes hit extreme math.
    # TUTORIAL: ------------------------------------------------------------------------
    table = np.nan_to_num(y, nan=0.0, posinf=1.0, neginf=-1.0).astype(np.float32)
    if not table_is_usable(table):
        notes.append("unsafe final table; fallback square used")
        LAST_WAVETABLE_NOTES += notes
        return fallback_table()

    LAST_WAVETABLE_NOTES += notes
    return table


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: The core generator. It builds the iter2 and iter3 recurrence arrays, then combines
# TUTORIAL: or selects them according to wavetable type.
# TUTORIAL: Later in the file this function name is redefined to add generation-stage windows
# TUTORIAL: and 12-bit pot behaviour. That layered style is why this script works like a visible
# TUTORIAL: development history.
# TUTORIAL: ------------------------------------------------------------------------
def generate_wavetable(
    pot1: int,
    pot2: int,
    pot3: int,
    wt_type: int,
    before_start: float = 0.0,
    before_length: float = WINDOW_STEPS,
    after_start: float = 0.0,
    after_length: float = WINDOW_STEPS,
) -> np.ndarray:
    """Generate one 256-sample bipolar wavetable.

    Window semantics:
      before window = source-window iter2/iter3 before wavetable-type math
      after window  = source-window the selected raw table before final normalise
    """
    global LAST_WAVETABLE_NOTES
    LAST_WAVETABLE_NOTES = []

    pot1 = clamp_int(pot1, 1, 255)
    pot2 = clamp_int(pot2, 0, 255)
    pot3 = clamp_int(pot3, 0, 255)
    wt_type = clamp_int(wt_type, 0, WAVETABLE_TYPE_MAX)

    max_val = 0.0
    max_val2 = 0.0
    temp = np.zeros(TABLE_SIZE, dtype=WORK_DTYPE)
    iter2 = np.zeros(TABLE_SIZE, dtype=WORK_DTYPE)
    iter3 = np.zeros(TABLE_SIZE, dtype=WORK_DTYPE)

    seed0 = (float(pot1) / 256.0) * 2.0
    seed1 = (float(pot2) / 256.0) * 2.0
    seed2 = (float(pot3) / 256.0) * 2.0

    iter2_multiplier = seed1 * seed0
    if wt_type in WT_TYPES_USING_ITER2 and iter2_multiplier > SAFE_ITER2_MULTIPLIER:
        iter2_multiplier = SAFE_ITER2_MULTIPLIER

    temp[0] = seed0 * seed0
    temp[1] = iter2_multiplier
    temp[2] = seed2 * seed0

    iter2[0:3] = temp[0:3]
    for i in range(3, TABLE_SIZE):
        iter2[i] = iter2[i - 3] * iter2[1]

    iter3[0:9] = iter2[0:9]
    for i in range(9, TABLE_SIZE):
        iter3[i] = iter3[i - 9] * iter3[2]

    # BEFORE MATH: remap each internal source before wavetable type selection
    # or combination. This is no longer a playback gate.
    if not is_full_window(before_start, before_length):
        iter2 = window_table_linear(iter2, before_start, before_length)
        iter3 = window_table_linear(iter3, before_start, before_length)

    if wt_type == 0:
        raw = iter2

    elif wt_type == 1:
        raw = iter3

    elif wt_type in (2, 3):
        skip = 0
        for j in range(TABLE_SIZE):
            if wt_type == 2:
                if j < 128:
                    temp[j] = iter3[skip]
                    max_val = max(max_val, float(temp[j]))
                else:
                    temp[j] = iter2[skip - 256]
                    max_val2 = max(max_val2, float(temp[j]))
            else:
                if j < 128:
                    temp[j] = iter2[skip]
                    max_val = max(max_val, float(temp[j]))
                else:
                    temp[j] = iter3[skip - 256]
                    max_val2 = max(max_val2, float(temp[j]))
            skip += 2

        out = np.zeros(TABLE_SIZE, dtype=WORK_DTYPE)
        if math.isfinite(max_val) and max_val > EPSILON:
            out[:128] = (temp[:128] / max_val) * 256.0
        if math.isfinite(max_val2) and max_val2 > EPSILON:
            out[128:] = (temp[128:] / max_val2) * 256.0
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
        # TUTORIAL: samples, and table generation can sometimes hit extreme math.
        # TUTORIAL: ------------------------------------------------------------------------
        raw = (np.nan_to_num(out, nan=0.0, posinf=256.0, neginf=0.0) / 128.0) - 1.0

    else:
        a = normalise_bipolar(iter2)
        b = normalise_bipolar(iter3)

        if wt_type == 4:
            raw = a + b
        elif wt_type == 5:
            raw = a - b
        elif wt_type == 6:
            raw = a * b
        elif wt_type == 7:
            raw = a / np.maximum(np.abs(b), 0.05)
        elif wt_type == 8:
            raw = u8_to_bipolar(bipolar_to_u8(a) | bipolar_to_u8(b))
        elif wt_type == 9:
            raw = u8_to_bipolar(bipolar_to_u8(a) ^ bipolar_to_u8(b))
        else:
            raw = iter2

    # AFTER MATH: remap the final raw table before DC removal/normalisation.
    if not is_full_window(after_start, after_length):
        raw = window_table_linear(raw, after_start, after_length)

    return finalise_raw_table_checked(raw)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _new_push_windows: small named steps make the signal path, UI path,
# TUTORIAL: and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _new_push_windows(self, *, source: str = "window", update_status: bool = True) -> None:
    """Windows are now table-generation controls, not audio-playback controls."""
    if source in {"before_start", "before_end", "after_start", "after_end", "window", "reset", "table"}:
        self.schedule_update()

    if update_status and hasattr(self, "status"):
        self.status.config(
            text=(
                f"WINDOW {source}  before={float(self.vars['before_start'].get()):.0f}→{float(self.vars['before_end'].get()):.0f} "
                f"after={float(self.vars['after_start'].get()):.0f}→{float(self.vars['after_end'].get()):.0f}"
            )
        )


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _new_push_table: small named steps make the signal path, UI path,
# TUTORIAL: and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _new_push_table(
    self,
    p1: int,
    p2: int,
    p3: int,
    wt_type: int,
    volume: float,
    *,
    hard_reset: bool,
) -> tuple[bool, tuple[int, int, int, int, float], list[str]]:
    eff_p1, eff_p2, eff_p3, eff_type, eff_volume, notes = clamp_control_values(
        p1, p2, p3, wt_type, volume
    )
    effective = (eff_p1, eff_p2, eff_p3, eff_type, eff_volume)

    before_start, before_length, before_notes = clamp_window_pair(
        float(self.vars["before_start"].get()), float(self.vars["before_end"].get()), "before"
    )
    after_start, after_length, after_notes = clamp_window_pair(
        float(self.vars["after_start"].get()), float(self.vars["after_end"].get()), "after"
    )
    window_notes = before_notes + after_notes

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function build: small named steps make the signal path, UI path, and
    # TUTORIAL: export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def build(bs: float, bl: float, a_s: float, al: float) -> tuple[np.ndarray, list[str]]:
        table = generate_wavetable(
            eff_p1,
            eff_p2,
            eff_p3,
            eff_type,
            before_start=bs,
            before_length=bl,
            after_start=a_s,
            after_length=al,
        )
        return table, list(LAST_WAVETABLE_NOTES)

    table, gen_notes = build(before_start, before_length, after_start, after_length)

    # Safety based on the generated table, not only on slider geometry. Because
    # finalise_raw_table_checked falls back to a square wave for unusable raw material,
    # its note tells us when to widen/bypass effective windows.
    if any("fallback square" in note or "fallback sine" in note or "collapsed" in note for note in gen_notes):
        candidates = [
            (before_start, before_length, 0.0, WINDOW_STEPS, "after generation-window widened to full"),
            (0.0, WINDOW_STEPS, after_start, after_length, "before generation-window widened to full"),
            (0.0, WINDOW_STEPS, 0.0, WINDOW_STEPS, "before+after generation-windows widened to full"),
        ]
        for bs, bl, a_s, al, label in candidates:
            candidate, c_notes = build(bs, bl, a_s, al)
            if table_is_usable(candidate) and not any("fallback square" in note or "fallback sine" in note or "collapsed" in note for note in c_notes):
                table = candidate
                before_start, before_length, after_start, after_length = bs, bl, a_s, al
                gen_notes = [label] + [n for n in c_notes if "fallback square" not in n and "fallback sine" not in n and "collapsed" not in n]
                break
        else:
            table = fallback_table()
            gen_notes = ["windowed table collapsed; fallback square used"]

    if not table_is_usable(table):
        table = fallback_table()
        gen_notes.append("unsafe table replaced with fallback square")

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
    # TUTORIAL: callback may touch it at the same time.
    # TUTORIAL: ------------------------------------------------------------------------
    with self.state.lock:
        self.state.target_table = table
        self.state.target_volume = eff_volume
        # Neutralise the older playback-window fields. Windows now affect table
        # generation only, so the oscillator should always scan a full cycle.
        self.state.target_before_start = 0.0
        self.state.target_before_length = WINDOW_STEPS
        self.state.target_after_start = 0.0
        self.state.target_after_length = WINDOW_STEPS
        if hard_reset:
            self.state.reset_requested = True

    self.pending = False
    self._draw_waveform(table)
    self.table_notes = notes + gen_notes
    self.window_notes = window_notes
    self._refresh_clip_indicator()
    return True, effective, self.table_notes



App._push_windows = _new_push_windows
App._push_table = _new_push_table


# -----------------------------------------------------------------------------
# Visualiser mode override
#
# Base mode draws the final generated wavetable after generation-stage windows
# and wavetable-type math. Rendered mode draws one nominal cycle after the
# self-modulation path: FM changes phase advance, PWM warps phase width, and AM
# shapes the output amplitude. The rendered preview deliberately ignores pitch
# and master volume so it remains a fixed single-cycle shape display.
# -----------------------------------------------------------------------------

# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: DSP render helper: render_selfmod_cycle produces an offline or preview waveform from
# TUTORIAL: tables and modulation settings.
# TUTORIAL: ------------------------------------------------------------------------
def render_selfmod_cycle(table: np.ndarray, fm: float, pwm: float, am: float, frames: int = TABLE_SIZE) -> np.ndarray:
    if not table_is_usable(table):
        table = fallback_table()
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
    # TUTORIAL: samples, and table generation can sometimes hit extreme math.
    # TUTORIAL: ------------------------------------------------------------------------
    src = np.nan_to_num(table.astype(np.float32), nan=0.0, posinf=1.0, neginf=-1.0)

    fm_index = clamp_float(fm, 0.0, 255.0) / 255.0 * 4.0
    pwm_depth = clamp_float(pwm, 0.0, 255.0) / 255.0 * 0.48
    am_depth = clamp_float(am, 0.0, 255.0) / 255.0

    phase = 0.0
    base_phase_inc = 1.0 / max(int(frames), 1)
    out = np.empty(frames, dtype=np.float64)

    for i in range(frames):
        # Modulation source is the same table being rendered.
        mod_sample = read_table_linear(src, phase)

        # AM is included in both the visible output and the self-mod signal,
        # matching the audio callback's "AM shapes FM/PWM" behaviour.
        am_gain = (1.0 - am_depth) + am_depth * (0.5 + 0.5 * mod_sample)
        am_gain = clamp_float(am_gain, 0.0, 1.5)
        mod_signal = clamp_float(mod_sample * am_gain, -1.0, 1.0)

        width = clamp_float(0.5 + pwm_depth * mod_signal, 0.02, 0.98)
        if phase < width:
            read_phase = (phase / width) * 0.5
        else:
            read_phase = 0.5 + ((phase - width) / (1.0 - width)) * 0.5

        sample = read_table_linear(src, read_phase)
        out[i] = sample * am_gain

        fm_scale = clamp_float(1.0 + fm_index * mod_signal, MIN_FM_PHASE_SCALE, 8.0)
        phase_inc = base_phase_inc * fm_scale
        phase += min(phase_inc, 0.49)
        if phase >= 1.0:
            phase -= math.floor(phase)

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
    # TUTORIAL: samples, and table generation can sometimes hit extreme math.
    # TUTORIAL: ------------------------------------------------------------------------
    out = np.nan_to_num(out, nan=0.0, posinf=1.0, neginf=-1.0)
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Clip the signal/control range after math that might overshoot. This keeps audio
    # TUTORIAL: buffers and tables bounded.
    # TUTORIAL: ------------------------------------------------------------------------
    return np.clip(out, -1.0, 1.0).astype(np.float32)



# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: DSP render helper: render_selfmod_slow produces an offline or preview waveform from
# TUTORIAL: tables and modulation settings.
# TUTORIAL: ------------------------------------------------------------------------
def render_selfmod_slow(table: np.ndarray, fm: float, pwm: float, am: float, frames: int = 2048, cycles: float = 4.0) -> np.ndarray:
    """Render several unfolded cycles through the self-modulation path.

    This is for the visualiser only. It deliberately ignores pitch and master
    volume, and renders a deterministic slow-motion trace so FM/PWM/AM can be
    seen without trying to draw audio-rate movement.
    """
    if not table_is_usable(table):
        table = fallback_table()
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
    # TUTORIAL: samples, and table generation can sometimes hit extreme math.
    # TUTORIAL: ------------------------------------------------------------------------
    src = np.nan_to_num(table.astype(np.float32), nan=0.0, posinf=1.0, neginf=-1.0)

    fm_index = clamp_float(fm, 0.0, 255.0) / 255.0 * 4.0
    pwm_depth = clamp_float(pwm, 0.0, 255.0) / 255.0 * 0.48
    am_depth = clamp_float(am, 0.0, 255.0) / 255.0

    phase = 0.0
    base_phase_inc = float(cycles) / max(int(frames), 1)
    out = np.empty(frames, dtype=np.float64)

    for i in range(frames):
        mod_sample = read_table_linear(src, phase)
        am_gain = (1.0 - am_depth) + am_depth * (0.5 + 0.5 * mod_sample)
        am_gain = clamp_float(am_gain, 0.0, 1.5)
        mod_signal = clamp_float(mod_sample * am_gain, -1.0, 1.0)

        width = clamp_float(0.5 + pwm_depth * mod_signal, 0.02, 0.98)
        if phase < width:
            read_phase = (phase / width) * 0.5
        else:
            read_phase = 0.5 + ((phase - width) / (1.0 - width)) * 0.5

        sample = read_table_linear(src, read_phase)
        out[i] = sample * am_gain

        fm_scale = clamp_float(1.0 + fm_index * mod_signal, MIN_FM_PHASE_SCALE, 8.0)
        phase_inc = base_phase_inc * fm_scale
        phase += min(phase_inc, 0.49)
        if phase >= 1.0:
            phase -= math.floor(phase)

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
    # TUTORIAL: samples, and table generation can sometimes hit extreme math.
    # TUTORIAL: ------------------------------------------------------------------------
    out = np.nan_to_num(out, nan=0.0, posinf=1.0, neginf=-1.0)
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Clip the signal/control range after math that might overshoot. This keeps audio
    # TUTORIAL: buffers and tables bounded.
    # TUTORIAL: ------------------------------------------------------------------------
    return np.clip(out, -1.0, 1.0).astype(np.float32)

# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Export helper: _visual_draw_waveform turns in-memory rendered audio/settings into
# TUTORIAL: files on disk. This is separate from live playback by design.
# TUTORIAL: ------------------------------------------------------------------------
def _visual_draw_waveform(self, table: np.ndarray) -> None:
    if not hasattr(self, "wave_canvas"):
        return

    mode = getattr(self, "visualizer_mode", tk.StringVar(value="base")).get()
    label = "one cycle, final generated table"
    y = np.asarray(table, dtype=np.float64)

    if mode == "rendered":
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
        # TUTORIAL: callback may touch it at the same time.
        # TUTORIAL: ------------------------------------------------------------------------
        with self.state.lock:
            fm = float(self.state.target_fm)
            pwm = float(self.state.target_pwm)
            am = float(self.state.target_am)
        y = render_selfmod_cycle(np.asarray(table, dtype=np.float32), fm=fm, pwm=pwm, am=am)
        label = "one nominal cycle, rendered with FM/PWM/AM"
    elif mode == "slow":
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
        # TUTORIAL: callback may touch it at the same time.
        # TUTORIAL: ------------------------------------------------------------------------
        with self.state.lock:
            fm = float(self.state.target_fm)
            pwm = float(self.state.target_pwm)
            am = float(self.state.target_am)
        y = render_selfmod_slow(np.asarray(table, dtype=np.float32), fm=fm, pwm=pwm, am=am)
        label = "slow-motion render: 4 nominal cycles through FM/PWM/AM"

    canvas = self.wave_canvas
    width = max(int(canvas.winfo_width()), int(self.wave_canvas_size))
    height = max(int(canvas.winfo_height()), int(self.wave_canvas_size))
    pad = 18
    plot_w = max(width - 2 * pad, 1)
    plot_h = max(height - 2 * pad, 1)
    mid_y = pad + plot_h / 2.0
    amp = plot_h * 0.46

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
    # TUTORIAL: samples, and table generation can sometimes hit extreme math.
    # TUTORIAL: ------------------------------------------------------------------------
    y = np.nan_to_num(np.asarray(y, dtype=np.float64).reshape(-1), nan=0.0, posinf=1.0, neginf=-1.0)
    if y.size < 2:
        y = fallback_table().astype(np.float64)
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Clip the signal/control range after math that might overshoot. This keeps audio
    # TUTORIAL: buffers and tables bounded.
    # TUTORIAL: ------------------------------------------------------------------------
    y = np.clip(y, -1.0, 1.0)
    n = int(y.size)

    canvas.delete("all")
    canvas.create_rectangle(pad, pad, pad + plot_w, pad + plot_h, outline="#444444")
    canvas.create_line(pad, mid_y, pad + plot_w, mid_y, fill="#333333")
    canvas.create_line(pad, pad, pad + plot_w, pad, fill="#222222")
    canvas.create_line(pad, pad + plot_h, pad + plot_w, pad + plot_h, fill="#222222")

    points: list[float] = []
    for i in range(n):
        sample = float(y[i])
        x = pad + plot_w * (i / max(n - 1, 1))
        yy = mid_y - sample * amp
        points.extend((x, yy))
    canvas.create_line(*points, fill="#00d0ff", width=2, smooth=False)

    canvas.create_text(
        pad,
        height - 6,
        anchor="sw",
        fill="#999999",
        text=label,
    )


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Export helper: _visual_draw_current_waveform turns in-memory rendered audio/settings
# TUTORIAL: into files on disk. This is separate from live playback by design.
# TUTORIAL: ------------------------------------------------------------------------
def _visual_draw_current_waveform(self) -> None:
    if not hasattr(self, "wave_canvas"):
        return
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
    # TUTORIAL: callback may touch it at the same time.
    # TUTORIAL: ------------------------------------------------------------------------
    with self.state.lock:
        table = self.state.target_table.copy()
    self._draw_waveform(table)


_old_push_modulation_for_visualiser = App._push_modulation


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Visualiser helper: _visual_push_modulation draws waveform previews. Visualiser code
# TUTORIAL: should be informative, but it should not be the source of truth for rendering.
# TUTORIAL: ------------------------------------------------------------------------
def _visual_push_modulation(self, *, source: str = "modulation") -> None:
    _old_push_modulation_for_visualiser(self, source=source)
    self._draw_current_waveform()


App._draw_waveform = _visual_draw_waveform
App._draw_current_waveform = _visual_draw_current_waveform
App._push_modulation = _visual_push_modulation


# -----------------------------------------------------------------------------
# Cross-window note helper
#
# Before and after windows are sequential generation stages, not two competing
# slices of the same playback stream. Simple overlap between them is therefore
# not inherently unsafe. This helper only warns when the *combined* narrowing is
# extremely small, because that is the condition likely to collapse the table.
# -----------------------------------------------------------------------------

# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function combined_window_notes: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def combined_window_notes(before_length: float, after_length: float) -> list[str]:
    before_fraction = clamp_float(before_length, MIN_WINDOW_STEPS, WINDOW_STEPS) / WINDOW_STEPS
    after_fraction = clamp_float(after_length, MIN_WINDOW_STEPS, WINDOW_STEPS) / WINDOW_STEPS
    combined_steps = WINDOW_STEPS * before_fraction * after_fraction
    if before_length < WINDOW_STEPS and after_length < WINDOW_STEPS and combined_steps < MIN_WINDOW_STEPS:
        return [f"combined before×after window {combined_steps:.1f}→{MIN_WINDOW_STEPS:.0f} effective steps"]
    return []


_previous_generation_push_table = App._push_table


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Visualiser helper: _visual_generation_push_table draws waveform previews. Visualiser
# TUTORIAL: code should be informative, but it should not be the source of truth for rendering.
# TUTORIAL: ------------------------------------------------------------------------
def _visual_generation_push_table(
    self,
    p1: int,
    p2: int,
    p3: int,
    wt_type: int,
    volume: float,
    *,
    hard_reset: bool,
) -> tuple[bool, tuple[int, int, int, int, float], list[str]]:
    ok, effective, notes = _previous_generation_push_table(
        self, p1, p2, p3, wt_type, volume, hard_reset=hard_reset
    )
    if ok:
        before_start, before_length, _before_notes = clamp_window_pair(
            float(self.vars["before_start"].get()), float(self.vars["before_end"].get()), "before"
        )
        after_start, after_length, _after_notes = clamp_window_pair(
            float(self.vars["after_start"].get()), float(self.vars["after_end"].get()), "after"
        )
        extra = combined_window_notes(before_length, after_length)
        if extra:
            self.window_notes = self.window_notes + extra
            self._refresh_clip_indicator()
            notes = notes + extra
    return ok, effective, notes


App._push_table = _visual_generation_push_table



# -----------------------------------------------------------------------------
# Animation + stronger audio recovery override
#
# The previous "Slow FM/PWM/AM" view rendered a single static offline trace.
# This override makes it an actual low-FPS animation. It also hardens the audio
# callback and Reset all path so bad/non-finite state is clamped before it can
# kill the PortAudio callback.
# -----------------------------------------------------------------------------

# Keep the original clamp name, but make it non-finite-safe. Earlier functions
# look up clamp_float at runtime, so this replacement protects audio, GUI, and
# preview paths without editing every call site.
# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Float version of the range guard. Several later override layers replace this name
# TUTORIAL: with a non-finite-safe version; Python looks up globals at call time, so old
# TUTORIAL: functions automatically use the safer later definition.
# TUTORIAL: ------------------------------------------------------------------------
def clamp_float(x: float, lo: float, hi: float) -> float:  # type: ignore[override]
    try:
        value = float(x)
    except (TypeError, ValueError):
        value = float(lo)
    if not math.isfinite(value):
        value = float(lo)
    return max(float(lo), min(float(hi), value))


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function sanitise_shared_state: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def sanitise_shared_state(state: SharedState) -> None:
    """Clamp shared values before the real-time callback reads them."""
    if not table_is_usable(state.target_table):
        state.target_table = fallback_table()
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
    # TUTORIAL: samples, and table generation can sometimes hit extreme math.
    # TUTORIAL: ------------------------------------------------------------------------
    state.target_table = np.nan_to_num(
        state.target_table.astype(np.float32), nan=0.0, posinf=1.0, neginf=-1.0
    )
    state.target_volume = clamp_float(state.target_volume, 0.0, 1.0)
    state.target_frequency = clamp_float(state.target_frequency, MIN_FREQUENCY, MAX_FREQUENCY)
    state.target_fm = clamp_float(state.target_fm, 0.0, 255.0)
    state.target_pwm = clamp_float(state.target_pwm, 0.0, 255.0)
    state.target_am = clamp_float(state.target_am, 0.0, 255.0)
    state.target_before_start = clamp_float(state.target_before_start, 0.0, WINDOW_STEPS)
    state.target_before_length = clamp_float(state.target_before_length, MIN_WINDOW_STEPS, WINDOW_STEPS)
    state.target_after_start = clamp_float(state.target_after_start, 0.0, WINDOW_STEPS)
    state.target_after_length = clamp_float(state.target_after_length, MIN_WINDOW_STEPS, WINDOW_STEPS)


_original_audio_callback_for_recovery = WavetableOscillator.callback


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Audio helper: _recovery_audio_callback bridges UI state and the audio engine. The
# TUTORIAL: main rule is to keep blocking work out of the real-time callback.
# TUTORIAL: ------------------------------------------------------------------------
def _recovery_audio_callback(self, outdata, frames, time, status):
    """Run the normal callback, but prevent bad state from stopping audio.

    If a non-finite phase/frequency/table ever sneaks through, PortAudio would
    normally keep calling the callback, but an uncaught Python exception can
    stop output until the program is restarted. This wrapper sanitises state
    before entry and outputs one block of silence while resetting if anything
    still goes wrong.
    """
    try:
        if (not math.isfinite(float(getattr(self, "phase", 0.0)))):
            self.phase = 0.0
        if not table_is_usable(getattr(self, "current_table", fallback_table())):
            self.current_table = fallback_table()
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
        # TUTORIAL: samples, and table generation can sometimes hit extreme math.
        # TUTORIAL: ------------------------------------------------------------------------
        self.current_table = np.nan_to_num(
            self.current_table.astype(np.float32), nan=0.0, posinf=1.0, neginf=-1.0
        )
        self.current_volume = clamp_float(getattr(self, "current_volume", 0.0), 0.0, 1.0)
        self.current_frequency = clamp_float(
            getattr(self, "current_frequency", DEFAULT_FREQUENCY), MIN_FREQUENCY, MAX_FREQUENCY
        )
        self.current_fm = clamp_float(getattr(self, "current_fm", 0.0), 0.0, 255.0)
        self.current_pwm = clamp_float(getattr(self, "current_pwm", 0.0), 0.0, 255.0)
        self.current_am = clamp_float(getattr(self, "current_am", 0.0), 0.0, 255.0)
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
        # TUTORIAL: callback may touch it at the same time.
        # TUTORIAL: ------------------------------------------------------------------------
        with self.state.lock:
            sanitise_shared_state(self.state)
        return _original_audio_callback_for_recovery(self, outdata, frames, time, status)
    except Exception:
        # Keep PortAudio alive and force a known-good silent block. The next
        # callback starts from a square table and clean phase rather than crashing.
        self.phase = 0.0
        self.current_table = fallback_table()
        self.current_volume = 0.0
        self.current_frequency = DEFAULT_FREQUENCY
        self.current_fm = 0.0
        self.current_pwm = 0.0
        self.current_am = 0.0
        self.current_before_start = 0.0
        self.current_before_length = WINDOW_STEPS
        self.current_after_start = 0.0
        self.current_after_length = WINDOW_STEPS
        try:
            outdata.fill(0.0)
        except Exception:
            pass


WavetableOscillator.callback = _recovery_audio_callback


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: DSP render helper: render_selfmod_animated_frame produces an offline or preview
# TUTORIAL: waveform from tables and modulation settings.
# TUTORIAL: ------------------------------------------------------------------------
def render_selfmod_animated_frame(
    table: np.ndarray,
    fm: float,
    pwm: float,
    am: float,
    *,
    start_phase: float,
    frames: int = 512,
    cycles_across_screen: float = 1.25,
) -> np.ndarray:
    """Render a moving slow-motion time-domain preview of self-modulation.

    This is intentionally offline and pitch-independent. Each animation frame
    draws a short moving time window through the same FM/PWM/AM algorithm used
    by the audio path, then the GUI slowly advances start_phase.
    """
    if not table_is_usable(table):
        table = fallback_table()
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
    # TUTORIAL: samples, and table generation can sometimes hit extreme math.
    # TUTORIAL: ------------------------------------------------------------------------
    src = np.nan_to_num(table.astype(np.float32), nan=0.0, posinf=1.0, neginf=-1.0)

    fm_index = clamp_float(fm, 0.0, 255.0) / 255.0 * 4.0
    pwm_depth = clamp_float(pwm, 0.0, 255.0) / 255.0 * 0.48
    am_depth = clamp_float(am, 0.0, 255.0) / 255.0

    phase = float(start_phase) % 1.0
    base_phase_inc = float(cycles_across_screen) / max(int(frames), 1)
    out = np.empty(frames, dtype=np.float64)

    for i in range(frames):
        mod_sample = read_table_linear(src, phase)
        am_gain = (1.0 - am_depth) + am_depth * (0.5 + 0.5 * mod_sample)
        am_gain = clamp_float(am_gain, 0.0, 1.5)
        mod_signal = clamp_float(mod_sample * am_gain, -1.0, 1.0)

        width = clamp_float(0.5 + pwm_depth * mod_signal, 0.02, 0.98)
        if phase < width:
            read_phase = (phase / width) * 0.5
        else:
            read_phase = 0.5 + ((phase - width) / (1.0 - width)) * 0.5

        sample = read_table_linear(src, read_phase)
        out[i] = sample * am_gain

        fm_scale = clamp_float(1.0 + fm_index * mod_signal, MIN_FM_PHASE_SCALE, 8.0)
        phase += min(base_phase_inc * fm_scale, 0.49)
        if phase >= 1.0:
            phase -= math.floor(phase)

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
    # TUTORIAL: samples, and table generation can sometimes hit extreme math.
    # TUTORIAL: ------------------------------------------------------------------------
    out = np.nan_to_num(out, nan=0.0, posinf=1.0, neginf=-1.0)
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Clip the signal/control range after math that might overshoot. This keeps audio
    # TUTORIAL: buffers and tables bounded.
    # TUTORIAL: ------------------------------------------------------------------------
    return np.clip(out, -1.0, 1.0).astype(np.float32)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Export helper: _animated_draw_waveform turns in-memory rendered audio/settings into
# TUTORIAL: files on disk. This is separate from live playback by design.
# TUTORIAL: ------------------------------------------------------------------------
def _animated_draw_waveform(self, table: np.ndarray) -> None:
    if not hasattr(self, "wave_canvas"):
        return

    mode = getattr(self, "visualizer_mode", tk.StringVar(value="base")).get()
    label = "one cycle, final generated table"
    y = np.asarray(table, dtype=np.float64)

    if mode == "rendered":
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
        # TUTORIAL: callback may touch it at the same time.
        # TUTORIAL: ------------------------------------------------------------------------
        with self.state.lock:
            fm = float(self.state.target_fm)
            pwm = float(self.state.target_pwm)
            am = float(self.state.target_am)
        y = render_selfmod_cycle(np.asarray(table, dtype=np.float32), fm=fm, pwm=pwm, am=am)
        label = "one nominal cycle, rendered with FM/PWM/AM"
    elif mode == "slow":
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
        # TUTORIAL: callback may touch it at the same time.
        # TUTORIAL: ------------------------------------------------------------------------
        with self.state.lock:
            fm = float(self.state.target_fm)
            pwm = float(self.state.target_pwm)
            am = float(self.state.target_am)
        phase = getattr(self, "visual_anim_phase", 0.0)
        y = render_selfmod_animated_frame(
            np.asarray(table, dtype=np.float32), fm=fm, pwm=pwm, am=am, start_phase=phase
        )
        label = "animated slow-motion FM/PWM/AM preview"

    canvas = self.wave_canvas
    width = max(int(canvas.winfo_width()), int(self.wave_canvas_size))
    height = max(int(canvas.winfo_height()), int(self.wave_canvas_size))
    pad = 18
    plot_w = max(width - 2 * pad, 1)
    plot_h = max(height - 2 * pad, 1)
    mid_y = pad + plot_h / 2.0
    amp = plot_h * 0.46

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
    # TUTORIAL: samples, and table generation can sometimes hit extreme math.
    # TUTORIAL: ------------------------------------------------------------------------
    y = np.nan_to_num(np.asarray(y, dtype=np.float64).reshape(-1), nan=0.0, posinf=1.0, neginf=-1.0)
    if y.size < 2:
        y = fallback_table().astype(np.float64)
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Clip the signal/control range after math that might overshoot. This keeps audio
    # TUTORIAL: buffers and tables bounded.
    # TUTORIAL: ------------------------------------------------------------------------
    y = np.clip(y, -1.0, 1.0)
    n = int(y.size)

    canvas.delete("all")
    canvas.create_rectangle(pad, pad, pad + plot_w, pad + plot_h, outline="#444444")
    canvas.create_line(pad, mid_y, pad + plot_w, mid_y, fill="#333333")
    canvas.create_line(pad, pad, pad + plot_w, pad, fill="#222222")
    canvas.create_line(pad, pad + plot_h, pad + plot_w, pad + plot_h, fill="#222222")

    points: list[float] = []
    for i in range(n):
        sample = float(y[i])
        x = pad + plot_w * (i / max(n - 1, 1))
        yy = mid_y - sample * amp
        points.extend((x, yy))
    canvas.create_line(*points, fill="#00d0ff", width=2, smooth=False)

    if mode == "slow":
        # A subtle phase marker makes it obvious the view is animated even when
        # the waveform itself is close to periodic.
        marker_x = pad + plot_w * (getattr(self, "visual_anim_phase", 0.0) % 1.0)
        canvas.create_line(marker_x, pad, marker_x, pad + plot_h, fill="#777777")

    canvas.create_text(pad, height - 6, anchor="sw", fill="#999999", text=label)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Visualiser helper: _animated_schedule_visualiser draws waveform previews. Visualiser
# TUTORIAL: code should be informative, but it should not be the source of truth for rendering.
# TUTORIAL: ------------------------------------------------------------------------
def _animated_schedule_visualiser(self) -> None:
    if getattr(self, "visualizer_mode", tk.StringVar(value="base")).get() != "slow":
        self.visual_anim_after_id = None
        return
    if getattr(self, "visual_anim_after_id", None) is None:
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: Tk uses scheduled callbacks instead of long blocking loops. after(...) lets
        # TUTORIAL: the UI stay responsive.
        # TUTORIAL: ------------------------------------------------------------------------
        self.visual_anim_after_id = self.root.after(80, self._visual_animation_tick)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Visualiser helper: _visual_animation_tick draws waveform previews. Visualiser code
# TUTORIAL: should be informative, but it should not be the source of truth for rendering.
# TUTORIAL: ------------------------------------------------------------------------
def _visual_animation_tick(self) -> None:
    self.visual_anim_after_id = None
    if getattr(self, "visualizer_mode", tk.StringVar(value="base")).get() != "slow":
        return
    self.visual_anim_phase = (getattr(self, "visual_anim_phase", 0.0) + 0.018) % 1.0
    self._draw_current_waveform()
    self._animated_schedule_visualiser()


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Export helper: _animated_draw_current_waveform turns in-memory rendered
# TUTORIAL: audio/settings into files on disk. This is separate from live playback by design.
# TUTORIAL: ------------------------------------------------------------------------
def _animated_draw_current_waveform(self) -> None:
    if not hasattr(self, "wave_canvas"):
        return
    mode = getattr(self, "visualizer_mode", tk.StringVar(value="base")).get()
    if mode != "slow" and getattr(self, "visual_anim_after_id", None) is not None:
        try:
            # TUTORIAL: ------------------------------------------------------------------------
            # TUTORIAL: Tk uses scheduled callbacks instead of long blocking loops. after(...)
            # TUTORIAL: lets the UI stay responsive.
            # TUTORIAL: ------------------------------------------------------------------------
            self.root.after_cancel(self.visual_anim_after_id)
        except Exception:
            pass
        self.visual_anim_after_id = None
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
    # TUTORIAL: callback may touch it at the same time.
    # TUTORIAL: ------------------------------------------------------------------------
    with self.state.lock:
        table = self.state.target_table.copy()
    self._draw_waveform(table)
    if mode == "slow":
        self._animated_schedule_visualiser()


App._draw_waveform = _animated_draw_waveform
App._draw_current_waveform = _animated_draw_current_waveform
App._visual_animation_tick = _visual_animation_tick
App._animated_schedule_visualiser = _animated_schedule_visualiser


# Rename the third radio button after App builds the controls. This avoids
# editing the earlier constructor block and keeps the rest of the layout intact.
_original_app_init_for_animation = App.__init__


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _animated_app_init: small named steps make the signal path, UI path,
# TUTORIAL: and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _animated_app_init(self, *args, **kwargs):
    _original_app_init_for_animation(self, *args, **kwargs)
    self.visual_anim_phase = 0.0
    self.visual_anim_after_id = None
    # Best-effort rename of the existing "Slow FM/PWM/AM" radiobutton.
    try:
        for child in self.visual_parent.winfo_children():
            if isinstance(child, tk.Frame):
                for sub in child.winfo_children():
                    if isinstance(sub, tk.Radiobutton) and str(sub.cget("text")) == "Slow FM/PWM/AM":
                        sub.config(text="Animated FM/PWM/AM")
    except Exception:
        pass


App.__init__ = _animated_app_init


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _strong_reset_all: small named steps make the signal path, UI path,
# TUTORIAL: and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _strong_reset_all(self) -> None:
    """One-button full reset that directly replaces all audio state."""
    p1, p2, p3, wt_type = 2048, 4080, 160, 3
    fm = pwm = am = 0
    before_start, before_end = 0, 4096
    after_start, after_end = 0, 4096
    volume = 0.20
    frequency = self.default_frequency

    self.vars["pot1"].set(p1)
    self.vars["pot2"].set(p2)
    self.vars["pot3"].set(p3)
    self.vars["type"].set(wt_type)
    self.vars["fm"].set(fm)
    self.vars["pwm"].set(pwm)
    self.vars["am"].set(am)
    self.vars["before_start"].set(before_start)
    self.vars["before_end"].set(before_end)
    self.vars["after_start"].set(after_start)
    self.vars["after_end"].set(after_end)
    self.vars["volume"].set(volume)
    self._set_frequency_widgets(frequency)
    self._update_type_description()

    table = generate_wavetable(
        p1, p2, p3, wt_type,
        before_start=0.0, before_length=WINDOW_STEPS,
        after_start=0.0, after_length=WINDOW_STEPS,
    )
    if not table_is_usable(table):
        table = fallback_table()

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
    # TUTORIAL: callback may touch it at the same time.
    # TUTORIAL: ------------------------------------------------------------------------
    with self.state.lock:
        self.state.target_table = table
        self.state.target_volume = volume
        self.state.target_frequency = frequency
        self.state.target_fm = 0.0
        self.state.target_pwm = 0.0
        self.state.target_am = 0.0
        self.state.target_before_start = 0.0
        self.state.target_before_length = WINDOW_STEPS
        self.state.target_after_start = 0.0
        self.state.target_after_length = WINDOW_STEPS
        self.state.reset_requested = True
        sanitise_shared_state(self.state)

    self.pending = False
    self.table_notes = []
    self.frequency_notes = []
    self.window_notes = []
    self._refresh_clip_indicator()
    self.status.config(text="RESET ALL  defaults pushed directly to audio engine")
    self._draw_current_waveform()


App.reset_all = _strong_reset_all



# -----------------------------------------------------------------------------
# Stable visualiser override
#
# The previous animated mode advanced the display start phase. That made even a
# plain safety wave appear to scroll sideways. This replacement keeps the x-axis
# phase-locked. The rendered view is a stable single-cycle representation of the
# FM/PWM/AM output. The animated view changes a visual depth scale over time, so
# it shows the modulation effect in slow motion without horizontal drift.


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: DSP render helper: render_selfmod_phase_locked produces an offline or preview
# TUTORIAL: waveform from tables and modulation settings.
# TUTORIAL: ------------------------------------------------------------------------
def render_selfmod_phase_locked(
    table: np.ndarray,
    fm: float,
    pwm: float,
    am: float,
    *,
    frames: int = 512,
    depth_scale: float = 1.0,
) -> np.ndarray:
    """Render one horizontally stable cycle after FM/PWM/AM.

    This is not a scrolling time-window. It builds a phase-locked one-cycle
    waveform. FM is represented as a normalised cumulative phase warp over one
    cycle, so a safety wave from Panic Audio remains stationary instead of drifting
    sideways.
    """
    if not table_is_usable(table):
        table = fallback_table()
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
    # TUTORIAL: samples, and table generation can sometimes hit extreme math.
    # TUTORIAL: ------------------------------------------------------------------------
    src = np.nan_to_num(table.astype(np.float32), nan=0.0, posinf=1.0, neginf=-1.0)

    frames = max(int(frames), 32)
    depth_scale = clamp_float(depth_scale, 0.0, 1.0)
    fm_index = clamp_float(fm, 0.0, 255.0) / 255.0 * 4.0 * depth_scale
    pwm_depth = clamp_float(pwm, 0.0, 255.0) / 255.0 * 0.48 * depth_scale
    am_depth = clamp_float(am, 0.0, 255.0) / 255.0 * depth_scale

    nominal_phase = np.linspace(0.0, 1.0, frames, endpoint=False, dtype=np.float64)

    # First pass: estimate the FM phase-rate over a nominal cycle.
    rates = np.empty(frames, dtype=np.float64)
    for i, ph in enumerate(nominal_phase):
        mod_sample = read_table_linear(src, float(ph))
        am_gain_for_mod = (1.0 - am_depth) + am_depth * (0.5 + 0.5 * mod_sample)
        am_gain_for_mod = clamp_float(am_gain_for_mod, 0.0, 1.5)
        mod_signal = clamp_float(mod_sample * am_gain_for_mod, -1.0, 1.0)
        rates[i] = clamp_float(1.0 + fm_index * mod_signal, MIN_FM_PHASE_SCALE, 8.0)

    total = float(np.sum(rates))
    if (not math.isfinite(total)) or total <= EPSILON:
        phase_map = nominal_phase
    else:
        # Phase-locked cumulative FM warp. Normalising by the total rate makes
        # the displayed waveform begin and end at the same x positions every
        # frame, rather than sliding left/right.
        cumulative = np.concatenate(([0.0], np.cumsum(rates[:-1])))
        phase_map = (cumulative / total) % 1.0

    out = np.empty(frames, dtype=np.float64)
    for i, math_phase in enumerate(phase_map):
        mod_sample = read_table_linear(src, float(math_phase))
        am_gain = (1.0 - am_depth) + am_depth * (0.5 + 0.5 * mod_sample)
        am_gain = clamp_float(am_gain, 0.0, 1.5)
        mod_signal = clamp_float(mod_sample * am_gain, -1.0, 1.0)

        # PWM phase-width warp. At depth zero this is neutral.
        width = clamp_float(0.5 + pwm_depth * mod_signal, 0.02, 0.98)
        if math_phase < width:
            read_phase = (math_phase / width) * 0.5
        else:
            read_phase = 0.5 + ((math_phase - width) / (1.0 - width)) * 0.5

        sample = read_table_linear(src, read_phase)
        out[i] = sample * am_gain

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
    # TUTORIAL: samples, and table generation can sometimes hit extreme math.
    # TUTORIAL: ------------------------------------------------------------------------
    out = np.nan_to_num(out, nan=0.0, posinf=1.0, neginf=-1.0)
    peak = float(np.max(np.abs(out)))
    if math.isfinite(peak) and peak > 1.0:
        out = out / peak
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Clip the signal/control range after math that might overshoot. This keeps audio
    # TUTORIAL: buffers and tables bounded.
    # TUTORIAL: ------------------------------------------------------------------------
    return np.clip(out, -1.0, 1.0).astype(np.float32)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Export helper: _stable_anim_draw_waveform turns in-memory rendered audio/settings
# TUTORIAL: into files on disk. This is separate from live playback by design.
# TUTORIAL: ------------------------------------------------------------------------
def _stable_anim_draw_waveform(self, table: np.ndarray) -> None:
    if not hasattr(self, "wave_canvas"):
        return

    mode = getattr(self, "visualizer_mode", tk.StringVar(value="base")).get()
    label = "one cycle, final generated table"
    y = np.asarray(table, dtype=np.float64)

    if mode in {"rendered", "slow"}:
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
        # TUTORIAL: callback may touch it at the same time.
        # TUTORIAL: ------------------------------------------------------------------------
        with self.state.lock:
            fm = float(self.state.target_fm)
            pwm = float(self.state.target_pwm)
            am = float(self.state.target_am)

        if mode == "slow":
            phase = getattr(self, "visual_anim_phase", 0.0)
            # 0→1→0 depth sweep, phase-locked. This animates the modulation
            # effect, not the horizontal starting phase of the waveform.
            depth_scale = 0.5 - 0.5 * math.cos(2.0 * math.pi * phase)
            label = f"animated stable FM/PWM/AM depth sweep: {depth_scale:.2f}×"
        else:
            depth_scale = 1.0
            label = "phase-locked rendered FM/PWM/AM"

        y = render_selfmod_phase_locked(
            np.asarray(table, dtype=np.float32),
            fm=fm,
            pwm=pwm,
            am=am,
            depth_scale=depth_scale,
        )

    canvas = self.wave_canvas
    width = max(int(canvas.winfo_width()), int(self.wave_canvas_size))
    height = max(int(canvas.winfo_height()), int(self.wave_canvas_size))
    pad = 18
    plot_w = max(width - 2 * pad, 1)
    plot_h = max(height - 2 * pad, 1)
    mid_y = pad + plot_h / 2.0
    amp = plot_h * 0.46

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
    # TUTORIAL: samples, and table generation can sometimes hit extreme math.
    # TUTORIAL: ------------------------------------------------------------------------
    y = np.nan_to_num(np.asarray(y, dtype=np.float64).reshape(-1), nan=0.0, posinf=1.0, neginf=-1.0)
    if y.size < 2:
        y = fallback_table().astype(np.float64)
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Clip the signal/control range after math that might overshoot. This keeps audio
    # TUTORIAL: buffers and tables bounded.
    # TUTORIAL: ------------------------------------------------------------------------
    y = np.clip(y, -1.0, 1.0)
    n = int(y.size)

    canvas.delete("all")
    canvas.create_rectangle(pad, pad, pad + plot_w, pad + plot_h, outline="#444444")
    canvas.create_line(pad, mid_y, pad + plot_w, mid_y, fill="#333333")
    canvas.create_line(pad, pad, pad + plot_w, pad, fill="#222222")
    canvas.create_line(pad, pad + plot_h, pad + plot_w, pad + plot_h, fill="#222222")

    points: list[float] = []
    for i in range(n):
        sample = float(y[i])
        x = pad + plot_w * (i / max(n - 1, 1))
        yy = mid_y - sample * amp
        points.extend((x, yy))
    canvas.create_line(*points, fill="#00d0ff", width=2, smooth=False)

    canvas.create_text(pad, height - 6, anchor="sw", fill="#999999", text=label)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _stable_anim_tick: small named steps make the signal path, UI path,
# TUTORIAL: and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _stable_anim_tick(self) -> None:
    self.visual_anim_after_id = None
    if getattr(self, "visualizer_mode", tk.StringVar(value="base")).get() != "slow":
        return
    self.visual_anim_phase = (getattr(self, "visual_anim_phase", 0.0) + 0.018) % 1.0
    self._draw_current_waveform()
    self._animated_schedule_visualiser()


# Override the previous scrolling animation renderer with the phase-locked one.
App._draw_waveform = _stable_anim_draw_waveform
App._visual_animation_tick = _stable_anim_tick


_previous_stable_app_init = App.__init__


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _stable_app_init: small named steps make the signal path, UI path,
# TUTORIAL: and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _stable_app_init(self, *args, **kwargs):
    _previous_stable_app_init(self, *args, **kwargs)
    try:
        for child in self.visual_parent.winfo_children():
            if isinstance(child, tk.Frame):
                for sub in child.winfo_children():
                    if isinstance(sub, tk.Radiobutton) and str(sub.cget("text")) in {
                        "Slow FM/PWM/AM",
                        "Animated FM/PWM/AM",
                    }:
                        sub.config(text="Animated stable FM/PWM/AM")
    except Exception:
        pass


App.__init__ = _stable_app_init


# -----------------------------------------------------------------------------
# User-selectable bounded FM range + frequency-referenced visualizer override
#
# The previous self-FM used a linear phase-increment scale. At high FM values the
# scale could approach the minimum phase rate for long parts of the cycle, which
# sounded like a large downward detune or octave drop. This override maps FM to a
# musical pitch range in cents:
#
#     instantaneous_multiplier = 2 ** (cents / 1200)
#
# The FM slider controls how much of the selected range is used. The range is
# selected independently, from small cent values up to multiple octaves.
# -----------------------------------------------------------------------------

FM_RANGE_OPTIONS = (
    ("±5 cents", 5.0),
    ("±10 cents", 10.0),
    ("±25 cents", 25.0),
    ("±50 cents", 50.0),
    ("±100 cents (1 semitone)", 100.0),
    ("±200 cents (2 semitones)", 200.0),
    ("±700 cents (perfect fifth)", 700.0),
    ("±1200 cents (1 octave)", 1200.0),
    ("±2400 cents (2 octaves)", 2400.0),
    ("±4800 cents (4 octaves)", 4800.0),
)
FM_RANGE_BY_LABEL = {label: cents for label, cents in FM_RANGE_OPTIONS}
DEFAULT_FM_RANGE_LABEL = "±1200 cents (1 octave)"
DEFAULT_FM_RANGE_CENTS = FM_RANGE_BY_LABEL[DEFAULT_FM_RANGE_LABEL]
MAX_FM_RANGE_CENTS = 4800.0
MAX_FM_PHASE_SCALE = 16.0


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function fm_range_label_to_cents: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def fm_range_label_to_cents(label: str) -> float:
    return clamp_float(FM_RANGE_BY_LABEL.get(str(label), DEFAULT_FM_RANGE_CENTS), 0.0, MAX_FM_RANGE_CENTS)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function bounded_fm_phase_scale: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def bounded_fm_phase_scale(mod_signal: float, fm: float, fm_range_cents: float, *, depth_scale: float = 1.0) -> float:
    """Return a bounded exponential pitch multiplier for self-FM.

    fm=255 uses the full selected range. fm=128 uses about half the range.
    The range is symmetrical around the nominal pitch in cents/octaves, so it
    cannot hit zero frequency or run away as a linear phase multiplier can.
    """
    fm_amount = clamp_float(fm, 0.0, 255.0) / 255.0
    fm_amount *= clamp_float(depth_scale, 0.0, 1.0)
    range_cents = clamp_float(fm_range_cents, 0.0, MAX_FM_RANGE_CENTS)
    signal = clamp_float(mod_signal, -1.0, 1.0)
    cents = signal * range_cents * fm_amount
    try:
        scale = 2.0 ** (cents / 1200.0)
    except OverflowError:
        scale = MAX_FM_PHASE_SCALE if cents > 0 else MIN_FM_PHASE_SCALE
    return clamp_float(scale, MIN_FM_PHASE_SCALE, MAX_FM_PHASE_SCALE)


_previous_sanitise_for_fm_range = sanitise_shared_state


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function sanitise_shared_state: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def sanitise_shared_state(state: SharedState) -> None:  # type: ignore[override]
    _previous_sanitise_for_fm_range(state)
    current = getattr(state, "target_fm_range_cents", DEFAULT_FM_RANGE_CENTS)
    state.target_fm_range_cents = clamp_float(current, 0.0, MAX_FM_RANGE_CENTS)


_previous_osc_init_for_fm_range = WavetableOscillator.__init__


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _fm_range_osc_init: small named steps make the signal path, UI path,
# TUTORIAL: and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _fm_range_osc_init(self, *args, **kwargs):
    _previous_osc_init_for_fm_range(self, *args, **kwargs)
    self.current_fm_range_cents = clamp_float(
        getattr(self.state, "target_fm_range_cents", DEFAULT_FM_RANGE_CENTS),
        0.0,
        MAX_FM_RANGE_CENTS,
    )


WavetableOscillator.__init__ = _fm_range_osc_init


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Audio helper: _bounded_fm_audio_callback bridges UI state and the audio engine. The
# TUTORIAL: main rule is to keep blocking work out of the real-time callback.
# TUTORIAL: ------------------------------------------------------------------------
def _bounded_fm_audio_callback(self, outdata, frames, time, status):
    """Audio callback using bounded exponential pitch-range FM."""
    try:
        if not math.isfinite(float(getattr(self, "phase", 0.0))):
            self.phase = 0.0
        if not table_is_usable(getattr(self, "current_table", fallback_table())):
            self.current_table = fallback_table()
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
        # TUTORIAL: samples, and table generation can sometimes hit extreme math.
        # TUTORIAL: ------------------------------------------------------------------------
        self.current_table = np.nan_to_num(
            self.current_table.astype(np.float32), nan=0.0, posinf=1.0, neginf=-1.0
        )
        self.current_volume = clamp_float(getattr(self, "current_volume", 0.0), 0.0, 1.0)
        self.current_frequency = clamp_float(
            getattr(self, "current_frequency", DEFAULT_FREQUENCY), MIN_FREQUENCY, MAX_FREQUENCY
        )
        self.current_fm = clamp_float(getattr(self, "current_fm", 0.0), 0.0, 255.0)
        self.current_pwm = clamp_float(getattr(self, "current_pwm", 0.0), 0.0, 255.0)
        self.current_am = clamp_float(getattr(self, "current_am", 0.0), 0.0, 255.0)
        self.current_fm_range_cents = clamp_float(
            getattr(self, "current_fm_range_cents", DEFAULT_FM_RANGE_CENTS), 0.0, MAX_FM_RANGE_CENTS
        )

        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
        # TUTORIAL: callback may touch it at the same time.
        # TUTORIAL: ------------------------------------------------------------------------
        with self.state.lock:
            sanitise_shared_state(self.state)
            target_table = self.state.target_table.copy()
            target_volume = float(self.state.target_volume)
            target_frequency = float(self.state.target_frequency)
            target_fm = float(self.state.target_fm)
            target_pwm = float(self.state.target_pwm)
            target_am = float(self.state.target_am)
            target_fm_range_cents = float(getattr(self.state, "target_fm_range_cents", DEFAULT_FM_RANGE_CENTS))
            target_before_start = float(self.state.target_before_start)
            target_before_length = float(self.state.target_before_length)
            target_after_start = float(self.state.target_after_start)
            target_after_length = float(self.state.target_after_length)
            reset_requested = self.state.reset_requested
            self.state.reset_requested = False

        if not table_is_usable(target_table):
            target_table = fallback_table()

        if reset_requested:
            self.current_table = target_table.copy()
            self.current_volume = target_volume
            self.current_frequency = target_frequency
            self.current_fm = target_fm
            self.current_pwm = target_pwm
            self.current_am = target_am
            self.current_fm_range_cents = target_fm_range_cents
            self.current_before_start = target_before_start
            self.current_before_length = target_before_length
            self.current_after_start = target_after_start
            self.current_after_length = target_after_length
            self.phase = 0.0
        else:
            self.current_table += (target_table - self.current_table) * self.table_morph
            self.current_volume += (target_volume - self.current_volume) * self.volume_morph
            self.current_frequency += (target_frequency - self.current_frequency) * self.frequency_morph
            self.current_fm += (target_fm - self.current_fm) * self.modulation_morph
            self.current_pwm += (target_pwm - self.current_pwm) * self.modulation_morph
            self.current_am += (target_am - self.current_am) * self.modulation_morph
            self.current_fm_range_cents += (target_fm_range_cents - self.current_fm_range_cents) * self.modulation_morph
            self.current_before_start += (target_before_start - self.current_before_start) * self.window_morph
            self.current_before_length += (target_before_length - self.current_before_length) * self.window_morph
            self.current_after_start += (target_after_start - self.current_after_start) * self.window_morph
            self.current_after_length += (target_after_length - self.current_after_length) * self.window_morph
            # TUTORIAL: ------------------------------------------------------------------------
            # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive
            # TUTORIAL: non-finite samples, and table generation can sometimes hit extreme math.
            # TUTORIAL: ------------------------------------------------------------------------
            self.current_table = np.nan_to_num(self.current_table, nan=0.0, posinf=1.0, neginf=-1.0)

        out = np.empty(frames, dtype=np.float32)
        base_phase_inc = clamp_float(self.current_frequency / self.sample_rate, 0.0, 0.49)
        table = self.current_table
        phase = self.phase
        volume = self.current_volume

        pwm_depth = clamp_float(self.current_pwm, 0.0, 255.0) / 255.0 * 0.48
        am_depth = clamp_float(self.current_am, 0.0, 255.0) / 255.0
        before_start = clamp_float(self.current_before_start, 0.0, WINDOW_STEPS)
        before_length = clamp_float(self.current_before_length, MIN_WINDOW_STEPS, WINDOW_STEPS)
        after_start = clamp_float(self.current_after_start, 0.0, WINDOW_STEPS)
        after_length = clamp_float(self.current_after_length, MIN_WINDOW_STEPS, WINDOW_STEPS)
        fm_value = clamp_float(self.current_fm, 0.0, 255.0)
        fm_range_cents = clamp_float(self.current_fm_range_cents, 0.0, MAX_FM_RANGE_CENTS)

        for i in range(frames):
            math_phase = apply_phase_window(phase, before_start, before_length)
            mod_sample = read_table_linear(table, math_phase)

            am_gain = (1.0 - am_depth) + am_depth * (0.5 + 0.5 * mod_sample)
            am_gain = clamp_float(am_gain, 0.0, 1.5)
            mod_signal = clamp_float(mod_sample * am_gain, -1.0, 1.0)

            width = clamp_float(0.5 + pwm_depth * mod_signal, 0.02, 0.98)
            if math_phase < width:
                read_phase = (math_phase / width) * 0.5
            else:
                read_phase = 0.5 + ((math_phase - width) / (1.0 - width)) * 0.5

            read_phase = apply_phase_window(read_phase, after_start, after_length)
            sample = read_table_linear(table, read_phase)
            out[i] = sample * volume * am_gain

            fm_scale = bounded_fm_phase_scale(mod_signal, fm_value, fm_range_cents)
            phase += min(base_phase_inc * fm_scale, 0.49)
            if phase >= 1.0:
                phase -= math.floor(phase)

        self.phase = phase
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
        # TUTORIAL: samples, and table generation can sometimes hit extreme math.
        # TUTORIAL: ------------------------------------------------------------------------
        out = np.nan_to_num(out, nan=0.0, posinf=1.0, neginf=-1.0)
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: Clip the signal/control range after math that might overshoot. This keeps
        # TUTORIAL: audio buffers and tables bounded.
        # TUTORIAL: ------------------------------------------------------------------------
        out = np.clip(out, -1.0, 1.0)
        outdata[:, 0] = out
        if outdata.shape[1] > 1:
            outdata[:, 1] = out
    except Exception:
        self.phase = 0.0
        self.current_table = fallback_table()
        self.current_volume = 0.0
        self.current_frequency = DEFAULT_FREQUENCY
        self.current_fm = 0.0
        self.current_pwm = 0.0
        self.current_am = 0.0
        self.current_fm_range_cents = DEFAULT_FM_RANGE_CENTS
        self.current_before_start = 0.0
        self.current_before_length = WINDOW_STEPS
        self.current_after_start = 0.0
        self.current_after_length = WINDOW_STEPS
        try:
            outdata.fill(0.0)
        except Exception:
            pass


WavetableOscillator.callback = _bounded_fm_audio_callback




# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: UI construction helper: _add_fm_modulation_controls creates a reusable group of Tk
# TUTORIAL: controls. Keeping these small avoids one enormous constructor.
# TUTORIAL: ------------------------------------------------------------------------
def _add_fm_modulation_controls(self) -> None:
    """FM slider row with adjacent bounded pitch-range selector."""
    frame = tk.Frame(self.control_parent)
    frame.pack(fill="x", padx=12, pady=4)

    tk.Label(frame, text="FM self", width=16, anchor="w").pack(side="left")
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Tk Scale is the slider widget. In this app, slider callbacks push new values
    # TUTORIAL: into patch or audio state.
    # TUTORIAL: ------------------------------------------------------------------------
    slider = tk.Scale(
        frame,
        variable=self.vars["fm"],
        from_=0,
        to=255,
        resolution=1,
        orient="horizontal",
        length=330,
        command=lambda _value: self._push_modulation(source="fm"),
    )
    slider.pack(side="left", fill="x", expand=True)

    if not hasattr(self, "fm_range_var"):
        self.fm_range_var = tk.StringVar(value=DEFAULT_FM_RANGE_LABEL)
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
    # TUTORIAL: callback may touch it at the same time.
    # TUTORIAL: ------------------------------------------------------------------------
    with self.state.lock:
        self.state.target_fm_range_cents = fm_range_label_to_cents(self.fm_range_var.get())

    tk.Label(frame, text="Range").pack(side="left", padx=(8, 4))
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: OptionMenu is used for compact dropdowns such as wavetable type, pitch range,
    # TUTORIAL: and export settings.
    # TUTORIAL: ------------------------------------------------------------------------
    menu = tk.OptionMenu(
        frame,
        self.fm_range_var,
        *[label for label, _cents in FM_RANGE_OPTIONS],
        command=lambda _value: self._push_fm_range(),
    )
    menu.config(width=20)
    menu.pack(side="left")

    self.fm_range_value_label = tk.Label(frame, width=16, anchor="e", text=f"±{DEFAULT_FM_RANGE_CENTS:.0f} cents")
    self.fm_range_value_label.pack(side="left", padx=(8, 0))


App._add_fm_modulation_controls = _add_fm_modulation_controls

_previous_app_init_for_fm_range = App.__init__


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _fm_range_app_init: small named steps make the signal path, UI path,
# TUTORIAL: and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _fm_range_app_init(self, *args, **kwargs):
    _previous_app_init_for_fm_range(self, *args, **kwargs)

    # Add a frequency-referenced visualisation mode. The phase-locked view is
    # useful for shape, but it intentionally hides effective pitch changes.
    try:
        for child in self.visual_parent.winfo_children():
            if isinstance(child, tk.Frame):
                already = False
                for sub in child.winfo_children():
                    try:
                        already = already or str(sub.cget("text")) == "Time-domain FM/PWM/AM"
                    except Exception:
                        pass
                if not already:
                    tk.Radiobutton(
                        child,
                        text="Time-domain FM/PWM/AM",
                        variable=self.visualizer_mode,
                        value="timedomain",
                        command=self._draw_current_waveform,
                    ).pack(side="left", padx=(10, 0))
                break
    except Exception:
        pass

    if not hasattr(self, "fm_range_var"):
        self.fm_range_var = tk.StringVar(value=DEFAULT_FM_RANGE_LABEL)
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
    # TUTORIAL: callback may touch it at the same time.
    # TUTORIAL: ------------------------------------------------------------------------
    with self.state.lock:
        self.state.target_fm_range_cents = fm_range_label_to_cents(self.fm_range_var.get())
    self._push_fm_range(update_status=False)

App.__init__ = _fm_range_app_init


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _push_fm_range: small named steps make the signal path, UI path, and
# TUTORIAL: export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _push_fm_range(self, update_status: bool = True) -> None:
    label = self.fm_range_var.get() if hasattr(self, "fm_range_var") else DEFAULT_FM_RANGE_LABEL
    cents = fm_range_label_to_cents(label)
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
    # TUTORIAL: callback may touch it at the same time.
    # TUTORIAL: ------------------------------------------------------------------------
    with self.state.lock:
        self.state.target_fm_range_cents = cents
    if hasattr(self, "fm_range_value_label"):
        if cents >= 1200.0:
            self.fm_range_value_label.config(text=f"±{cents / 1200.0:g} octave(s)")
        else:
            self.fm_range_value_label.config(text=f"±{cents:.0f} cents")
    if update_status and hasattr(self, "status"):
        self.status.config(text=f"FM RANGE  {label}")
    if hasattr(self, "wave_canvas"):
        self._draw_current_waveform()


App._push_fm_range = _push_fm_range


_previous_push_modulation_for_fm_range = App._push_modulation


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _push_modulation_with_fm_range: small named steps make the signal
# TUTORIAL: path, UI path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _push_modulation_with_fm_range(self, *, source: str = "modulation") -> None:
    _previous_push_modulation_for_fm_range(self, source=source)
    if hasattr(self, "fm_range_var"):
        self._push_fm_range(update_status=False)


App._push_modulation = _push_modulation_with_fm_range


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: DSP render helper: render_selfmod_phase_locked produces an offline or preview
# TUTORIAL: waveform from tables and modulation settings.
# TUTORIAL: ------------------------------------------------------------------------
def render_selfmod_phase_locked(
    table: np.ndarray,
    fm: float,
    pwm: float,
    am: float,
    *,
    frames: int = 512,
    depth_scale: float = 1.0,
    fm_range_cents: float = DEFAULT_FM_RANGE_CENTS,
) -> np.ndarray:  # type: ignore[override]
    """Horizontally stable one-cycle view using bounded FM range."""
    if not table_is_usable(table):
        table = fallback_table()
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
    # TUTORIAL: samples, and table generation can sometimes hit extreme math.
    # TUTORIAL: ------------------------------------------------------------------------
    src = np.nan_to_num(table.astype(np.float32), nan=0.0, posinf=1.0, neginf=-1.0)

    frames = max(int(frames), 32)
    depth_scale = clamp_float(depth_scale, 0.0, 1.0)
    pwm_depth = clamp_float(pwm, 0.0, 255.0) / 255.0 * 0.48 * depth_scale
    am_depth = clamp_float(am, 0.0, 255.0) / 255.0 * depth_scale
    fm_range_cents = clamp_float(fm_range_cents, 0.0, MAX_FM_RANGE_CENTS)

    nominal_phase = np.linspace(0.0, 1.0, frames, endpoint=False, dtype=np.float64)
    rates = np.empty(frames, dtype=np.float64)
    for i, ph in enumerate(nominal_phase):
        mod_sample = read_table_linear(src, float(ph))
        am_gain_for_mod = (1.0 - am_depth) + am_depth * (0.5 + 0.5 * mod_sample)
        am_gain_for_mod = clamp_float(am_gain_for_mod, 0.0, 1.5)
        mod_signal = clamp_float(mod_sample * am_gain_for_mod, -1.0, 1.0)
        rates[i] = bounded_fm_phase_scale(mod_signal, fm, fm_range_cents, depth_scale=depth_scale)

    total = float(np.sum(rates))
    if (not math.isfinite(total)) or total <= EPSILON:
        phase_map = nominal_phase
    else:
        cumulative = np.concatenate(([0.0], np.cumsum(rates[:-1])))
        phase_map = (cumulative / total) % 1.0

    out = np.empty(frames, dtype=np.float64)
    for i, math_phase in enumerate(phase_map):
        mod_sample = read_table_linear(src, float(math_phase))
        am_gain = (1.0 - am_depth) + am_depth * (0.5 + 0.5 * mod_sample)
        am_gain = clamp_float(am_gain, 0.0, 1.5)
        mod_signal = clamp_float(mod_sample * am_gain, -1.0, 1.0)

        width = clamp_float(0.5 + pwm_depth * mod_signal, 0.02, 0.98)
        if math_phase < width:
            read_phase = (math_phase / width) * 0.5
        else:
            read_phase = 0.5 + ((math_phase - width) / (1.0 - width)) * 0.5

        sample = read_table_linear(src, read_phase)
        out[i] = sample * am_gain

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
    # TUTORIAL: samples, and table generation can sometimes hit extreme math.
    # TUTORIAL: ------------------------------------------------------------------------
    out = np.nan_to_num(out, nan=0.0, posinf=1.0, neginf=-1.0)
    peak = float(np.max(np.abs(out)))
    if math.isfinite(peak) and peak > 1.0:
        out = out / peak
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Clip the signal/control range after math that might overshoot. This keeps audio
    # TUTORIAL: buffers and tables bounded.
    # TUTORIAL: ------------------------------------------------------------------------
    return np.clip(out, -1.0, 1.0).astype(np.float32)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: DSP render helper: render_selfmod_time_domain produces an offline or preview
# TUTORIAL: waveform from tables and modulation settings.
# TUTORIAL: ------------------------------------------------------------------------
def render_selfmod_time_domain(
    table: np.ndarray,
    fm: float,
    pwm: float,
    am: float,
    frequency: float,
    fm_range_cents: float,
    *,
    frames: int = 512,
    nominal_cycles: float = 1.0,
) -> np.ndarray:
    """Frequency-referenced preview.

    This draws one nominal unmodulated period across the screen. It does not
    force the modulated phase to complete exactly one cycle, so FM-caused pitch
    shifts are visible as fewer/more cycles across the square.
    """
    if not table_is_usable(table):
        table = fallback_table()
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
    # TUTORIAL: samples, and table generation can sometimes hit extreme math.
    # TUTORIAL: ------------------------------------------------------------------------
    src = np.nan_to_num(table.astype(np.float32), nan=0.0, posinf=1.0, neginf=-1.0)

    frames = max(int(frames), 32)
    pwm_depth = clamp_float(pwm, 0.0, 255.0) / 255.0 * 0.48
    am_depth = clamp_float(am, 0.0, 255.0) / 255.0
    fm_range_cents = clamp_float(fm_range_cents, 0.0, MAX_FM_RANGE_CENTS)

    # Nominal one-cycle display. Frequency does not change the number of pixels;
    # it is included so the mode conceptually matches the selected pitch.
    _frequency = clamp_float(frequency, MIN_FREQUENCY, MAX_FREQUENCY)
    base_phase_inc = float(nominal_cycles) / float(frames)
    phase = 0.0
    out = np.empty(frames, dtype=np.float64)

    for i in range(frames):
        mod_sample = read_table_linear(src, phase)
        am_gain = (1.0 - am_depth) + am_depth * (0.5 + 0.5 * mod_sample)
        am_gain = clamp_float(am_gain, 0.0, 1.5)
        mod_signal = clamp_float(mod_sample * am_gain, -1.0, 1.0)

        width = clamp_float(0.5 + pwm_depth * mod_signal, 0.02, 0.98)
        if phase < width:
            read_phase = (phase / width) * 0.5
        else:
            read_phase = 0.5 + ((phase - width) / (1.0 - width)) * 0.5

        out[i] = read_table_linear(src, read_phase) * am_gain
        phase = (phase + base_phase_inc * bounded_fm_phase_scale(mod_signal, fm, fm_range_cents)) % 1.0

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
    # TUTORIAL: samples, and table generation can sometimes hit extreme math.
    # TUTORIAL: ------------------------------------------------------------------------
    out = np.nan_to_num(out, nan=0.0, posinf=1.0, neginf=-1.0)
    peak = float(np.max(np.abs(out)))
    if math.isfinite(peak) and peak > 1.0:
        out = out / peak
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Clip the signal/control range after math that might overshoot. This keeps audio
    # TUTORIAL: buffers and tables bounded.
    # TUTORIAL: ------------------------------------------------------------------------
    return np.clip(out, -1.0, 1.0).astype(np.float32)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Export helper: _fm_range_draw_waveform turns in-memory rendered audio/settings into
# TUTORIAL: files on disk. This is separate from live playback by design.
# TUTORIAL: ------------------------------------------------------------------------
def _fm_range_draw_waveform(self, table: np.ndarray) -> None:
    if not hasattr(self, "wave_canvas"):
        return

    mode = getattr(self, "visualizer_mode", tk.StringVar(value="base")).get()
    label = "one cycle, final generated table"
    y = np.asarray(table, dtype=np.float64)

    if mode in {"rendered", "slow", "timedomain"}:
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
        # TUTORIAL: callback may touch it at the same time.
        # TUTORIAL: ------------------------------------------------------------------------
        with self.state.lock:
            fm = float(self.state.target_fm)
            pwm = float(self.state.target_pwm)
            am = float(self.state.target_am)
            frequency = float(self.state.target_frequency)
            fm_range_cents = float(getattr(self.state, "target_fm_range_cents", DEFAULT_FM_RANGE_CENTS))

        if mode == "slow":
            phase = getattr(self, "visual_anim_phase", 0.0)
            depth_scale = 0.5 - 0.5 * math.cos(2.0 * math.pi * phase)
            label = f"animated stable FM/PWM/AM depth sweep: {depth_scale:.2f}×"
            y = render_selfmod_phase_locked(
                np.asarray(table, dtype=np.float32),
                fm=fm,
                pwm=pwm,
                am=am,
                depth_scale=depth_scale,
                fm_range_cents=fm_range_cents,
            )
        elif mode == "timedomain":
            label = "time-domain FM/PWM/AM: one nominal period; pitch shifts remain visible"
            y = render_selfmod_time_domain(
                np.asarray(table, dtype=np.float32),
                fm=fm,
                pwm=pwm,
                am=am,
                frequency=frequency,
                fm_range_cents=fm_range_cents,
            )
        else:
            label = "phase-locked rendered FM/PWM/AM"
            y = render_selfmod_phase_locked(
                np.asarray(table, dtype=np.float32),
                fm=fm,
                pwm=pwm,
                am=am,
                depth_scale=1.0,
                fm_range_cents=fm_range_cents,
            )

    canvas = self.wave_canvas
    width = max(int(canvas.winfo_width()), int(self.wave_canvas_size))
    height = max(int(canvas.winfo_height()), int(self.wave_canvas_size))
    pad = 18
    plot_w = max(width - 2 * pad, 1)
    plot_h = max(height - 2 * pad, 1)
    mid_y = pad + plot_h / 2.0
    amp = plot_h * 0.46

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
    # TUTORIAL: samples, and table generation can sometimes hit extreme math.
    # TUTORIAL: ------------------------------------------------------------------------
    y = np.nan_to_num(np.asarray(y, dtype=np.float64).reshape(-1), nan=0.0, posinf=1.0, neginf=-1.0)
    if y.size < 2:
        y = fallback_table().astype(np.float64)
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Clip the signal/control range after math that might overshoot. This keeps audio
    # TUTORIAL: buffers and tables bounded.
    # TUTORIAL: ------------------------------------------------------------------------
    y = np.clip(y, -1.0, 1.0)
    n = int(y.size)

    canvas.delete("all")
    canvas.create_rectangle(pad, pad, pad + plot_w, pad + plot_h, outline="#444444")
    canvas.create_line(pad, mid_y, pad + plot_w, mid_y, fill="#333333")
    canvas.create_line(pad, pad, pad + plot_w, pad, fill="#222222")
    canvas.create_line(pad, pad + plot_h, pad + plot_w, pad + plot_h, fill="#222222")

    points: list[float] = []
    for i in range(n):
        sample = float(y[i])
        x = pad + plot_w * (i / max(n - 1, 1))
        yy = mid_y - sample * amp
        points.extend((x, yy))
    canvas.create_line(*points, fill="#00d0ff", width=2, smooth=False)
    canvas.create_text(pad, height - 6, anchor="sw", fill="#999999", text=label)


App._draw_waveform = _fm_range_draw_waveform


_previous_reset_all_for_fm_range = App.reset_all


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _reset_all_with_fm_range: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _reset_all_with_fm_range(self) -> None:
    _previous_reset_all_for_fm_range(self)
    if hasattr(self, "fm_range_var"):
        self.fm_range_var.set(DEFAULT_FM_RANGE_LABEL)
        self._push_fm_range(update_status=False)
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
    # TUTORIAL: callback may touch it at the same time.
    # TUTORIAL: ------------------------------------------------------------------------
    with self.state.lock:
        self.state.target_fm_range_cents = DEFAULT_FM_RANGE_CENTS
        self.state.reset_requested = True
    if hasattr(self, "status"):
        self.status.config(text="RESET ALL  defaults pushed directly to audio engine")


App.reset_all = _reset_all_with_fm_range


_previous_panic_audio_for_fm_range = App.panic_audio


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Audio helper: _panic_audio_with_fm_range bridges UI state and the audio engine. The
# TUTORIAL: main rule is to keep blocking work out of the real-time callback.
# TUTORIAL: ------------------------------------------------------------------------
def _panic_audio_with_fm_range(self) -> None:
    _previous_panic_audio_for_fm_range(self)
    if hasattr(self, "fm_range_var"):
        self.fm_range_var.set(DEFAULT_FM_RANGE_LABEL)
        self._push_fm_range(update_status=False)
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
    # TUTORIAL: callback may touch it at the same time.
    # TUTORIAL: ------------------------------------------------------------------------
    with self.state.lock:
        self.state.target_fm_range_cents = DEFAULT_FM_RANGE_CENTS
        self.state.reset_requested = True


App.panic_audio = _panic_audio_with_fm_range


# -----------------------------------------------------------------------------
# Audio stall watchdog / push-start override
#
# Some wavetable settings, especially bitwise modes, can create long plateaus
# or otherwise leave the audio callback outputting the same value for too long.
# This layer watches the rendered output, not just the control values. If the
# stream becomes effectively DC/constant while the synth is expected to be
# audible, it nudges the oscillator phase to the next moving part of the table.
# If the table itself has collapsed to a constant, it replaces it with the
# fallback square and reports the recovery in the status line.
# -----------------------------------------------------------------------------

STALL_OUTPUT_SPAN_EPS = 1e-8
STALL_TABLE_EDGE_EPS = 1e-5
STALL_VOLUME_THRESHOLD = 1e-5
STALL_DETECT_CYCLES = 3.0
STALL_DETECT_SECONDS = 0.025
STALL_PRONE_TABLE_RUN_FRACTION = 0.49
STALL_PRONE_TABLE_RUN_SAMPLES = int(TABLE_SIZE * STALL_PRONE_TABLE_RUN_FRACTION)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function table_motion_stats: small named steps make the signal path, UI path,
# TUTORIAL: and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def table_motion_stats(table: np.ndarray, *, edge_eps: float = STALL_TABLE_EDGE_EPS) -> tuple[int, int, float]:
    """Return edge count, longest same-value run, and table span."""
    if not table_is_usable(table):
        return 0, TABLE_SIZE, 0.0

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
    # TUTORIAL: samples, and table generation can sometimes hit extreme math.
    # TUTORIAL: ------------------------------------------------------------------------
    y = np.nan_to_num(np.asarray(table, dtype=np.float64).reshape(-1), nan=0.0, posinf=1.0, neginf=-1.0)
    if y.size != TABLE_SIZE:
        return 0, TABLE_SIZE, 0.0

    span = float(np.max(y) - np.min(y))
    diffs = np.abs(np.diff(np.r_[y, y[0]]))
    moving = diffs > edge_eps
    edge_count = int(np.count_nonzero(moving))

    same = ~moving
    longest = 0
    run = 0
    # Circular table: duplicate once so a run crossing the wrap is counted.
    for is_same in np.r_[same, same]:
        if bool(is_same):
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    longest = min(longest, TABLE_SIZE)
    return edge_count, longest, span


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function next_moving_phase: small named steps make the signal path, UI path,
# TUTORIAL: and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def next_moving_phase(table: np.ndarray, current_phase: float, *, edge_eps: float = STALL_TABLE_EDGE_EPS) -> float:
    """Find a phase just after the next table edge, used as a push-start."""
    if not table_is_usable(table):
        return 0.0

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
    # TUTORIAL: samples, and table generation can sometimes hit extreme math.
    # TUTORIAL: ------------------------------------------------------------------------
    y = np.nan_to_num(np.asarray(table, dtype=np.float64).reshape(-1), nan=0.0, posinf=1.0, neginf=-1.0)
    diffs = np.abs(np.diff(np.r_[y, y[0]]))
    edges = np.flatnonzero(diffs > edge_eps)
    if edges.size == 0:
        return 0.0

    current_index = int((float(current_phase) % 1.0) * TABLE_SIZE) % TABLE_SIZE
    distances = (edges - current_index) % TABLE_SIZE
    distances[distances == 0] = TABLE_SIZE
    edge = int(edges[int(np.argmin(distances))])
    # Land just after the edge, not directly on the discontinuity.
    return ((edge + 2) % TABLE_SIZE) / float(TABLE_SIZE)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: DSP render helper: render_fallback_push_block produces an offline or preview
# TUTORIAL: waveform from tables and modulation settings.
# TUTORIAL: ------------------------------------------------------------------------
def render_fallback_push_block(self, outdata, frames: int) -> None:
    """Fill one output block with a known-good square wave so recovery is audible immediately."""
    table = fallback_table()
    frequency = clamp_float(getattr(self, "current_frequency", DEFAULT_FREQUENCY), MIN_FREQUENCY, MAX_FREQUENCY)
    volume = clamp_float(getattr(self, "current_volume", 0.20), 0.0, 1.0)
    if volume <= STALL_VOLUME_THRESHOLD:
        volume = 0.20
    phase = float(getattr(self, "phase", 0.0)) % 1.0
    phase_inc = clamp_float(frequency / float(getattr(self, "sample_rate", DEFAULT_SAMPLE_RATE)), 0.0, 0.49)
    out = np.empty(frames, dtype=np.float32)
    for i in range(frames):
        out[i] = read_table_linear(table, phase) * volume
        phase = (phase + phase_inc) % 1.0
    self.phase = phase
    outdata[:, 0] = out
    if outdata.shape[1] > 1:
        outdata[:, 1] = out


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function force_audio_push_start: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def force_audio_push_start(self, reason: str, *, force_fallback_table: bool) -> None:
    """Recover the oscillator from a DC/constant-output stall."""
    table = getattr(self, "current_table", fallback_table())
    edge_count, longest_run, _span = table_motion_stats(table)

    if force_fallback_table or edge_count == 0:
        recovery_table = fallback_table()
        self.current_table = recovery_table.copy()
        self.current_fm = 0.0
        self.current_pwm = 0.0
        self.current_am = 0.0
        self.current_before_start = 0.0
        self.current_before_length = WINDOW_STEPS
        self.current_after_start = 0.0
        self.current_after_length = WINDOW_STEPS
        self.phase = 0.0
        recovery = "fallback square pushed"
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
        # TUTORIAL: callback may touch it at the same time.
        # TUTORIAL: ------------------------------------------------------------------------
        with self.state.lock:
            self.state.target_table = recovery_table
            self.state.target_fm = 0.0
            self.state.target_pwm = 0.0
            self.state.target_am = 0.0
            self.state.target_before_start = 0.0
            self.state.target_before_length = WINDOW_STEPS
            self.state.target_after_start = 0.0
            self.state.target_after_length = WINDOW_STEPS
            self.state.reset_requested = True
            self.state.audio_watchdog_message = f"AUDIO WATCHDOG  {reason}; {recovery}"
    else:
        self.phase = next_moving_phase(table, getattr(self, "phase", 0.0))
        recovery = f"phase push-start to {self.phase:.3f}"
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
        # TUTORIAL: callback may touch it at the same time.
        # TUTORIAL: ------------------------------------------------------------------------
        with self.state.lock:
            self.state.reset_requested = True
            self.state.audio_watchdog_message = (
                f"AUDIO WATCHDOG  {reason}; {recovery}; table edges={edge_count}, longest plateau={longest_run} samples"
            )

    self.audio_stall_cycles = 0.0
    self.audio_stall_seconds = 0.0


_previous_audio_callback_for_stall_watchdog = WavetableOscillator.callback


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Audio helper: _stall_watchdog_audio_callback bridges UI state and the audio engine.
# TUTORIAL: The main rule is to keep blocking work out of the real-time callback.
# TUTORIAL: ------------------------------------------------------------------------
def _stall_watchdog_audio_callback(self, outdata, frames, time, status):
    try:
        _previous_audio_callback_for_stall_watchdog(self, outdata, frames, time, status)
    except Exception as exc:
        # Do not let a callback exception leave PortAudio permanently silent.
        try:
            outdata.fill(0.0)
        except Exception:
            pass
        force_audio_push_start(self, f"callback exception {type(exc).__name__}", force_fallback_table=True)
        render_fallback_push_block(self, outdata, frames)
        return

    try:
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
        # TUTORIAL: callback may touch it at the same time.
        # TUTORIAL: ------------------------------------------------------------------------
        with self.state.lock:
            target_volume = clamp_float(getattr(self.state, "target_volume", 0.0), 0.0, 1.0)
            target_frequency = clamp_float(getattr(self.state, "target_frequency", DEFAULT_FREQUENCY), MIN_FREQUENCY, MAX_FREQUENCY)

        # Do not recover deliberate silence.
        if target_volume <= STALL_VOLUME_THRESHOLD:
            self.audio_stall_cycles = 0.0
            self.audio_stall_seconds = 0.0
            return

        block = np.asarray(outdata[:, 0], dtype=np.float64)
        finite = bool(np.all(np.isfinite(block)))
        if finite:
            block_span = float(np.max(block) - np.min(block))
            block_stalled = block_span <= STALL_OUTPUT_SPAN_EPS
            reason = f"constant output span={block_span:.2e}"
        else:
            block_stalled = True
            reason = "non-finite output block"

        if block_stalled:
            cycles_this_block = frames * target_frequency / float(getattr(self, "sample_rate", DEFAULT_SAMPLE_RATE))
            seconds_this_block = frames / float(getattr(self, "sample_rate", DEFAULT_SAMPLE_RATE))
            self.audio_stall_cycles = float(getattr(self, "audio_stall_cycles", 0.0)) + cycles_this_block
            self.audio_stall_seconds = float(getattr(self, "audio_stall_seconds", 0.0)) + seconds_this_block

            if self.audio_stall_cycles >= STALL_DETECT_CYCLES or self.audio_stall_seconds >= STALL_DETECT_SECONDS:
                table = getattr(self, "current_table", fallback_table())
                edge_count, _longest_run, table_span = table_motion_stats(table)
                force_fallback = (edge_count == 0) or (table_span <= STALL_TABLE_EDGE_EPS)
                force_audio_push_start(self, reason, force_fallback_table=force_fallback)
                if force_fallback:
                    render_fallback_push_block(self, outdata, frames)
        else:
            self.audio_stall_cycles = 0.0
            self.audio_stall_seconds = 0.0
    except Exception:
        # Watchdog failure should never become an audio failure.
        self.audio_stall_cycles = 0.0
        self.audio_stall_seconds = 0.0


WavetableOscillator.callback = _stall_watchdog_audio_callback


_previous_push_table_for_stall_notes = App._push_table


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _push_table_with_stall_notes: small named steps make the signal
# TUTORIAL: path, UI path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _push_table_with_stall_notes(
    self,
    p1: int,
    p2: int,
    p3: int,
    wt_type: int,
    volume: float,
    *,
    hard_reset: bool,
) -> tuple[bool, tuple[int, int, int, int, float], list[str]]:
    ok, effective, notes = _previous_push_table_for_stall_notes(
        self, p1, p2, p3, wt_type, volume, hard_reset=hard_reset
    )
    if ok:
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
        # TUTORIAL: callback may touch it at the same time.
        # TUTORIAL: ------------------------------------------------------------------------
        with self.state.lock:
            table = self.state.target_table.copy()
        edge_count, longest_run, _span = table_motion_stats(table)
        extra: list[str] = []
        if longest_run >= STALL_PRONE_TABLE_RUN_SAMPLES:
            extra.append(f"stall-prone table plateau {longest_run}/{TABLE_SIZE} samples")
        if edge_count == 0:
            extra.append("table has no moving edge; watchdog will force fallback square")
        if extra:
            self.table_notes = self.table_notes + extra
            self._refresh_clip_indicator()
            notes = notes + extra
    return ok, effective, notes


App._push_table = _push_table_with_stall_notes


_previous_poll_messages_for_stall_watchdog = App._poll_messages


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _poll_messages_with_stall_watchdog: small named steps make the
# TUTORIAL: signal path, UI path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _poll_messages_with_stall_watchdog(self) -> None:
    watchdog_message = ""
    try:
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
        # TUTORIAL: callback may touch it at the same time.
        # TUTORIAL: ------------------------------------------------------------------------
        with self.state.lock:
            watchdog_message = str(getattr(self.state, "audio_watchdog_message", ""))
            if watchdog_message:
                self.state.audio_watchdog_message = ""
    except Exception:
        watchdog_message = ""

    if watchdog_message and hasattr(self, "status"):
        self.status.config(text=watchdog_message)
    _previous_poll_messages_for_stall_watchdog(self)


App._poll_messages = _poll_messages_with_stall_watchdog




# -----------------------------------------------------------------------------
# Stream-level audio recovery override
#
# A non-collapsed visual table plus no audio after Reset/Panic means the fault is
# probably below the wavetable/oscillator layer: the PortAudio stream may have
# stopped receiving callbacks, or the callback may be repeatedly failing before
# producing a valid block. Resetting oscillator state cannot repair a dead stream,
# so Panic/Reset now perform a hard stream restart as well.
# -----------------------------------------------------------------------------


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Audio helper: _diagnostic_audio_callback bridges UI state and the audio engine. The
# TUTORIAL: main rule is to keep blocking work out of the real-time callback.
# TUTORIAL: ------------------------------------------------------------------------
def _diagnostic_audio_callback(self, outdata, frames, time, status):
    """Outermost audio callback wrapper with heartbeat + block diagnostics."""
    if not hasattr(self, "audio_callback_count"):
        self.audio_callback_count = 0
        self.last_output_span = 0.0
        self.last_output_rms = 0.0
        self.last_callback_error = ""

    try:
        _previous_audio_callback_for_stream_diagnostics(self, outdata, frames, time, status)
        self.last_callback_error = ""
    except Exception as exc:
        # This should be rare because earlier wrappers also catch exceptions.
        # Keep the stream alive and make the failure visible to the GUI.
        self.last_callback_error = f"{type(exc).__name__}: {exc}"
        try:
            render_fallback_push_block(self, outdata, frames)
        except Exception:
            try:
                outdata.fill(0.0)
            except Exception:
                pass
        try:
            # TUTORIAL: ------------------------------------------------------------------------
            # TUTORIAL: SharedState is read or written under a lock because the GUI thread and
            # TUTORIAL: audio callback may touch it at the same time.
            # TUTORIAL: ------------------------------------------------------------------------
            with self.state.lock:
                self.state.audio_watchdog_message = f"AUDIO CALLBACK ERROR  {self.last_callback_error}"
        except Exception:
            pass
    finally:
        self.audio_callback_count = int(getattr(self, "audio_callback_count", 0)) + 1
        try:
            block = np.asarray(outdata[:, 0], dtype=np.float64)
            self.last_output_span = float(np.max(block) - np.min(block))
            self.last_output_rms = float(np.sqrt(np.mean(block * block)))
        except Exception:
            self.last_output_span = 0.0
            self.last_output_rms = 0.0


_previous_audio_callback_for_stream_diagnostics = WavetableOscillator.callback
WavetableOscillator.callback = _diagnostic_audio_callback


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Wrapper around sounddevice streams. Keeping stream start/stop/restart logic in one
# TUTORIAL: class makes recovery from bad audio states less tangled with the UI code.
# TUTORIAL: ------------------------------------------------------------------------
class AudioEngine:
    """Small controller that can rebuild the PortAudio stream on demand."""

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function __init__: small named steps make the signal path, UI path, and
    # TUTORIAL: export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def __init__(self, state: SharedState, samplerate: int, device=None):
        self.state = state
        self.samplerate = int(samplerate)
        self.device = device
        self.stream = None
        self.osc: WavetableOscillator | None = None
        self.lock = threading.RLock()
        self.restart_count = 0
        self.last_status = "not started"
        self.last_callback_count: int | None = None
        self.closed = False
        self.start("initial start")

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function start: small named steps make the signal path, UI path, and
    # TUTORIAL: export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def start(self, reason: str = "start") -> None:
        with self.lock:
            if self.closed:
                return
            self.osc = WavetableOscillator(self.state, sample_rate=self.samplerate)
            self.stream = sd.OutputStream(
                device=self.device,
                samplerate=self.samplerate,
                channels=2,
                dtype="float32",
                blocksize=256,
                latency="low",
                callback=self.osc.callback,
            )
            self.stream.start()
            self.last_callback_count = None
            self.last_status = f"audio stream started ({reason})"
            try:
                # TUTORIAL: ------------------------------------------------------------------------
                # TUTORIAL: SharedState is read or written under a lock because the GUI thread
                # TUTORIAL: and audio callback may touch it at the same time.
                # TUTORIAL: ------------------------------------------------------------------------
                with self.state.lock:
                    self.state.audio_watchdog_message = self.last_status
            except Exception:
                pass

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function _stop_current_stream: small named steps make the signal path, UI
    # TUTORIAL: path, and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def _stop_current_stream(self) -> None:
        stream = self.stream
        self.stream = None
        if stream is None:
            return
        for method_name in ("abort", "stop", "close"):
            try:
                method = getattr(stream, method_name, None)
                if method is not None:
                    method()
            except Exception:
                pass

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function hard_restart: small named steps make the signal path, UI path,
    # TUTORIAL: and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def hard_restart(self, reason: str, *, force_fallback_state: bool = False) -> None:
        with self.lock:
            if self.closed:
                return
            self.restart_count += 1
            if force_fallback_state:
                table = fallback_table()
                # TUTORIAL: ------------------------------------------------------------------------
                # TUTORIAL: SharedState is read or written under a lock because the GUI thread
                # TUTORIAL: and audio callback may touch it at the same time.
                # TUTORIAL: ------------------------------------------------------------------------
                with self.state.lock:
                    self.state.target_table = table
                    self.state.target_volume = 0.20
                    self.state.target_frequency = clamp_float(
                        getattr(self.state, "target_frequency", DEFAULT_FREQUENCY),
                        MIN_FREQUENCY,
                        MAX_FREQUENCY,
                    )
                    self.state.target_fm = 0.0
                    self.state.target_pwm = 0.0
                    self.state.target_am = 0.0
                    self.state.target_before_start = 0.0
                    self.state.target_before_length = WINDOW_STEPS
                    self.state.target_after_start = 0.0
                    self.state.target_after_length = WINDOW_STEPS
                    self.state.target_fm_range_cents = DEFAULT_FM_RANGE_CENTS
                    self.state.reset_requested = True
            else:
                # TUTORIAL: ------------------------------------------------------------------------
                # TUTORIAL: SharedState is read or written under a lock because the GUI thread
                # TUTORIAL: and audio callback may touch it at the same time.
                # TUTORIAL: ------------------------------------------------------------------------
                with self.state.lock:
                    self.state.reset_requested = True
                    sanitise_shared_state(self.state)

            self._stop_current_stream()
            self.start(reason)
            self.last_status = f"AUDIO STREAM RESTART  {reason}  count={self.restart_count}"
            try:
                # TUTORIAL: ------------------------------------------------------------------------
                # TUTORIAL: SharedState is read or written under a lock because the GUI thread
                # TUTORIAL: and audio callback may touch it at the same time.
                # TUTORIAL: ------------------------------------------------------------------------
                with self.state.lock:
                    self.state.audio_watchdog_message = self.last_status
            except Exception:
                pass

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function close: small named steps make the signal path, UI path, and
    # TUTORIAL: export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def close(self) -> None:
        with self.lock:
            self.closed = True
            self._stop_current_stream()
            self.last_status = "audio stream closed"

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function is_active: small named steps make the signal path, UI path, and
    # TUTORIAL: export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def is_active(self) -> bool:
        stream = self.stream
        if stream is None:
            return False
        try:
            return bool(getattr(stream, "active"))
        except Exception:
            return False

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function callback_count: small named steps make the signal path, UI path,
    # TUTORIAL: and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def callback_count(self) -> int:
        osc = self.osc
        if osc is None:
            return 0
        return int(getattr(osc, "audio_callback_count", 0))

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function last_audio_stats: small named steps make the signal path, UI
    # TUTORIAL: path, and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def last_audio_stats(self) -> tuple[float, float, str]:
        osc = self.osc
        if osc is None:
            return 0.0, 0.0, "no oscillator"
        return (
            float(getattr(osc, "last_output_span", 0.0)),
            float(getattr(osc, "last_output_rms", 0.0)),
            str(getattr(osc, "last_callback_error", "")),
        )

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function check_health: small named steps make the signal path, UI path,
    # TUTORIAL: and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def check_health(self) -> None:
        """Restart if the stream has stopped or callbacks stop advancing."""
        if self.closed:
            return
        if self.stream is None:
            self.hard_restart("missing stream")
            return

        active = self.is_active()
        count = self.callback_count()
        if not active:
            self.hard_restart("inactive stream")
            return

        if self.last_callback_count is None:
            self.last_callback_count = count
            return

        if count == self.last_callback_count:
            self.hard_restart("callback heartbeat stopped")
            return

        self.last_callback_count = count


# Add GUI access to hard stream recovery. This is intentionally after all earlier
# App method overrides so it wraps the final reset/panic methods.
_previous_app_init_for_stream_recovery = App.__init__


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _stream_recovery_app_init: small named steps make the signal path,
# TUTORIAL: UI path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _stream_recovery_app_init(self, *args, **kwargs):
    _previous_app_init_for_stream_recovery(self, *args, **kwargs)
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Tk uses scheduled callbacks instead of long blocking loops. after(...) lets the
    # TUTORIAL: UI stay responsive.
    # TUTORIAL: ------------------------------------------------------------------------
    self.root.after(1000, self._audio_health_tick)

    # Add a manual stream restart button next to the existing reset/panic buttons.
    try:
        for child in self.control_parent.winfo_children():
            if isinstance(child, tk.Frame):
                buttons = [sub for sub in child.winfo_children() if isinstance(sub, tk.Button)]
                labels = {str(button.cget("text")) for button in buttons}
                if "Panic audio" in labels and "Restart audio stream" not in labels:
                    tk.Button(
                        child,
                        text="Restart audio stream",
                        command=self.restart_audio_stream,
                    ).pack(side="left", padx=(8, 0))
                    break
    except Exception:
        pass


App.__init__ = _stream_recovery_app_init


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Audio helper: _audio_health_tick bridges UI state and the audio engine. The main
# TUTORIAL: rule is to keep blocking work out of the real-time callback.
# TUTORIAL: ------------------------------------------------------------------------
def _audio_health_tick(self) -> None:
    try:
        engine = getattr(self, "audio_engine", None)
        if engine is not None:
            engine.check_health()
    except Exception as exc:
        if hasattr(self, "status"):
            self.status.config(text=f"AUDIO HEALTH CHECK ERROR  {type(exc).__name__}: {exc}")
    finally:
        try:
            # TUTORIAL: ------------------------------------------------------------------------
            # TUTORIAL: Tk uses scheduled callbacks instead of long blocking loops. after(...)
            # TUTORIAL: lets the UI stay responsive.
            # TUTORIAL: ------------------------------------------------------------------------
            self.root.after(1000, self._audio_health_tick)
        except Exception:
            pass


App._audio_health_tick = _audio_health_tick


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function restart_audio_stream: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def restart_audio_stream(self) -> None:
    engine = getattr(self, "audio_engine", None)
    if engine is None:
        if hasattr(self, "status"):
            self.status.config(text="AUDIO STREAM RESTART unavailable: engine not attached yet")
        return
    engine.hard_restart("manual restart", force_fallback_state=True)
    if hasattr(self, "status"):
        span, rms, error = engine.last_audio_stats()
        suffix = f"  last span={span:.2e} rms={rms:.2e}"
        if error:
            suffix += f"  error={error}"
        self.status.config(text="AUDIO STREAM RESTART  manual restart" + suffix)


App.restart_audio_stream = restart_audio_stream


_previous_panic_audio_for_stream_recovery = App.panic_audio


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Audio helper: _panic_audio_with_stream_recovery bridges UI state and the audio
# TUTORIAL: engine. The main rule is to keep blocking work out of the real-time callback.
# TUTORIAL: ------------------------------------------------------------------------
def _panic_audio_with_stream_recovery(self) -> None:
    _previous_panic_audio_for_stream_recovery(self)
    engine = getattr(self, "audio_engine", None)
    if engine is not None:
        engine.hard_restart("panic audio", force_fallback_state=True)
    if hasattr(self, "status"):
        self.status.config(text="PANIC AUDIO  fallback square + hard audio stream restart")


App.panic_audio = _panic_audio_with_stream_recovery


_previous_reset_all_for_stream_recovery = App.reset_all


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _reset_all_with_stream_recovery: small named steps make the signal
# TUTORIAL: path, UI path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _reset_all_with_stream_recovery(self) -> None:
    _previous_reset_all_for_stream_recovery(self)
    engine = getattr(self, "audio_engine", None)
    if engine is not None:
        engine.hard_restart("reset all", force_fallback_state=False)
    if hasattr(self, "status"):
        self.status.config(text="RESET ALL  defaults pushed + hard audio stream restart")


App.reset_all = _reset_all_with_stream_recovery


_previous_poll_messages_for_stream_recovery = App._poll_messages


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _poll_messages_with_stream_recovery: small named steps make the
# TUTORIAL: signal path, UI path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _poll_messages_with_stream_recovery(self) -> None:
    _previous_poll_messages_for_stream_recovery(self)
    # Keep this non-invasive: only display stream errors/restarts that were
    # explicitly posted through audio_watchdog_message by the engine/callback.


App._poll_messages = _poll_messages_with_stream_recovery



# -----------------------------------------------------------------------------
# 12-bit pot resolution + window-aware safety square override
#
# The original Arduino-style controls were 8-bit-ish. This layer makes the
# three pots 12-bit controls while preserving the old response range by mapping
# 0..4095 to approximately the same 0..2 seed range used by the recurrence.
# Old patches translate approximately as old_value × 16.
#
# Pot 1 is now allowed to reach 0. With the existing safety path, an all-zero or
# otherwise constant raw table becomes a generated safety square rather than a
# broken/poisoned table.
#
# The safety square is now passed through the same before/after generation windows
# as a normal table. This makes start/end controls behave consistently when the
# fractal recurrence collapses and the fallback oscillator is used.
# -----------------------------------------------------------------------------

POT_MAX_12BIT = 4095
POT_SEED_SCALE_12BIT = 2048.0
DEFAULT_POT1 = 2048
DEFAULT_POT2 = 4080
DEFAULT_POT3 = 160


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function pot_to_seed_12bit: small named steps make the signal path, UI path,
# TUTORIAL: and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def pot_to_seed_12bit(value: int | float) -> float:
    return clamp_float(value, 0.0, float(POT_MAX_12BIT)) / POT_SEED_SCALE_12BIT


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function windowed_safety_square_table: small named steps make the signal
# TUTORIAL: path, UI path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def windowed_safety_square_table(
    before_start: float = 0.0,
    before_length: float = WINDOW_STEPS,
    after_start: float = 0.0,
    after_length: float = WINDOW_STEPS,
) -> np.ndarray:
    """Return the safety square after the same generation windows as a real table."""
    raw = fallback_table().astype(WORK_DTYPE)
    if not is_full_window(before_start, before_length):
        raw = window_table_linear(raw, before_start, before_length)
    if not is_full_window(after_start, after_length):
        raw = window_table_linear(raw, after_start, after_length)

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
    # TUTORIAL: samples, and table generation can sometimes hit extreme math.
    # TUTORIAL: ------------------------------------------------------------------------
    y = np.nan_to_num(raw.astype(WORK_DTYPE), nan=0.0, posinf=0.0, neginf=0.0)
    y -= float(np.mean(y))
    peak = float(np.max(np.abs(y)))
    if (not math.isfinite(peak)) or peak <= EPSILON:
        return fallback_table()
    y /= peak
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
    # TUTORIAL: samples, and table generation can sometimes hit extreme math.
    # TUTORIAL: ------------------------------------------------------------------------
    return np.clip(np.nan_to_num(y, nan=0.0, posinf=1.0, neginf=-1.0), -1.0, 1.0).astype(np.float32)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function finalise_raw_table_checked_windowed: small named steps make the
# TUTORIAL: signal path, UI path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def finalise_raw_table_checked_windowed(
    raw: np.ndarray,
    *,
    before_start: float = 0.0,
    before_length: float = WINDOW_STEPS,
    after_start: float = 0.0,
    after_length: float = WINDOW_STEPS,
) -> np.ndarray:
    """Finalise a raw table, using a windowed safety square if the raw table fails."""
    global LAST_WAVETABLE_NOTES

    notes: list[str] = []
    raw_arr = np.asarray(raw, dtype=WORK_DTYPE)
    if raw_arr.shape != (TABLE_SIZE,):
        notes.append(f"raw table shape {raw_arr.shape}→safety square source")
        LAST_WAVETABLE_NOTES += notes
        return windowed_safety_square_table(before_start, before_length, after_start, after_length)

    if not np.all(np.isfinite(raw_arr)):
        notes.append("non-finite table values sanitised")
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
    # TUTORIAL: samples, and table generation can sometimes hit extreme math.
    # TUTORIAL: ------------------------------------------------------------------------
    y = np.nan_to_num(raw_arr, nan=0.0, posinf=0.0, neginf=0.0)

    y -= float(np.mean(y))
    peak = float(np.max(np.abs(y)))
    if (not math.isfinite(peak)) or peak <= EPSILON:
        # Deliberately avoid the words used by the earlier auto-window-widening
        # heuristic. A safety square is a valid generated source here, and should
        # continue to obey the user's start/end windows.
        notes.append("flat raw table→windowed safety square source")
        LAST_WAVETABLE_NOTES += notes
        return windowed_safety_square_table(before_start, before_length, after_start, after_length)

    y /= peak
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
    # TUTORIAL: samples, and table generation can sometimes hit extreme math.
    # TUTORIAL: ------------------------------------------------------------------------
    table = np.nan_to_num(y, nan=0.0, posinf=1.0, neginf=-1.0).astype(np.float32)
    if not table_is_usable(table):
        notes.append("unsafe final table→windowed safety square source")
        LAST_WAVETABLE_NOTES += notes
        return windowed_safety_square_table(before_start, before_length, after_start, after_length)

    LAST_WAVETABLE_NOTES += notes
    return table


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Validates the core patch controls and also reports safety notes. The important
# TUTORIAL: detail is that risky interactions are clipped internally while leaving the visible
# TUTORIAL: slider positions alone.
# TUTORIAL: That distinction matters for learning: the user can see which values they requested,
# TUTORIAL: while the engine can still protect itself from numeric blow-ups.
# TUTORIAL: ------------------------------------------------------------------------
def clamp_control_values(
    pot1: int,
    pot2: int,
    pot3: int,
    wt_type: int,
    volume: float,
) -> tuple[int, int, int, int, float, list[str]]:  # type: ignore[override]
    """Return 12-bit range-checked controls plus safety notes."""
    requested = (int(pot1), int(pot2), int(pot3), int(wt_type), float(volume))

    p1 = clamp_int(requested[0], 0, POT_MAX_12BIT)
    p2 = clamp_int(requested[1], 0, POT_MAX_12BIT)
    p3 = clamp_int(requested[2], 0, POT_MAX_12BIT)
    typ = clamp_int(requested[3], 0, WAVETABLE_TYPE_MAX)
    vol = clamp_float(requested[4], 0.0, 1.0)

    notes: list[str] = []
    if p1 != requested[0]:
        notes.append(f"pot1 {requested[0]}→{p1}")
    if p2 != requested[1]:
        notes.append(f"pot2 {requested[1]}→{p2}")
    if p3 != requested[2]:
        notes.append(f"pot3 {requested[2]}→{p3}")
    if typ != requested[3]:
        notes.append(f"type {requested[3]}→{typ}")
    if vol != requested[4]:
        notes.append(f"volume {requested[4]:.2f}→{vol:.2f}")

    iter2_multiplier = pot_to_seed_12bit(p1) * pot_to_seed_12bit(p2)
    if typ in WT_TYPES_USING_ITER2 and iter2_multiplier > SAFE_ITER2_MULTIPLIER:
        notes.append(
            f"iter2 multiplier clipped {iter2_multiplier:.3f}→{SAFE_ITER2_MULTIPLIER:.3f}"
        )

    return p1, p2, p3, typ, vol, notes


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: The core generator. It builds the iter2 and iter3 recurrence arrays, then combines
# TUTORIAL: or selects them according to wavetable type.
# TUTORIAL: Later in the file this function name is redefined to add generation-stage windows
# TUTORIAL: and 12-bit pot behaviour. That layered style is why this script works like a visible
# TUTORIAL: development history.
# TUTORIAL: ------------------------------------------------------------------------
def generate_wavetable(
    pot1: int,
    pot2: int,
    pot3: int,
    wt_type: int,
    before_start: float = 0.0,
    before_length: float = WINDOW_STEPS,
    after_start: float = 0.0,
    after_length: float = WINDOW_STEPS,
) -> np.ndarray:  # type: ignore[override]
    """Generate one 256-sample bipolar wavetable from 12-bit pots."""
    global LAST_WAVETABLE_NOTES
    LAST_WAVETABLE_NOTES = []

    pot1 = clamp_int(pot1, 0, POT_MAX_12BIT)
    pot2 = clamp_int(pot2, 0, POT_MAX_12BIT)
    pot3 = clamp_int(pot3, 0, POT_MAX_12BIT)
    wt_type = clamp_int(wt_type, 0, WAVETABLE_TYPE_MAX)

    max_val = 0.0
    max_val2 = 0.0
    temp = np.zeros(TABLE_SIZE, dtype=WORK_DTYPE)
    iter2 = np.zeros(TABLE_SIZE, dtype=WORK_DTYPE)
    iter3 = np.zeros(TABLE_SIZE, dtype=WORK_DTYPE)

    # 12-bit pots mapped to the same nominal 0..2 seed range as the old code.
    seed0 = pot_to_seed_12bit(pot1)
    seed1 = pot_to_seed_12bit(pot2)
    seed2 = pot_to_seed_12bit(pot3)

    iter2_multiplier = seed1 * seed0
    if wt_type in WT_TYPES_USING_ITER2 and iter2_multiplier > SAFE_ITER2_MULTIPLIER:
        iter2_multiplier = SAFE_ITER2_MULTIPLIER

    temp[0] = seed0 * seed0
    temp[1] = iter2_multiplier
    temp[2] = seed2 * seed0

    iter2[0:3] = temp[0:3]
    for i in range(3, TABLE_SIZE):
        iter2[i] = iter2[i - 3] * iter2[1]

    iter3[0:9] = iter2[0:9]
    for i in range(9, TABLE_SIZE):
        iter3[i] = iter3[i - 9] * iter3[2]

    if not is_full_window(before_start, before_length):
        iter2 = window_table_linear(iter2, before_start, before_length)
        iter3 = window_table_linear(iter3, before_start, before_length)

    if wt_type == 0:
        raw = iter2

    elif wt_type == 1:
        raw = iter3

    elif wt_type in (2, 3):
        skip = 0
        for j in range(TABLE_SIZE):
            if wt_type == 2:
                if j < 128:
                    temp[j] = iter3[skip]
                    max_val = max(max_val, float(temp[j]))
                else:
                    temp[j] = iter2[skip - 256]
                    max_val2 = max(max_val2, float(temp[j]))
            else:
                if j < 128:
                    temp[j] = iter2[skip]
                    max_val = max(max_val, float(temp[j]))
                else:
                    temp[j] = iter3[skip - 256]
                    max_val2 = max(max_val2, float(temp[j]))
            skip += 2

        out = np.zeros(TABLE_SIZE, dtype=WORK_DTYPE)
        if math.isfinite(max_val) and max_val > EPSILON:
            out[:128] = (temp[:128] / max_val) * 256.0
        if math.isfinite(max_val2) and max_val2 > EPSILON:
            out[128:] = (temp[128:] / max_val2) * 256.0
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
        # TUTORIAL: samples, and table generation can sometimes hit extreme math.
        # TUTORIAL: ------------------------------------------------------------------------
        raw = (np.nan_to_num(out, nan=0.0, posinf=256.0, neginf=0.0) / 128.0) - 1.0

    else:
        a = normalise_bipolar(iter2)
        b = normalise_bipolar(iter3)

        if wt_type == 4:
            raw = a + b
        elif wt_type == 5:
            raw = a - b
        elif wt_type == 6:
            raw = a * b
        elif wt_type == 7:
            raw = a / np.maximum(np.abs(b), 0.05)
        elif wt_type == 8:
            raw = u8_to_bipolar(bipolar_to_u8(a) | bipolar_to_u8(b))
        elif wt_type == 9:
            raw = u8_to_bipolar(bipolar_to_u8(a) ^ bipolar_to_u8(b))
        else:
            raw = iter2

    if not is_full_window(after_start, after_length):
        raw = window_table_linear(raw, after_start, after_length)

    return finalise_raw_table_checked_windowed(
        raw,
        before_start=before_start,
        before_length=before_length,
        after_start=after_start,
        after_length=after_length,
    )


_previous_app_init_for_12bit_pots = App.__init__


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _app_init_for_12bit_pots: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _app_init_for_12bit_pots(self, *args, **kwargs):
    _previous_app_init_for_12bit_pots(self, *args, **kwargs)

    # Reconfigure the already-built pot sliders in case an earlier constructor
    # layer made them with 8-bit bounds.
    try:
        var_names = {key: str(var) for key, var in self.vars.items()}
        for widget in self.control_parent.winfo_children():
            if not isinstance(widget, tk.Frame):
                continue
            for child in widget.winfo_children():
                if isinstance(child, tk.Scale):
                    var_name = str(child.cget("variable"))
                    if var_name in {var_names["pot1"], var_names["pot2"], var_names["pot3"]}:
                        child.config(from_=0, to=POT_MAX_12BIT, resolution=1)
    except Exception:
        pass

    # Move the default controls to their 12-bit equivalents. This preserves the
    # old default sound rather than making the old 8-bit numbers tiny values.
    try:
        self.vars["pot1"].set(DEFAULT_POT1)
        self.vars["pot2"].set(DEFAULT_POT2)
        self.vars["pot3"].set(DEFAULT_POT3)
        self.update_table()
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
        # TUTORIAL: callback may touch it at the same time.
        # TUTORIAL: ------------------------------------------------------------------------
        with self.state.lock:
            self.state.reset_requested = True
    except Exception:
        pass


App.__init__ = _app_init_for_12bit_pots


_previous_reset_all_for_12bit_pots = App.reset_all


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _reset_all_for_12bit_pots: small named steps make the signal path,
# TUTORIAL: UI path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _reset_all_for_12bit_pots(self) -> None:
    # Use the earlier reset implementation for frequency/FM/PWM/AM/window/stream,
    # then correct the three pot defaults and regenerate the table.
    _previous_reset_all_for_12bit_pots(self)
    try:
        self.vars["pot1"].set(DEFAULT_POT1)
        self.vars["pot2"].set(DEFAULT_POT2)
        self.vars["pot3"].set(DEFAULT_POT3)
        self.update_table()
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
        # TUTORIAL: callback may touch it at the same time.
        # TUTORIAL: ------------------------------------------------------------------------
        with self.state.lock:
            self.state.reset_requested = True
        if hasattr(self, "status"):
            self.status.config(text="RESET ALL  12-bit defaults pushed + hard audio stream restart")
    except Exception as exc:
        if hasattr(self, "status"):
            self.status.config(text=f"RESET ALL 12-bit correction failed: {type(exc).__name__}: {exc}")


App.reset_all = _reset_all_for_12bit_pots


_previous_panic_audio_for_windowed_square = App.panic_audio


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Audio helper: _panic_audio_with_windowed_square bridges UI state and the audio
# TUTORIAL: engine. The main rule is to keep blocking work out of the real-time callback.
# TUTORIAL: ------------------------------------------------------------------------
def _panic_audio_with_windowed_square(self) -> None:
    # Panic remains a known-good recovery, but the fallback square is drawn through
    # the current generation windows so the visual/resulting start-stop behaviour
    # stays consistent when the user is studying the safety oscillator.
    before_start, before_length, _before_notes = clamp_window_pair(
        float(self.vars["before_start"].get()), float(self.vars["before_end"].get()), "before"
    )
    after_start, after_length, _after_notes = clamp_window_pair(
        float(self.vars["after_start"].get()), float(self.vars["after_end"].get()), "after"
    )
    table = windowed_safety_square_table(before_start, before_length, after_start, after_length)

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
    # TUTORIAL: callback may touch it at the same time.
    # TUTORIAL: ------------------------------------------------------------------------
    with self.state.lock:
        self.state.target_table = table
        self.state.target_volume = 0.20
        self.state.target_frequency = self.default_frequency
        self.state.target_fm = 0.0
        self.state.target_pwm = 0.0
        self.state.target_am = 0.0
        self.state.target_before_start = 0.0
        self.state.target_before_length = WINDOW_STEPS
        self.state.target_after_start = 0.0
        self.state.target_after_length = WINDOW_STEPS
        self.state.target_fm_range_cents = DEFAULT_FM_RANGE_CENTS
        self.state.reset_requested = True

    if hasattr(self, "fm_range_var"):
        self.fm_range_var.set(DEFAULT_FM_RANGE_LABEL)
        self._push_fm_range(update_status=False)
    self._draw_waveform(table)

    engine = getattr(self, "audio_engine", None)
    if engine is not None:
        engine.hard_restart("panic audio", force_fallback_state=False)
    if hasattr(self, "status"):
        self.status.config(text="PANIC AUDIO  windowed safety square + hard audio stream restart")


App.panic_audio = _panic_audio_with_windowed_square



# -----------------------------------------------------------------------------
# Period-based generator/audio stall watchdog override
#
# The earlier watchdog used a broad multi-cycle/timeout rule. This layer follows
# the musical criterion directly: if one complete nominal period passes while
# the rendered output value is stuck at the same value, intervene. The threshold
# is sample-rate / frequency, so higher notes recover faster and lower notes get
# a proportionally longer chance to move.
# -----------------------------------------------------------------------------

# Disable the older broad timeout-style stall trigger. This final watchdog is
# intentionally the authority: one full nominal period with no output change.
STALL_DETECT_CYCLES = 1.0e12
STALL_DETECT_SECONDS = 1.0e12

PERIOD_STALL_OUTPUT_EPS = 1e-8
PERIOD_STALL_MIN_SAMPLES = 8


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function nominal_period_samples_for_watchdog: small named steps make the
# TUTORIAL: signal path, UI path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def nominal_period_samples_for_watchdog(self) -> int:
    """Return the current nominal oscillator period in output samples."""
    try:
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
        # TUTORIAL: callback may touch it at the same time.
        # TUTORIAL: ------------------------------------------------------------------------
        with self.state.lock:
            frequency = clamp_float(
                getattr(self.state, "target_frequency", getattr(self, "current_frequency", DEFAULT_FREQUENCY)),
                MIN_FREQUENCY,
                MAX_FREQUENCY,
            )
    except Exception:
        frequency = clamp_float(getattr(self, "current_frequency", DEFAULT_FREQUENCY), MIN_FREQUENCY, MAX_FREQUENCY)

    sample_rate = clamp_float(getattr(self, "sample_rate", DEFAULT_SAMPLE_RATE), 1.0, 384000.0)
    period_samples = int(math.ceil(sample_rate / max(frequency, MIN_FREQUENCY)))
    return max(PERIOD_STALL_MIN_SAMPLES, period_samples)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function period_flatline_scan: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def period_flatline_scan(self, block: np.ndarray, *, eps: float = PERIOD_STALL_OUTPUT_EPS) -> tuple[bool, int, int]:
    """Scan output for one complete nominal period with no value change.

    Returns (triggered, flat_sample_count, period_samples). The count is
    maintained across audio blocks, because a low note's period is often longer
    than a single callback block.
    """
    period_samples = nominal_period_samples_for_watchdog(self)
    flat_count = int(getattr(self, "period_flatline_samples", 0))
    previous = getattr(self, "period_last_output_sample", None)

    y = np.asarray(block, dtype=np.float64).reshape(-1)
    if y.size == 0 or not np.all(np.isfinite(y)):
        self.period_flatline_samples = period_samples
        self.period_last_output_sample = 0.0
        return True, period_samples, period_samples

    for sample in y:
        sample_f = float(sample)
        if previous is not None and abs(sample_f - float(previous)) <= eps:
            flat_count += 1
        else:
            flat_count = 0
        previous = sample_f
        if flat_count >= period_samples:
            self.period_flatline_samples = 0
            self.period_last_output_sample = previous
            return True, flat_count, period_samples

    self.period_flatline_samples = flat_count
    self.period_last_output_sample = previous
    return False, flat_count, period_samples


_previous_audio_callback_for_period_watchdog = WavetableOscillator.callback


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Audio helper: _period_watchdog_audio_callback bridges UI state and the audio engine.
# TUTORIAL: The main rule is to keep blocking work out of the real-time callback.
# TUTORIAL: ------------------------------------------------------------------------
def _period_watchdog_audio_callback(self, outdata, frames, time, status):
    _previous_audio_callback_for_period_watchdog(self, outdata, frames, time, status)

    try:
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
        # TUTORIAL: callback may touch it at the same time.
        # TUTORIAL: ------------------------------------------------------------------------
        with self.state.lock:
            target_volume = clamp_float(getattr(self.state, "target_volume", 0.0), 0.0, 1.0)

        # Do not rescue intentional silence.
        if target_volume <= STALL_VOLUME_THRESHOLD:
            self.period_flatline_samples = 0
            self.period_last_output_sample = None
            return

        block = np.asarray(outdata[:, 0], dtype=np.float64)
        triggered, flat_samples, period_samples = period_flatline_scan(self, block)
        if not triggered:
            return

        table = getattr(self, "current_table", fallback_table())
        edge_count, _longest_run, table_span = table_motion_stats(table)
        force_fallback = (edge_count == 0) or (table_span <= STALL_TABLE_EDGE_EPS)
        reason = f"one nominal period flatlined ({flat_samples}/{period_samples} samples unchanged)"
        force_audio_push_start(self, reason, force_fallback_table=force_fallback)

        # Make the recovery audible in the same callback block when fallback is
        # needed. If a phase nudge is enough, the next block will use it.
        if force_fallback:
            render_fallback_push_block(self, outdata, frames)

        self.period_flatline_samples = 0
        try:
            self.period_last_output_sample = float(outdata[-1, 0])
        except Exception:
            self.period_last_output_sample = None
    except Exception as exc:
        # The watchdog itself must not be able to kill audio.
        try:
            # TUTORIAL: ------------------------------------------------------------------------
            # TUTORIAL: SharedState is read or written under a lock because the GUI thread and
            # TUTORIAL: audio callback may touch it at the same time.
            # TUTORIAL: ------------------------------------------------------------------------
            with self.state.lock:
                self.state.audio_watchdog_message = f"PERIOD WATCHDOG ERROR  {type(exc).__name__}: {exc}"
        except Exception:
            pass
        self.period_flatline_samples = 0
        self.period_last_output_sample = None


WavetableOscillator.callback = _period_watchdog_audio_callback



# -----------------------------------------------------------------------------
# Faster heartbeat recovery + clearer generator labels + output resolution/rate
#
# The previous stream health loop checked once per second, which could make a
# dead callback feel like a long silence before the stream was rebuilt. This
# layer records a monotonic timestamp from the audio callback and checks for a
# stale callback heartbeat several times per second.
#
# It also renames the first three generator controls from generic pot labels to
# names that describe the recurrence they affect, and adds user controls for
# audible output quantisation and stream sample rate.
# -----------------------------------------------------------------------------

import time as _fw_time

GENERATOR_LABELS = {
    "pot1": "Core Seed",
    "pot2": "3-Step Growth",
    "pot3": "9-Step Growth",
}

WAVETABLE_DESCRIPTIONS.update({
    0: "Type 0: iter2 only — 3-step recurrence, driven by Core Seed × 3-Step Growth.",
    1: "Type 1: iter3 only — 9-step recurrence, more shaped by Core Seed × 9-Step Growth.",
    2: "Type 2: splice — first half iter3, second half iter2, using every second source sample.",
    3: "Type 3: splice — first half iter2, second half iter3, using every second source sample.",
})

OUTPUT_BIT_DEPTH_OPTIONS = (8, 12, 16, 24)
DEFAULT_OUTPUT_BIT_DEPTH = 24

SAMPLE_RATE_OPTIONS = (
    ("44.1 kHz", 44_100),
    ("48 kHz", 48_000),
    ("96 kHz", 96_000),
    ("192 kHz", 192_000),
)
SAMPLE_RATE_BY_LABEL = {label: rate for label, rate in SAMPLE_RATE_OPTIONS}
SAMPLE_RATE_LABEL_BY_VALUE = {rate: label for label, rate in SAMPLE_RATE_OPTIONS}
FAST_HEALTH_INTERVAL_MS = 25
FAST_CALLBACK_STALE_SECONDS = 0.08
FAST_STARTUP_GRACE_SECONDS = 0.30


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function closest_supported_sample_rate: small named steps make the signal
# TUTORIAL: path, UI path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def closest_supported_sample_rate(rate: int | float) -> int:
    requested = int(round(float(rate)))
    return min((value for _label, value in SAMPLE_RATE_OPTIONS), key=lambda value: abs(value - requested))


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function sample_rate_label: small named steps make the signal path, UI path,
# TUTORIAL: and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def sample_rate_label(rate: int | float) -> str:
    selected = closest_supported_sample_rate(rate)
    return SAMPLE_RATE_LABEL_BY_VALUE.get(selected, f"{selected:g} Hz")


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function output_bit_depth_from_value: small named steps make the signal path,
# TUTORIAL: UI path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def output_bit_depth_from_value(value: int | float | str) -> int:
    try:
        requested = int(float(value))
    except (TypeError, ValueError):
        requested = DEFAULT_OUTPUT_BIT_DEPTH
    if requested in OUTPUT_BIT_DEPTH_OPTIONS:
        return requested
    return min(OUTPUT_BIT_DEPTH_OPTIONS, key=lambda bits: abs(bits - requested))


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function quantize_bipolar_signal: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def quantize_bipolar_signal(signal: np.ndarray, bit_depth: int) -> np.ndarray:
    """Quantise a bipolar -1..+1 float signal to an audible bit depth."""
    bits = output_bit_depth_from_value(bit_depth)
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
    # TUTORIAL: samples, and table generation can sometimes hit extreme math.
    # TUTORIAL: ------------------------------------------------------------------------
    y = np.nan_to_num(np.asarray(signal, dtype=np.float64), nan=0.0, posinf=1.0, neginf=-1.0)
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Clip the signal/control range after math that might overshoot. This keeps audio
    # TUTORIAL: buffers and tables bounded.
    # TUTORIAL: ------------------------------------------------------------------------
    y = np.clip(y, -1.0, 1.0)

    # Treat the selected bit depth as the number of discrete full-scale levels
    # across the bipolar waveform. The PortAudio stream remains float32, but the
    # sound and display follow these quantised steps.
    levels = float((1 << bits) - 1)
    y = np.round((y + 1.0) * 0.5 * levels) / levels
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Clip the signal/control range after math that might overshoot. This keeps audio
    # TUTORIAL: buffers and tables bounded.
    # TUTORIAL: ------------------------------------------------------------------------
    return np.clip((y * 2.0) - 1.0, -1.0, 1.0).astype(np.float32)


_previous_clamp_control_values_for_labels = clamp_control_values


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Validates the core patch controls and also reports safety notes. The important
# TUTORIAL: detail is that risky interactions are clipped internally while leaving the visible
# TUTORIAL: slider positions alone.
# TUTORIAL: That distinction matters for learning: the user can see which values they requested,
# TUTORIAL: while the engine can still protect itself from numeric blow-ups.
# TUTORIAL: ------------------------------------------------------------------------
def clamp_control_values(
    pot1: int,
    pot2: int,
    pot3: int,
    wt_type: int,
    volume: float,
) -> tuple[int, int, int, int, float, list[str]]:  # type: ignore[override]
    p1, p2, p3, typ, vol, notes = _previous_clamp_control_values_for_labels(
        pot1, pot2, pot3, wt_type, volume
    )
    renamed_notes = []
    for note in notes:
        renamed = (
            note.replace("pot1", GENERATOR_LABELS["pot1"])
            .replace("pot2", GENERATOR_LABELS["pot2"])
            .replace("pot3", GENERATOR_LABELS["pot3"])
            .replace("iter2 multiplier", "3-step multiplier")
        )
        renamed_notes.append(renamed)
    return p1, p2, p3, typ, vol, renamed_notes


_previous_sanitise_for_resolution_controls = sanitise_shared_state


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function sanitise_shared_state: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def sanitise_shared_state(state: SharedState) -> None:  # type: ignore[override]
    _previous_sanitise_for_resolution_controls(state)
    current_depth = getattr(state, "target_output_bit_depth", DEFAULT_OUTPUT_BIT_DEPTH)
    state.target_output_bit_depth = output_bit_depth_from_value(current_depth)


_previous_audio_callback_for_resolution_controls = WavetableOscillator.callback


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Audio helper: _resolution_audio_callback bridges UI state and the audio engine. The
# TUTORIAL: main rule is to keep blocking work out of the real-time callback.
# TUTORIAL: ------------------------------------------------------------------------
def _resolution_audio_callback(self, outdata, frames, time, status):
    """Final audio wrapper: output bit-depth quantisation + heartbeat timestamp."""
    try:
        _previous_audio_callback_for_resolution_controls(self, outdata, frames, time, status)
        try:
            # TUTORIAL: ------------------------------------------------------------------------
            # TUTORIAL: SharedState is read or written under a lock because the GUI thread and
            # TUTORIAL: audio callback may touch it at the same time.
            # TUTORIAL: ------------------------------------------------------------------------
            with self.state.lock:
                bit_depth = output_bit_depth_from_value(
                    getattr(self.state, "target_output_bit_depth", DEFAULT_OUTPUT_BIT_DEPTH)
                )
        except Exception:
            bit_depth = DEFAULT_OUTPUT_BIT_DEPTH

        if bit_depth < 24:
            quantized = quantize_bipolar_signal(outdata[:, 0], bit_depth)
            outdata[:, 0] = quantized
            if outdata.shape[1] > 1:
                outdata[:, 1] = quantized
    finally:
        # The health checker uses this instead of waiting for a one-second
        # unchanged callback counter, reducing perceived restart latency.
        self.last_callback_monotonic = _fw_time.monotonic()


WavetableOscillator.callback = _resolution_audio_callback


_previous_audio_engine_start_for_fast_health = AudioEngine.start


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Audio helper: _audio_engine_start_fast_health bridges UI state and the audio engine.
# TUTORIAL: The main rule is to keep blocking work out of the real-time callback.
# TUTORIAL: ------------------------------------------------------------------------
def _audio_engine_start_fast_health(self, reason: str = "start") -> None:
    _previous_audio_engine_start_for_fast_health(self, reason)
    self.last_start_monotonic = _fw_time.monotonic()
    if self.osc is not None:
        self.osc.last_callback_monotonic = self.last_start_monotonic


AudioEngine.start = _audio_engine_start_fast_health


_previous_audio_engine_check_health = AudioEngine.check_health


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Audio helper: _audio_engine_check_health_fast bridges UI state and the audio engine.
# TUTORIAL: The main rule is to keep blocking work out of the real-time callback.
# TUTORIAL: ------------------------------------------------------------------------
def _audio_engine_check_health_fast(self) -> None:
    if self.closed:
        return
    if self.stream is None:
        self.hard_restart("missing stream")
        return
    if not self.is_active():
        self.hard_restart("inactive stream")
        return

    now = _fw_time.monotonic()
    start_time = float(getattr(self, "last_start_monotonic", now))
    osc = self.osc
    callback_time = None if osc is None else getattr(osc, "last_callback_monotonic", None)

    if callback_time is None:
        if now - start_time > FAST_STARTUP_GRACE_SECONDS:
            self.hard_restart("callback heartbeat missing")
        return

    age = now - float(callback_time)
    if age > FAST_CALLBACK_STALE_SECONDS:
        self.hard_restart(f"callback heartbeat stale {age:.2f}s")
        return

    # Keep the older counter-based state fresh for diagnostics, but do not use
    # a single unchanged counter tick as the primary restart trigger.
    self.last_callback_count = self.callback_count()


AudioEngine.check_health = _audio_engine_check_health_fast


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Audio helper: _audio_engine_set_sample_rate bridges UI state and the audio engine.
# TUTORIAL: The main rule is to keep blocking work out of the real-time callback.
# TUTORIAL: ------------------------------------------------------------------------
def _audio_engine_set_sample_rate(self, samplerate: int) -> bool:
    new_rate = closest_supported_sample_rate(samplerate)
    old_rate = int(getattr(self, "samplerate", DEFAULT_SAMPLE_RATE))
    if new_rate == old_rate and self.is_active():
        self.last_status = f"sample rate already {sample_rate_label(new_rate)}"
        return True

    try:
        sd.check_output_settings(
            device=self.device,
            samplerate=new_rate,
            channels=2,
            dtype="float32",
        )
    except Exception as exc:
        self.last_status = f"sample rate {sample_rate_label(new_rate)} not available: {type(exc).__name__}: {exc}"
        try:
            # TUTORIAL: ------------------------------------------------------------------------
            # TUTORIAL: SharedState is read or written under a lock because the GUI thread and
            # TUTORIAL: audio callback may touch it at the same time.
            # TUTORIAL: ------------------------------------------------------------------------
            with self.state.lock:
                self.state.audio_watchdog_message = self.last_status
        except Exception:
            pass
        return False

    self.samplerate = new_rate
    try:
        self.hard_restart(f"sample rate changed to {sample_rate_label(new_rate)}", force_fallback_state=False)
        return True
    except Exception as exc:
        self.samplerate = old_rate
        self.last_status = (
            f"sample rate change to {sample_rate_label(new_rate)} failed; "
            f"returning to {sample_rate_label(old_rate)}: {type(exc).__name__}: {exc}"
        )
        try:
            self.hard_restart(f"sample rate fallback to {sample_rate_label(old_rate)}", force_fallback_state=False)
        except Exception:
            pass
        try:
            # TUTORIAL: ------------------------------------------------------------------------
            # TUTORIAL: SharedState is read or written under a lock because the GUI thread and
            # TUTORIAL: audio callback may touch it at the same time.
            # TUTORIAL: ------------------------------------------------------------------------
            with self.state.lock:
                self.state.audio_watchdog_message = self.last_status
        except Exception:
            pass
        return False


AudioEngine.set_sample_rate = _audio_engine_set_sample_rate


_previous_status_text_for_generator_labels = App._status_text


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _status_text_with_generator_labels: small named steps make the
# TUTORIAL: signal path, UI path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _status_text_with_generator_labels(
    self,
    prefix: str,
    requested: tuple[int, int, int, int, float],
    effective: tuple[int, int, int, int, float],
    notes: list[str],
) -> str:
    req_p1, req_p2, req_p3, req_type, req_volume = requested
    eff_p1, eff_p2, eff_p3, eff_type, eff_volume = effective

    if notes:
        return (
            f"{prefix}  REQUEST core={req_p1}  3-step={req_p2}  9-step={req_p3} "
            f"type={req_type} volume={req_volume:.2f}  |  "
            f"USED core={eff_p1}  3-step={eff_p2}  9-step={eff_p3} "
            f"type={eff_type} volume={eff_volume:.2f}"
        )

    return (
        f"{prefix}  core={eff_p1}  3-step={eff_p2}  9-step={eff_p3}  "
        f"type={eff_type}  volume={eff_volume:.2f}"
    )


App._status_text = _status_text_with_generator_labels


_previous_draw_waveform_for_bit_depth = App._draw_waveform


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Export helper: _draw_waveform_with_output_bit_depth turns in-memory rendered
# TUTORIAL: audio/settings into files on disk. This is separate from live playback by design.
# TUTORIAL: ------------------------------------------------------------------------
def _draw_waveform_with_output_bit_depth(self, table: np.ndarray) -> None:
    try:
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
        # TUTORIAL: callback may touch it at the same time.
        # TUTORIAL: ------------------------------------------------------------------------
        with self.state.lock:
            bit_depth = output_bit_depth_from_value(
                getattr(self.state, "target_output_bit_depth", DEFAULT_OUTPUT_BIT_DEPTH)
            )
    except Exception:
        bit_depth = DEFAULT_OUTPUT_BIT_DEPTH

    # The actual audio quantisation is in the callback. This display-side
    # quantisation makes the base table view reflect the selected output steps.
    if bit_depth < 24:
        table = quantize_bipolar_signal(np.asarray(table, dtype=np.float32), bit_depth)
    _previous_draw_waveform_for_bit_depth(self, table)


App._draw_waveform = _draw_waveform_with_output_bit_depth


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _relabel_generator_controls: small named steps make the signal path,
# TUTORIAL: UI path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _relabel_generator_controls(self) -> None:
    replacements = {
        "Pot 1": GENERATOR_LABELS["pot1"],
        "Pot 2": GENERATOR_LABELS["pot2"],
        "Pot 3": GENERATOR_LABELS["pot3"],
    }
    try:
        for frame in self.control_parent.winfo_children():
            if not isinstance(frame, tk.Frame):
                continue
            for child in frame.winfo_children():
                if isinstance(child, tk.Label):
                    text = str(child.cget("text"))
                    if text in replacements:
                        child.config(text=replacements[text])
    except Exception:
        pass
    self._update_type_description()


App._relabel_generator_controls = _relabel_generator_controls


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Audio helper: _add_audio_resolution_controls bridges UI state and the audio engine.
# TUTORIAL: The main rule is to keep blocking work out of the real-time callback.
# TUTORIAL: ------------------------------------------------------------------------
def _add_audio_resolution_controls(self) -> None:
    frame = tk.Frame(self.control_parent)
    frame.pack(fill="x", padx=12, pady=4)

    tk.Label(frame, text="Output bit depth", width=16, anchor="w").pack(side="left")
    if not hasattr(self, "output_bit_depth_var"):
        self.output_bit_depth_var = tk.StringVar(value=str(DEFAULT_OUTPUT_BIT_DEPTH))
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: OptionMenu is used for compact dropdowns such as wavetable type, pitch range,
    # TUTORIAL: and export settings.
    # TUTORIAL: ------------------------------------------------------------------------
    bit_menu = tk.OptionMenu(
        frame,
        self.output_bit_depth_var,
        *[str(bits) for bits in OUTPUT_BIT_DEPTH_OPTIONS],
        command=lambda _value: self._push_output_bit_depth(),
    )
    bit_menu.config(width=6)
    bit_menu.pack(side="left")
    tk.Label(frame, text="bit").pack(side="left", padx=(4, 18))

    tk.Label(frame, text="Sample rate").pack(side="left")
    initial_rate = closest_supported_sample_rate(getattr(self, "sample_rate", DEFAULT_SAMPLE_RATE))
    if not hasattr(self, "sample_rate_var"):
        self.sample_rate_var = tk.StringVar(value=sample_rate_label(initial_rate))
    else:
        self.sample_rate_var.set(sample_rate_label(initial_rate))
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: OptionMenu is used for compact dropdowns such as wavetable type, pitch range,
    # TUTORIAL: and export settings.
    # TUTORIAL: ------------------------------------------------------------------------
    rate_menu = tk.OptionMenu(
        frame,
        self.sample_rate_var,
        *[label for label, _rate in SAMPLE_RATE_OPTIONS],
        command=lambda _value: self._push_sample_rate(),
    )
    rate_menu.config(width=10)
    rate_menu.pack(side="left", padx=(6, 0))

    self.audio_resolution_label = tk.Label(frame, anchor="w", text="")
    self.audio_resolution_label.pack(side="left", padx=(12, 0), fill="x", expand=True)
    self._push_output_bit_depth(update_status=False)


App._add_audio_resolution_controls = _add_audio_resolution_controls


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _push_output_bit_depth: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _push_output_bit_depth(self, update_status: bool = True) -> None:
    bits = output_bit_depth_from_value(
        self.output_bit_depth_var.get() if hasattr(self, "output_bit_depth_var") else DEFAULT_OUTPUT_BIT_DEPTH
    )
    if hasattr(self, "output_bit_depth_var"):
        self.output_bit_depth_var.set(str(bits))
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
    # TUTORIAL: callback may touch it at the same time.
    # TUTORIAL: ------------------------------------------------------------------------
    with self.state.lock:
        self.state.target_output_bit_depth = bits
    if hasattr(self, "audio_resolution_label"):
        self.audio_resolution_label.config(text=f"audio output quantised to {bits}-bit steps")
    if hasattr(self, "wave_canvas"):
        self._draw_current_waveform()
    if update_status and hasattr(self, "status"):
        self.status.config(text=f"OUTPUT BIT DEPTH  {bits}-bit")


App._push_output_bit_depth = _push_output_bit_depth


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _push_sample_rate: small named steps make the signal path, UI path,
# TUTORIAL: and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _push_sample_rate(self, update_status: bool = True) -> None:
    label = self.sample_rate_var.get() if hasattr(self, "sample_rate_var") else sample_rate_label(DEFAULT_SAMPLE_RATE)
    requested_rate = SAMPLE_RATE_BY_LABEL.get(label, closest_supported_sample_rate(DEFAULT_SAMPLE_RATE))
    requested_rate = closest_supported_sample_rate(requested_rate)
    self.sample_rate = float(requested_rate)

    engine = getattr(self, "audio_engine", None)
    ok = True
    if engine is not None:
        ok = bool(engine.set_sample_rate(requested_rate))
        active_rate = closest_supported_sample_rate(getattr(engine, "samplerate", requested_rate))
        if hasattr(self, "sample_rate_var"):
            self.sample_rate_var.set(sample_rate_label(active_rate))
        self.sample_rate = float(active_rate)
    elif hasattr(self, "sample_rate_var"):
        self.sample_rate_var.set(sample_rate_label(requested_rate))

    if hasattr(self, "audio_resolution_label"):
        bit_depth = output_bit_depth_from_value(
            self.output_bit_depth_var.get() if hasattr(self, "output_bit_depth_var") else DEFAULT_OUTPUT_BIT_DEPTH
        )
        self.audio_resolution_label.config(
            text=f"{bit_depth}-bit output steps @ {sample_rate_label(self.sample_rate)}"
        )

    if update_status and hasattr(self, "status"):
        if ok:
            self.status.config(text=f"SAMPLE RATE  {sample_rate_label(self.sample_rate)}")
        else:
            self.status.config(text=f"SAMPLE RATE unavailable; kept {sample_rate_label(self.sample_rate)}")


App._push_sample_rate = _push_sample_rate


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Audio helper: _schedule_fast_audio_health bridges UI state and the audio engine. The
# TUTORIAL: main rule is to keep blocking work out of the real-time callback.
# TUTORIAL: ------------------------------------------------------------------------
def _schedule_fast_audio_health(self) -> None:
    try:
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: Tk uses scheduled callbacks instead of long blocking loops. after(...) lets
        # TUTORIAL: the UI stay responsive.
        # TUTORIAL: ------------------------------------------------------------------------
        self.root.after(FAST_HEALTH_INTERVAL_MS, self._audio_health_tick)
    except Exception:
        pass


App._schedule_fast_audio_health = _schedule_fast_audio_health


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Audio helper: _audio_health_tick_fast bridges UI state and the audio engine. The
# TUTORIAL: main rule is to keep blocking work out of the real-time callback.
# TUTORIAL: ------------------------------------------------------------------------
def _audio_health_tick_fast(self) -> None:
    now = _fw_time.monotonic()
    last_check = float(getattr(self, "_last_audio_health_check_monotonic", 0.0))
    if now - last_check >= (FAST_HEALTH_INTERVAL_MS / 1000.0) * 0.75:
        self._last_audio_health_check_monotonic = now
        try:
            engine = getattr(self, "audio_engine", None)
            if engine is not None:
                engine.check_health()
        except Exception as exc:
            if hasattr(self, "status"):
                self.status.config(text=f"AUDIO HEALTH CHECK ERROR  {type(exc).__name__}: {exc}")
    self._schedule_fast_audio_health()


App._audio_health_tick = _audio_health_tick_fast


_previous_app_init_for_resolution_controls = App.__init__


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _app_init_with_resolution_controls: small named steps make the
# TUTORIAL: signal path, UI path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _app_init_with_resolution_controls(self, *args, **kwargs):
    _previous_app_init_for_resolution_controls(self, *args, **kwargs)
    self._relabel_generator_controls()
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
    # TUTORIAL: callback may touch it at the same time.
    # TUTORIAL: ------------------------------------------------------------------------
    with self.state.lock:
        if not hasattr(self.state, "target_output_bit_depth"):
            self.state.target_output_bit_depth = DEFAULT_OUTPUT_BIT_DEPTH
    self._add_audio_resolution_controls()
    self._schedule_fast_audio_health()


App.__init__ = _app_init_with_resolution_controls


# -----------------------------------------------------------------------------
# Transport controls + timed Play gate
#
# The audio stream should stay alive whether the synth is sounding or not. Stop
# therefore gates the rendered output to silence instead of stopping PortAudio.
# Drone opens the gate continuously. Play opens the gate for a callback-counted
# number of samples, so very short durations such as one waveform cycle are not
# limited by Tk's millisecond timer granularity.
# -----------------------------------------------------------------------------

TRANSPORT_DEFAULT_GATE = 1.0
PLAY_TIMER_MAX_SECONDS = 60.0
PLAY_TIMER_SLIDER_RESOLUTION = -1  # Tk: no rounding; allows sub-ms cycle times.


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _transport_frequency_hz_from_state: small named steps make the
# TUTORIAL: signal path, UI path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _transport_frequency_hz_from_state(state: SharedState, fallback: float = DEFAULT_FREQUENCY) -> float:
    try:
        return clamp_float(getattr(state, "target_frequency", fallback), MIN_FREQUENCY, MAX_FREQUENCY)
    except Exception:
        return clamp_float(fallback, MIN_FREQUENCY, MAX_FREQUENCY)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _timer_min_seconds_for_frequency: small named steps make the signal
# TUTORIAL: path, UI path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _timer_min_seconds_for_frequency(frequency: float) -> float:
    return 1.0 / max(clamp_float(frequency, MIN_FREQUENCY, MAX_FREQUENCY), MIN_FREQUENCY)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _format_timer_seconds: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _format_timer_seconds(seconds: float) -> str:
    seconds = clamp_float(seconds, 0.0, PLAY_TIMER_MAX_SECONDS)
    if seconds < 0.001:
        return f"{seconds:.6f} seconds"
    if seconds < 1.0:
        return f"{seconds:.3f} seconds"
    return f"{seconds:.3f} seconds"


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _format_timer_cycles: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _format_timer_cycles(seconds: float, frequency: float) -> str:
    cycles = max(0.0, float(seconds) * clamp_float(frequency, MIN_FREQUENCY, MAX_FREQUENCY))
    if cycles < 10.0:
        return f"{cycles:.2f} cycles"
    if cycles < 1000.0:
        return f"{cycles:.1f} cycles"
    return f"{cycles:.0f} cycles"


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _parse_timer_duration: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _parse_timer_duration(text: str, frequency: float) -> float:
    """Parse values like '128 cycles', '0.100 seconds', '60s'."""
    raw = str(text).strip().lower().replace(",", "")
    raw = raw.replace("seconds", "sec").replace("second", "sec")
    raw = raw.replace("cycles", "cycle")

    if raw in {"cycle", "one cycle", "single cycle", "1 cycle"}:
        return _timer_min_seconds_for_frequency(frequency)

    parts = raw.split()
    if not parts:
        return _timer_min_seconds_for_frequency(frequency)

    # Compact suffix forms: 0.1s, 128cyc, 128cycles.
    first = parts[0]
    unit = parts[1] if len(parts) > 1 else "sec"
    for suffix, replacement_unit in (
        ("cycles", "cycle"),
        ("cycle", "cycle"),
        ("cyc", "cycle"),
        ("secs", "sec"),
        ("sec", "sec"),
        ("s", "sec"),
    ):
        if first.endswith(suffix) and first != suffix:
            first = first[: -len(suffix)]
            unit = replacement_unit
            break

    try:
        value = float(first)
    except ValueError:
        return _timer_min_seconds_for_frequency(frequency)

    if unit.startswith("cycle") or unit == "cyc":
        seconds = value / max(clamp_float(frequency, MIN_FREQUENCY, MAX_FREQUENCY), MIN_FREQUENCY)
    else:
        seconds = value

    return seconds


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _clamp_timer_seconds: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _clamp_timer_seconds(seconds: float, frequency: float) -> float:
    minimum = _timer_min_seconds_for_frequency(frequency)
    return clamp_float(seconds, minimum, PLAY_TIMER_MAX_SECONDS)


_previous_sanitise_for_transport_controls = sanitise_shared_state


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function sanitise_shared_state: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def sanitise_shared_state(state: SharedState) -> None:  # type: ignore[override]
    _previous_sanitise_for_transport_controls(state)
    state.target_audio_gate = clamp_float(getattr(state, "target_audio_gate", TRANSPORT_DEFAULT_GATE), 0.0, 1.0)
    remaining = getattr(state, "target_play_remaining_samples", None)
    if remaining is None:
        state.target_play_remaining_samples = None
    else:
        try:
            state.target_play_remaining_samples = max(0, int(remaining))
        except (TypeError, ValueError):
            state.target_play_remaining_samples = None
    state.target_transport_mode = str(getattr(state, "target_transport_mode", "drone"))


_previous_audio_callback_for_transport_controls = WavetableOscillator.callback


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Audio helper: _transport_audio_callback bridges UI state and the audio engine. The
# TUTORIAL: main rule is to keep blocking work out of the real-time callback.
# TUTORIAL: ------------------------------------------------------------------------
def _transport_audio_callback(self, outdata, frames, time, status):
    """Final audio wrapper: Stop/Drone/Play output gate."""
    _previous_audio_callback_for_transport_controls(self, outdata, frames, time, status)

    try:
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
        # TUTORIAL: callback may touch it at the same time.
        # TUTORIAL: ------------------------------------------------------------------------
        with self.state.lock:
            gate = clamp_float(getattr(self.state, "target_audio_gate", TRANSPORT_DEFAULT_GATE), 0.0, 1.0)
            remaining = getattr(self.state, "target_play_remaining_samples", None)

            if gate <= 0.0:
                outdata.fill(0.0)
                return

            # Drone mode: no sample countdown, just pass audio through.
            if remaining is None:
                return

            remaining = max(0, int(remaining))
            if remaining <= 0:
                self.state.target_audio_gate = 0.0
                self.state.target_play_remaining_samples = 0
                self.state.target_transport_mode = "stopped"
                self.state.audio_watchdog_message = "PLAY TIMER ended"
                outdata.fill(0.0)
                return

            if remaining < frames:
                outdata[remaining:, :] = 0.0
                self.state.target_audio_gate = 0.0
                self.state.target_play_remaining_samples = 0
                self.state.target_transport_mode = "stopped"
                self.state.audio_watchdog_message = "PLAY TIMER ended"
                return

            self.state.target_play_remaining_samples = remaining - int(frames)
    except Exception:
        # Transport gating should never be able to kill the callback.
        pass


WavetableOscillator.callback = _transport_audio_callback


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _find_reset_button_frame: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _find_reset_button_frame(self):
    try:
        for child in self.control_parent.winfo_children():
            if not isinstance(child, tk.Frame):
                continue
            labels = []
            for sub in child.winfo_children():
                if isinstance(sub, tk.Button):
                    labels.append(str(sub.cget("text")))
            if "Reset all" in labels and "Panic audio" in labels:
                return child
    except Exception:
        return None
    return None


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _current_timer_frequency: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _current_timer_frequency(self) -> float:
    try:
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
        # TUTORIAL: callback may touch it at the same time.
        # TUTORIAL: ------------------------------------------------------------------------
        with self.state.lock:
            return _transport_frequency_hz_from_state(self.state, self.default_frequency)
    except Exception:
        return clamp_float(getattr(self, "default_frequency", DEFAULT_FREQUENCY), MIN_FREQUENCY, MAX_FREQUENCY)


App._current_timer_frequency = _current_timer_frequency


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _timer_seconds_from_widgets: small named steps make the signal path,
# TUTORIAL: UI path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _timer_seconds_from_widgets(self) -> float:
    frequency = self._current_timer_frequency()
    try:
        entry_text = self.play_timer_entry_var.get() if hasattr(self, "play_timer_entry_var") else "1 cycle"
        seconds = _parse_timer_duration(entry_text, frequency)
    except Exception:
        seconds = _timer_min_seconds_for_frequency(frequency)
    return _clamp_timer_seconds(seconds, frequency)


App._timer_seconds_from_widgets = _timer_seconds_from_widgets


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _set_timer_widgets_from_seconds: small named steps make the signal
# TUTORIAL: path, UI path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _set_timer_widgets_from_seconds(self, seconds: float, *, update_entry: bool = True) -> float:
    frequency = self._current_timer_frequency()
    seconds = _clamp_timer_seconds(seconds, frequency)
    minimum = _timer_min_seconds_for_frequency(frequency)

    try:
        if hasattr(self, "play_timer_slider"):
            self.play_timer_slider.config(from_=minimum, to=PLAY_TIMER_MAX_SECONDS)
        if hasattr(self, "play_timer_slider_var"):
            self._suppress_timer_slider = True
            try:
                self.play_timer_slider_var.set(seconds)
            finally:
                self._suppress_timer_slider = False
        if update_entry and hasattr(self, "play_timer_entry_var"):
            self.play_timer_entry_var.set(_format_timer_seconds(seconds))
        if hasattr(self, "play_timer_cycle_label"):
            self.play_timer_cycle_label.config(
                text=f"min 1 cycle = {_format_timer_seconds(minimum)}; selected ≈ {_format_timer_cycles(seconds, frequency)}"
            )
    except Exception:
        pass
    return seconds


App._set_timer_widgets_from_seconds = _set_timer_widgets_from_seconds


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _timer_slider_changed: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _timer_slider_changed(self, _value: str | None = None) -> None:
    if getattr(self, "_suppress_timer_slider", False):
        return
    try:
        seconds = float(self.play_timer_slider_var.get())
    except Exception:
        seconds = _timer_min_seconds_for_frequency(self._current_timer_frequency())
    self._set_timer_widgets_from_seconds(seconds, update_entry=True)


App._timer_slider_changed = _timer_slider_changed


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _timer_entry_committed: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _timer_entry_committed(self, _event=None) -> None:
    seconds = self._timer_seconds_from_widgets()
    self._set_timer_widgets_from_seconds(seconds, update_entry=True)
    if hasattr(self, "status"):
        self.status.config(
            text=f"PLAY TIMER  {_format_timer_seconds(seconds)} ≈ {_format_timer_cycles(seconds, self._current_timer_frequency())}"
        )


App._timer_entry_committed = _timer_entry_committed


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _refresh_timer_for_frequency: small named steps make the signal
# TUTORIAL: path, UI path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _refresh_timer_for_frequency(self) -> None:
    try:
        current = float(self.play_timer_slider_var.get()) if hasattr(self, "play_timer_slider_var") else _timer_min_seconds_for_frequency(self._current_timer_frequency())
    except Exception:
        current = _timer_min_seconds_for_frequency(self._current_timer_frequency())
    self._set_timer_widgets_from_seconds(current, update_entry=False)


App._refresh_timer_for_frequency = _refresh_timer_for_frequency


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _set_transport_state: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _set_transport_state(self, mode: str, *, seconds: float | None = None) -> None:
    mode = str(mode)
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
    # TUTORIAL: callback may touch it at the same time.
    # TUTORIAL: ------------------------------------------------------------------------
    with self.state.lock:
        sanitise_shared_state(self.state)
        if mode == "stop":
            self.state.target_audio_gate = 0.0
            self.state.target_play_remaining_samples = 0
            self.state.target_transport_mode = "stopped"
        elif mode == "play":
            frequency = _transport_frequency_hz_from_state(self.state, self.default_frequency)
            duration = _clamp_timer_seconds(
                seconds if seconds is not None else self._timer_seconds_from_widgets(),
                frequency,
            )
            sample_rate = clamp_float(
                getattr(getattr(self, "audio_engine", None), "samplerate", getattr(self, "sample_rate", DEFAULT_SAMPLE_RATE)),
                1.0,
                384000.0,
            )
            self.state.target_audio_gate = 1.0
            self.state.target_play_remaining_samples = max(1, int(math.ceil(duration * sample_rate)))
            self.state.target_transport_mode = "play"
            self.state.reset_requested = True
        else:
            self.state.target_audio_gate = 1.0
            self.state.target_play_remaining_samples = None
            self.state.target_transport_mode = "drone"
            self.state.reset_requested = True


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _transport_status_suffix: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _transport_status_suffix(self) -> str:
    try:
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
        # TUTORIAL: callback may touch it at the same time.
        # TUTORIAL: ------------------------------------------------------------------------
        with self.state.lock:
            mode = str(getattr(self.state, "target_transport_mode", "drone"))
            remaining = getattr(self.state, "target_play_remaining_samples", None)
        if mode == "play" and remaining is not None:
            sample_rate = clamp_float(
                getattr(getattr(self, "audio_engine", None), "samplerate", getattr(self, "sample_rate", DEFAULT_SAMPLE_RATE)),
                1.0,
                384000.0,
            )
            return f"play, {int(remaining)} samples left ({float(remaining) / sample_rate:.3f}s)"
        return mode
    except Exception:
        return "unknown"


App._set_transport_state = _set_transport_state
App._transport_status_suffix = _transport_status_suffix


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function drone_audio: small named steps make the signal path, UI path, and
# TUTORIAL: export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def drone_audio(self) -> None:
    self._set_transport_state("drone")
    if hasattr(self, "status"):
        self.status.config(text="DRONE  continuous audio gate open")


App.drone_audio = drone_audio


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function stop_audio: small named steps make the signal path, UI path, and
# TUTORIAL: export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def stop_audio(self) -> None:
    self._set_transport_state("stop")
    if hasattr(self, "status"):
        self.status.config(text="STOP  audio gated off; stream remains alive")


App.stop_audio = stop_audio


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function play_timed_audio: small named steps make the signal path, UI path,
# TUTORIAL: and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def play_timed_audio(self) -> None:
    seconds = self._timer_seconds_from_widgets()
    seconds = self._set_timer_widgets_from_seconds(seconds, update_entry=True)
    self._set_transport_state("play", seconds=seconds)
    if hasattr(self, "status"):
        self.status.config(
            text=f"PLAY  {_format_timer_seconds(seconds)} ≈ {_format_timer_cycles(seconds, self._current_timer_frequency())}"
        )


App.play_timed_audio = play_timed_audio


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: UI construction helper: _add_transport_timer_controls creates a reusable group of Tk
# TUTORIAL: controls. Keeping these small avoids one enormous constructor.
# TUTORIAL: ------------------------------------------------------------------------
def _add_transport_timer_controls(self) -> None:
    if getattr(self, "_transport_timer_controls_added", False):
        return
    self._transport_timer_controls_added = True

    reset_frame = _find_reset_button_frame(self)

    timer_frame = tk.Frame(self.control_parent)
    if reset_frame is not None:
        timer_frame.pack(fill="x", padx=12, pady=(8, 2), before=reset_frame)
    else:
        timer_frame.pack(fill="x", padx=12, pady=(8, 2))

    tk.Label(timer_frame, text="Play timer", width=16, anchor="w").pack(side="left")
    self.play_timer_slider_var = tk.DoubleVar(value=_timer_min_seconds_for_frequency(self._current_timer_frequency()))
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Tk Scale is the slider widget. In this app, slider callbacks push new values
    # TUTORIAL: into patch or audio state.
    # TUTORIAL: ------------------------------------------------------------------------
    self.play_timer_slider = tk.Scale(
        timer_frame,
        variable=self.play_timer_slider_var,
        from_=_timer_min_seconds_for_frequency(self._current_timer_frequency()),
        to=PLAY_TIMER_MAX_SECONDS,
        resolution=PLAY_TIMER_SLIDER_RESOLUTION,
        showvalue=False,
        orient="horizontal",
        length=320,
        command=self._timer_slider_changed,
    )
    self.play_timer_slider.pack(side="left", fill="x", expand=True)

    self.play_timer_entry_var = tk.StringVar(value="1 cycle")
    entry = tk.Entry(timer_frame, textvariable=self.play_timer_entry_var, width=16, justify="right")
    entry.pack(side="left", padx=(8, 0))
    entry.bind("<Return>", self._timer_entry_committed)
    entry.bind("<FocusOut>", self._timer_entry_committed)

    self.play_timer_cycle_label = tk.Label(timer_frame, anchor="w", text="")
    self.play_timer_cycle_label.pack(side="left", padx=(8, 0), fill="x", expand=True)

    transport_frame = tk.Frame(self.control_parent)
    if reset_frame is not None:
        transport_frame.pack(fill="x", padx=12, pady=(2, 6), before=reset_frame)
    else:
        transport_frame.pack(fill="x", padx=12, pady=(2, 6))

    tk.Label(transport_frame, text="Transport", width=16, anchor="w").pack(side="left")
    tk.Button(transport_frame, text="Drone", command=self.drone_audio).pack(side="left")
    tk.Button(transport_frame, text="Stop", command=self.stop_audio).pack(side="left", padx=(8, 0))
    tk.Button(transport_frame, text="Play (Timer)", command=self.play_timed_audio).pack(side="left", padx=(8, 0))

    self._set_timer_widgets_from_seconds(_timer_min_seconds_for_frequency(self._current_timer_frequency()), update_entry=True)


App._add_transport_timer_controls = _add_transport_timer_controls


_previous_push_frequency_for_timer = App._push_frequency


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _push_frequency_with_timer_update: small named steps make the signal
# TUTORIAL: path, UI path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _push_frequency_with_timer_update(
    self,
    frequency: float,
    *,
    update_ui: bool,
    hard_reset: bool,
    source: str = "frequency",
) -> float:
    result = _previous_push_frequency_for_timer(
        self,
        frequency,
        update_ui=update_ui,
        hard_reset=hard_reset,
        source=source,
    )
    if hasattr(self, "play_timer_slider"):
        self._refresh_timer_for_frequency()
    return result


App._push_frequency = _push_frequency_with_timer_update


_previous_reset_all_for_transport_controls = App.reset_all


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _reset_all_with_transport_controls: small named steps make the
# TUTORIAL: signal path, UI path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _reset_all_with_transport_controls(self) -> None:
    _previous_reset_all_for_transport_controls(self)
    # Keep Reset All behaving like the previous continuous-audio default.
    self._set_transport_state("drone")
    if hasattr(self, "play_timer_slider"):
        self._set_timer_widgets_from_seconds(_timer_min_seconds_for_frequency(self._current_timer_frequency()), update_entry=True)
    if hasattr(self, "status"):
        self.status.config(text="RESET ALL  defaults pushed + transport set to Drone")


App.reset_all = _reset_all_with_transport_controls


_previous_panic_audio_for_transport_controls = App.panic_audio


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Audio helper: _panic_audio_with_transport_controls bridges UI state and the audio
# TUTORIAL: engine. The main rule is to keep blocking work out of the real-time callback.
# TUTORIAL: ------------------------------------------------------------------------
def _panic_audio_with_transport_controls(self) -> None:
    _previous_panic_audio_for_transport_controls(self)
    # Panic Audio should be audible, even if Stop was previously active.
    self._set_transport_state("drone")
    if hasattr(self, "status"):
        self.status.config(text="PANIC AUDIO  safety square + hard audio stream restart + Drone")


App.panic_audio = _panic_audio_with_transport_controls


_previous_app_init_for_transport_controls = App.__init__


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _app_init_with_transport_controls: small named steps make the signal
# TUTORIAL: path, UI path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _app_init_with_transport_controls(self, *args, **kwargs):
    _previous_app_init_for_transport_controls(self, *args, **kwargs)
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
    # TUTORIAL: callback may touch it at the same time.
    # TUTORIAL: ------------------------------------------------------------------------
    with self.state.lock:
        self.state.target_audio_gate = TRANSPORT_DEFAULT_GATE
        self.state.target_play_remaining_samples = None
        self.state.target_transport_mode = "drone"
    self._add_transport_timer_controls()


App.__init__ = _app_init_with_transport_controls


# -----------------------------------------------------------------------------
# Dedicated Play timer cycle-count input override
#
# The earlier timer field accepted mixed text such as "128 cycles", but it did
# not provide a separate numeric cycle box. This layer adds a dedicated Cycles
# entry beside the seconds/duration entry. The slider, duration entry, and cycle
# entry are kept in sync. Play still converts the chosen duration to a callback-
# counted number of samples so very short cycle counts are sample-accurate.
# -----------------------------------------------------------------------------


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _timer_max_cycles_for_frequency: small named steps make the signal
# TUTORIAL: path, UI path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _timer_max_cycles_for_frequency(frequency: float) -> float:
    return PLAY_TIMER_MAX_SECONDS * clamp_float(frequency, MIN_FREQUENCY, MAX_FREQUENCY)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _format_timer_cycle_count: small named steps make the signal path,
# TUTORIAL: UI path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _format_timer_cycle_count(cycles: float) -> str:
    cycles = max(1.0, float(cycles))
    if abs(cycles - round(cycles)) < 1e-6:
        return str(int(round(cycles)))
    if cycles < 10.0:
        return f"{cycles:.3f}".rstrip("0").rstrip(".")
    if cycles < 1000.0:
        return f"{cycles:.2f}".rstrip("0").rstrip(".")
    return f"{cycles:.1f}".rstrip("0").rstrip(".")


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _parse_timer_cycles: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _parse_timer_cycles(text: str, frequency: float) -> float:
    """Parse numeric cycle counts such as '128', '1000', or '128 cycles'."""
    raw = str(text).strip().lower().replace(",", "")
    raw = raw.replace("cycles", "").replace("cycle", "").replace("cyc", "").strip()
    if raw in {"", "one", "single"}:
        return 1.0
    try:
        cycles = float(raw.split()[0])
    except (ValueError, IndexError):
        cycles = 1.0
    return clamp_float(cycles, 1.0, _timer_max_cycles_for_frequency(frequency))


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _timer_seconds_from_cycle_entry: small named steps make the signal
# TUTORIAL: path, UI path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _timer_seconds_from_cycle_entry(self) -> float:
    frequency = self._current_timer_frequency()
    try:
        cycles = _parse_timer_cycles(self.play_timer_cycles_var.get(), frequency)
    except Exception:
        cycles = 1.0
    return _clamp_timer_seconds(cycles / max(frequency, MIN_FREQUENCY), frequency)


App._timer_seconds_from_cycle_entry = _timer_seconds_from_cycle_entry


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _timer_seconds_from_widgets: small named steps make the signal path,
# TUTORIAL: UI path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _timer_seconds_from_widgets(self) -> float:
    """Return timer duration from the duration field by default.

    Play uses this value. The dedicated cycle entry commits into the same shared
    slider/seconds state, so after editing Cycles, the duration field is already
    synchronised.
    """
    frequency = self._current_timer_frequency()
    try:
        entry_text = self.play_timer_entry_var.get() if hasattr(self, "play_timer_entry_var") else "1 cycle"
        seconds = _parse_timer_duration(entry_text, frequency)
    except Exception:
        seconds = _timer_min_seconds_for_frequency(frequency)
    return _clamp_timer_seconds(seconds, frequency)


App._timer_seconds_from_widgets = _timer_seconds_from_widgets


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _set_timer_widgets_from_seconds: small named steps make the signal
# TUTORIAL: path, UI path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _set_timer_widgets_from_seconds(
    self,
    seconds: float,
    *,
    update_entry: bool = True,
    update_cycle_entry: bool = True,
) -> float:
    frequency = self._current_timer_frequency()
    seconds = _clamp_timer_seconds(seconds, frequency)
    minimum = _timer_min_seconds_for_frequency(frequency)
    cycles = seconds * clamp_float(frequency, MIN_FREQUENCY, MAX_FREQUENCY)

    try:
        if hasattr(self, "play_timer_slider"):
            self.play_timer_slider.config(from_=minimum, to=PLAY_TIMER_MAX_SECONDS)
        if hasattr(self, "play_timer_slider_var"):
            self._suppress_timer_slider = True
            try:
                self.play_timer_slider_var.set(seconds)
            finally:
                self._suppress_timer_slider = False
        if update_entry and hasattr(self, "play_timer_entry_var"):
            self.play_timer_entry_var.set(_format_timer_seconds(seconds))
        if update_cycle_entry and hasattr(self, "play_timer_cycles_var"):
            self.play_timer_cycles_var.set(_format_timer_cycle_count(cycles))
        if hasattr(self, "play_timer_cycle_label"):
            self.play_timer_cycle_label.config(
                text=(
                    f"min 1 cycle = {_format_timer_seconds(minimum)}; "
                    f"selected = {_format_timer_seconds(seconds)} ≈ {_format_timer_cycles(seconds, frequency)}"
                )
            )
    except Exception:
        pass
    return seconds


App._set_timer_widgets_from_seconds = _set_timer_widgets_from_seconds


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _set_timer_widgets_from_cycles: small named steps make the signal
# TUTORIAL: path, UI path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _set_timer_widgets_from_cycles(self, cycles: float, *, update_entry: bool = True, update_cycle_entry: bool = True) -> float:
    frequency = self._current_timer_frequency()
    cycles = clamp_float(cycles, 1.0, _timer_max_cycles_for_frequency(frequency))
    seconds = cycles / max(frequency, MIN_FREQUENCY)
    return self._set_timer_widgets_from_seconds(
        seconds,
        update_entry=update_entry,
        update_cycle_entry=update_cycle_entry,
    )


App._set_timer_widgets_from_cycles = _set_timer_widgets_from_cycles


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _timer_slider_changed: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _timer_slider_changed(self, _value: str | None = None) -> None:
    if getattr(self, "_suppress_timer_slider", False):
        return
    try:
        seconds = float(self.play_timer_slider_var.get())
    except Exception:
        seconds = _timer_min_seconds_for_frequency(self._current_timer_frequency())
    self._set_timer_widgets_from_seconds(seconds, update_entry=True, update_cycle_entry=True)


App._timer_slider_changed = _timer_slider_changed


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _timer_entry_committed: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _timer_entry_committed(self, _event=None) -> None:
    seconds = self._timer_seconds_from_widgets()
    self._set_timer_widgets_from_seconds(seconds, update_entry=True, update_cycle_entry=True)
    if hasattr(self, "status"):
        self.status.config(
            text=f"PLAY TIMER  {_format_timer_seconds(seconds)} ≈ {_format_timer_cycles(seconds, self._current_timer_frequency())}"
        )


App._timer_entry_committed = _timer_entry_committed


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _timer_cycles_committed: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _timer_cycles_committed(self, _event=None) -> None:
    frequency = self._current_timer_frequency()
    try:
        cycles = _parse_timer_cycles(self.play_timer_cycles_var.get(), frequency)
    except Exception:
        cycles = 1.0
    seconds = self._set_timer_widgets_from_cycles(cycles, update_entry=True, update_cycle_entry=True)
    if hasattr(self, "status"):
        self.status.config(
            text=f"PLAY TIMER  {_format_timer_cycle_count(seconds * frequency)} cycles = {_format_timer_seconds(seconds)}"
        )


App._timer_cycles_committed = _timer_cycles_committed


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _refresh_timer_for_frequency: small named steps make the signal
# TUTORIAL: path, UI path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _refresh_timer_for_frequency(self) -> None:
    # Preserve the current duration when pitch changes, then refresh the displayed
    # cycle count because cycles = seconds × frequency.
    try:
        current = float(self.play_timer_slider_var.get()) if hasattr(self, "play_timer_slider_var") else _timer_min_seconds_for_frequency(self._current_timer_frequency())
    except Exception:
        current = _timer_min_seconds_for_frequency(self._current_timer_frequency())
    self._set_timer_widgets_from_seconds(current, update_entry=False, update_cycle_entry=True)


App._refresh_timer_for_frequency = _refresh_timer_for_frequency


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: UI construction helper: _add_transport_timer_controls creates a reusable group of Tk
# TUTORIAL: controls. Keeping these small avoids one enormous constructor.
# TUTORIAL: ------------------------------------------------------------------------
def _add_transport_timer_controls(self) -> None:
    if getattr(self, "_transport_timer_controls_added", False):
        return
    self._transport_timer_controls_added = True

    reset_frame = _find_reset_button_frame(self)

    timer_frame = tk.Frame(self.control_parent)
    if reset_frame is not None:
        timer_frame.pack(fill="x", padx=12, pady=(8, 2), before=reset_frame)
    else:
        timer_frame.pack(fill="x", padx=12, pady=(8, 2))

    tk.Label(timer_frame, text="Play timer", width=16, anchor="w").pack(side="left")
    self.play_timer_slider_var = tk.DoubleVar(value=_timer_min_seconds_for_frequency(self._current_timer_frequency()))
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Tk Scale is the slider widget. In this app, slider callbacks push new values
    # TUTORIAL: into patch or audio state.
    # TUTORIAL: ------------------------------------------------------------------------
    self.play_timer_slider = tk.Scale(
        timer_frame,
        variable=self.play_timer_slider_var,
        from_=_timer_min_seconds_for_frequency(self._current_timer_frequency()),
        to=PLAY_TIMER_MAX_SECONDS,
        resolution=PLAY_TIMER_SLIDER_RESOLUTION,
        showvalue=False,
        orient="horizontal",
        length=260,
        command=self._timer_slider_changed,
    )
    self.play_timer_slider.pack(side="left", fill="x", expand=True)

    tk.Label(timer_frame, text="Time").pack(side="left", padx=(8, 4))
    self.play_timer_entry_var = tk.StringVar(value="1 cycle")
    entry = tk.Entry(timer_frame, textvariable=self.play_timer_entry_var, width=14, justify="right")
    entry.pack(side="left")
    entry.bind("<Return>", self._timer_entry_committed)
    entry.bind("<FocusOut>", self._timer_entry_committed)

    tk.Label(timer_frame, text="Cycles").pack(side="left", padx=(8, 4))
    self.play_timer_cycles_var = tk.StringVar(value="1")
    cycle_entry = tk.Entry(timer_frame, textvariable=self.play_timer_cycles_var, width=10, justify="right")
    cycle_entry.pack(side="left")
    cycle_entry.bind("<Return>", self._timer_cycles_committed)
    cycle_entry.bind("<FocusOut>", self._timer_cycles_committed)

    self.play_timer_cycle_label = tk.Label(timer_frame, anchor="w", text="")
    self.play_timer_cycle_label.pack(side="left", padx=(8, 0), fill="x", expand=True)

    transport_frame = tk.Frame(self.control_parent)
    if reset_frame is not None:
        transport_frame.pack(fill="x", padx=12, pady=(2, 6), before=reset_frame)
    else:
        transport_frame.pack(fill="x", padx=12, pady=(2, 6))

    tk.Label(transport_frame, text="Transport", width=16, anchor="w").pack(side="left")
    tk.Button(transport_frame, text="Drone", command=self.drone_audio).pack(side="left")
    tk.Button(transport_frame, text="Stop", command=self.stop_audio).pack(side="left", padx=(8, 0))
    tk.Button(transport_frame, text="Play (Timer)", command=self.play_timed_audio).pack(side="left", padx=(8, 0))

    self._set_timer_widgets_from_seconds(_timer_min_seconds_for_frequency(self._current_timer_frequency()), update_entry=True, update_cycle_entry=True)


App._add_transport_timer_controls = _add_transport_timer_controls



# -----------------------------------------------------------------------------
# Randomise button override
#
# Randomise is intentionally scoped to the patch-shaping controls only:
# Core Seed, 3-Step Growth, 9-Step Growth, wavetable type, FM/PWM/AM, and the
# before/after generation-window start/end points. It leaves pitch, FM range,
# volume, output resolution, sample rate, and play timer untouched.
# -----------------------------------------------------------------------------

import random as _random


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _random_window_pair: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _random_window_pair() -> tuple[int, int]:
    """Return a random start/end pair on the 0..4096 generation-window scale."""
    start = _random.randint(0, int(WINDOW_STEPS) - 1)
    # Pick a length rather than two unrelated endpoints, so the result is less
    # likely to collapse to a microscopic slice. Wraparound windows are allowed.
    length = _random.randint(int(MIN_WINDOW_STEPS), int(WINDOW_STEPS))
    if length >= int(WINDOW_STEPS):
        return 0, int(WINDOW_STEPS)
    end = (start + length) % int(WINDOW_STEPS)
    return start, end


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function randomise_patch: small named steps make the signal path, UI path,
# TUTORIAL: and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def randomise_patch(self) -> None:
    """Randomise the waveform-generation and self-mod controls only."""
    try:
        before_start, before_end = _random_window_pair()
        after_start, after_end = _random_window_pair()

        self.vars["pot1"].set(_random.randint(0, POT_MAX_12BIT))
        self.vars["pot2"].set(_random.randint(0, POT_MAX_12BIT))
        self.vars["pot3"].set(_random.randint(0, POT_MAX_12BIT))
        self.vars["type"].set(_random.randint(0, WAVETABLE_TYPE_MAX))
        self.vars["fm"].set(_random.randint(0, 255))
        self.vars["pwm"].set(_random.randint(0, 255))
        self.vars["am"].set(_random.randint(0, 255))
        self.vars["before_start"].set(before_start)
        self.vars["before_end"].set(before_end)
        self.vars["after_start"].set(after_start)
        self.vars["after_end"].set(after_end)

        self._update_type_description()
        self._push_modulation(source="randomise")
        self.update_table()
        self._draw_current_waveform()

        if hasattr(self, "status"):
            self.status.config(
                text=(
                    "RANDOMISE  generator/mod/window controls changed; "
                    "frequency, FM range, volume, sample rate, bit depth, and play timer unchanged"
                )
            )
    except Exception as exc:
        if hasattr(self, "status"):
            self.status.config(text=f"RANDOMISE ERROR  {type(exc).__name__}: {exc}")


App.randomise_patch = randomise_patch


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: UI construction helper: _add_randomise_controls creates a reusable group of Tk
# TUTORIAL: controls. Keeping these small avoids one enormous constructor.
# TUTORIAL: ------------------------------------------------------------------------
def _add_randomise_controls(self) -> None:
    if getattr(self, "_randomise_controls_added", False):
        return
    self._randomise_controls_added = True

    frame = tk.Frame(self.control_parent)
    children = self.control_parent.winfo_children()
    pack_kwargs = {"fill": "x", "padx": 12, "pady": (8, 4)}
    if children:
        frame.pack(**pack_kwargs, before=children[0])
    else:
        frame.pack(**pack_kwargs)

    tk.Label(frame, text="Patch", width=16, anchor="w").pack(side="left")
    tk.Button(frame, text="Randomise", command=self.randomise_patch).pack(side="left")
    tk.Label(
        frame,
        anchor="w",
        text="changes generator, FM/PWM/AM, and generation windows only",
    ).pack(side="left", padx=(8, 0), fill="x", expand=True)


_previous_app_init_for_randomise = App.__init__


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _app_init_for_randomise: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _app_init_for_randomise(self, *args, **kwargs):
    _previous_app_init_for_randomise(self, *args, **kwargs)
    self._add_randomise_controls()


App.__init__ = _app_init_for_randomise
App._add_randomise_controls = _add_randomise_controls


# -----------------------------------------------------------------------------
# Expanded wavetable mode list + drop-down selector
#
# The numeric type slider worked while there were only ten modes. With the
# richer/mellower experiments, a long drop-down is more useful because the user
# can see the mode number, terse name, and a short tonal description together.
# -----------------------------------------------------------------------------

WAVETABLE_MODE_INFO = {
    0:  ("3-Step Source", "3-step recurrence driven by Core Seed × 3-Step Growth"),
    1:  ("9-Step Source", "9-step recurrence shaped by Core Seed × 9-Step Growth"),
    2:  ("9→3 Splice", "First half 9-step source, second half 3-step source"),
    3:  ("3→9 Splice", "First half 3-step source, second half 9-step source"),
    4:  ("Sum", "Normalised 3-step and 9-step sources added together"),
    5:  ("Difference", "3-step source minus 9-step source"),
    6:  ("Multiply", "Sources multiplied sample-by-sample"),
    7:  ("Divide", "3-step source divided by protected 9-step magnitude"),
    8:  ("Bit OR", "8-bit source collision using bitwise OR"),
    9:  ("Bit XOR", "8-bit source collision using bitwise XOR"),
    10: ("Bit AND", "8-bit source collision using bitwise AND"),
    11: ("Bit XNOR", "Inverted XOR; wide plateaus and hard flips"),
    12: ("Min", "Takes the lower of the two sources at each sample"),
    13: ("Max", "Takes the higher of the two sources at each sample"),
    14: ("Abs Difference", "Rectified source difference; buzzy and octave-like"),
    15: ("Fold Sum", "Wavefolded sum for rich synth-like harmonics"),
    16: ("Fold Difference", "Wavefolded difference for metallic hollow harmonics"),
    17: ("Comparator", "Hard ±1 output depending on whether 3-step exceeds 9-step"),
    18: ("Interleave", "Alternates samples from the 3-step and 9-step sources"),
    19: ("Edge", "Derivative-style mode that emphasises moving edges"),
    20: ("Average", "Balanced average of the two sources"),
    21: ("Mostly 3-Step", "Weighted blend biased toward the 3-step source"),
    22: ("Mostly 9-Step", "Weighted blend biased toward the 9-step source"),
    23: ("Core Crossfade", "Core Seed crossfades between 3-step and 9-step sources"),
    24: ("Smooth Average", "Low-pass smoothed source average"),
    25: ("Smooth Difference", "Rounded version of the difference mode"),
    26: ("Sine Shaper", "Trigonometric rounding of the blended source"),
    27: ("Soft Saturate", "Mild tanh saturation for warmer compression"),
    28: ("Triangle Fold Soft", "Gentle triangular folding with rounded edges"),
    29: ("Root Shape", "Lifts quiet detail while rounding the extremes"),
    30: ("Power Shape", "Darker shaping that suppresses mid-level detail"),
    31: ("Zero-Cross Blend", "Blends toward a sine-like curve near zero crossings"),
    32: ("Hann Windowed", "Raised-cosine cycle window that softens boundary bite"),
    33: ("Phase Blur", "Chorus-like internal smoothing inside one cycle"),
    34: ("Odd Soft", "Symmetric odd-harmonic leaning soft blend"),
    35: ("Even Soft", "Symmetric even-harmonic leaning soft blend"),
    36: ("Gated 9-Step", "9-step source chopped by the sign of the 3-step source"),
    37: ("Sign Multiply", "9-step source polarity-flipped by the 3-step source"),
    38: ("Soft Clip Sum", "Heavier tanh clipping of the source sum"),
    39: ("Hard Clip Sum", "Flat-topped clipped source sum with strong harmonics"),
}

WAVETABLE_TYPE_MAX = max(WAVETABLE_MODE_INFO)
WT_TYPES_USING_ITER2 = set(range(WAVETABLE_TYPE_MAX + 1)) - {1}

WAVETABLE_DESCRIPTIONS.clear()
WAVETABLE_DESCRIPTIONS.update({
    number: f"Type {number}: {name} — {description}."
    for number, (name, description) in WAVETABLE_MODE_INFO.items()
})


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function wavetable_type_option: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def wavetable_type_option(wt_type: int) -> str:
    wt = clamp_int(int(wt_type), 0, WAVETABLE_TYPE_MAX)
    name, description = WAVETABLE_MODE_INFO.get(wt, ("Unknown", "Unknown wavetable mode"))
    return f"{wt} - {name} - {description}"


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function wavetable_type_from_option: small named steps make the signal path,
# TUTORIAL: UI path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def wavetable_type_from_option(text: str | int) -> int:
    if isinstance(text, int):
        return clamp_int(text, 0, WAVETABLE_TYPE_MAX)
    raw = str(text).strip()
    try:
        return clamp_int(int(raw.split("-", 1)[0].strip()), 0, WAVETABLE_TYPE_MAX)
    except Exception:
        try:
            return clamp_int(int(float(raw)), 0, WAVETABLE_TYPE_MAX)
        except Exception:
            return 0


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function fold_bipolar: small named steps make the signal path, UI path, and
# TUTORIAL: export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def fold_bipolar(x: np.ndarray | float) -> np.ndarray:
    """Fold arbitrary values into the bipolar -1..+1 range."""
    y = (np.asarray(x, dtype=WORK_DTYPE) + 1.0) % 4.0
    return np.where(y < 2.0, y - 1.0, 3.0 - y)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function circular_smooth_table: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def circular_smooth_table(x: np.ndarray, passes: int = 2) -> np.ndarray:
    y = np.asarray(x, dtype=WORK_DTYPE).copy()
    for _ in range(max(1, int(passes))):
        y = (np.roll(y, 1) + 2.0 * y + np.roll(y, -1)) * 0.25
    return y


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function phase_blur_table: small named steps make the signal path, UI path,
# TUTORIAL: and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def phase_blur_table(x: np.ndarray) -> np.ndarray:
    y = np.asarray(x, dtype=WORK_DTYPE)
    return (
        0.36 * y
        + 0.18 * np.roll(y, 1)
        + 0.18 * np.roll(y, -1)
        + 0.10 * np.roll(y, 3)
        + 0.10 * np.roll(y, -3)
        + 0.04 * np.roll(y, 9)
        + 0.04 * np.roll(y, -9)
    )


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: The core generator. It builds the iter2 and iter3 recurrence arrays, then combines
# TUTORIAL: or selects them according to wavetable type.
# TUTORIAL: Later in the file this function name is redefined to add generation-stage windows
# TUTORIAL: and 12-bit pot behaviour. That layered style is why this script works like a visible
# TUTORIAL: development history.
# TUTORIAL: ------------------------------------------------------------------------
def generate_wavetable(
    pot1: int,
    pot2: int,
    pot3: int,
    wt_type: int,
    before_start: float = 0.0,
    before_length: float = WINDOW_STEPS,
    after_start: float = 0.0,
    after_length: float = WINDOW_STEPS,
) -> np.ndarray:  # type: ignore[override]
    """Generate one 256-sample bipolar wavetable from 12-bit controls."""
    global LAST_WAVETABLE_NOTES
    LAST_WAVETABLE_NOTES = []

    pot1 = clamp_int(pot1, 0, POT_MAX_12BIT)
    pot2 = clamp_int(pot2, 0, POT_MAX_12BIT)
    pot3 = clamp_int(pot3, 0, POT_MAX_12BIT)
    wt_type = clamp_int(wt_type, 0, WAVETABLE_TYPE_MAX)

    max_val = 0.0
    max_val2 = 0.0
    temp = np.zeros(TABLE_SIZE, dtype=WORK_DTYPE)
    iter2 = np.zeros(TABLE_SIZE, dtype=WORK_DTYPE)
    iter3 = np.zeros(TABLE_SIZE, dtype=WORK_DTYPE)

    seed0 = pot_to_seed_12bit(pot1)
    seed1 = pot_to_seed_12bit(pot2)
    seed2 = pot_to_seed_12bit(pot3)

    iter2_multiplier = seed1 * seed0
    if wt_type in WT_TYPES_USING_ITER2 and iter2_multiplier > SAFE_ITER2_MULTIPLIER:
        iter2_multiplier = SAFE_ITER2_MULTIPLIER

    temp[0] = seed0 * seed0
    temp[1] = iter2_multiplier
    temp[2] = seed2 * seed0

    iter2[0:3] = temp[0:3]
    for i in range(3, TABLE_SIZE):
        iter2[i] = iter2[i - 3] * iter2[1]

    iter3[0:9] = iter2[0:9]
    for i in range(9, TABLE_SIZE):
        iter3[i] = iter3[i - 9] * iter3[2]

    if not is_full_window(before_start, before_length):
        iter2 = window_table_linear(iter2, before_start, before_length)
        iter3 = window_table_linear(iter3, before_start, before_length)

    if wt_type == 0:
        raw = iter2

    elif wt_type == 1:
        raw = iter3

    elif wt_type in (2, 3):
        skip = 0
        for j in range(TABLE_SIZE):
            if wt_type == 2:
                if j < 128:
                    temp[j] = iter3[skip]
                    max_val = max(max_val, float(temp[j]))
                else:
                    temp[j] = iter2[skip - 256]
                    max_val2 = max(max_val2, float(temp[j]))
            else:
                if j < 128:
                    temp[j] = iter2[skip]
                    max_val = max(max_val, float(temp[j]))
                else:
                    temp[j] = iter3[skip - 256]
                    max_val2 = max(max_val2, float(temp[j]))
            skip += 2

        out = np.zeros(TABLE_SIZE, dtype=WORK_DTYPE)
        if math.isfinite(max_val) and max_val > EPSILON:
            out[:128] = (temp[:128] / max_val) * 256.0
        if math.isfinite(max_val2) and max_val2 > EPSILON:
            out[128:] = (temp[128:] / max_val2) * 256.0
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
        # TUTORIAL: samples, and table generation can sometimes hit extreme math.
        # TUTORIAL: ------------------------------------------------------------------------
        raw = (np.nan_to_num(out, nan=0.0, posinf=256.0, neginf=0.0) / 128.0) - 1.0

    else:
        a = normalise_bipolar(iter2)
        b = normalise_bipolar(iter3)
        c = (a + b) * 0.5
        phase = np.linspace(0.0, 2.0 * math.pi, TABLE_SIZE, endpoint=False, dtype=WORK_DTYPE)

        if wt_type == 4:
            raw = a + b
        elif wt_type == 5:
            raw = a - b
        elif wt_type == 6:
            raw = a * b
        elif wt_type == 7:
            raw = a / np.maximum(np.abs(b), 0.05)
        elif wt_type == 8:
            raw = u8_to_bipolar(bipolar_to_u8(a) | bipolar_to_u8(b))
        elif wt_type == 9:
            raw = u8_to_bipolar(bipolar_to_u8(a) ^ bipolar_to_u8(b))
        elif wt_type == 10:
            raw = u8_to_bipolar(bipolar_to_u8(a) & bipolar_to_u8(b))
        elif wt_type == 11:
            raw = u8_to_bipolar(np.uint8(255) ^ (bipolar_to_u8(a) ^ bipolar_to_u8(b)))
        elif wt_type == 12:
            raw = np.minimum(a, b)
        elif wt_type == 13:
            raw = np.maximum(a, b)
        elif wt_type == 14:
            raw = np.abs(a - b)
        elif wt_type == 15:
            raw = fold_bipolar(a + b)
        elif wt_type == 16:
            raw = fold_bipolar(a - b)
        elif wt_type == 17:
            raw = np.where(a > b, 1.0, -1.0)
        elif wt_type == 18:
            raw = np.where((np.arange(TABLE_SIZE) & 1) == 0, a, b)
        elif wt_type == 19:
            raw = np.roll(c, -1) - c
        elif wt_type == 20:
            raw = c
        elif wt_type == 21:
            raw = 0.75 * a + 0.25 * b
        elif wt_type == 22:
            raw = 0.25 * a + 0.75 * b
        elif wt_type == 23:
            mix = clamp_float(seed0 / 2.0, 0.0, 1.0)
            raw = (1.0 - mix) * a + mix * b
        elif wt_type == 24:
            raw = circular_smooth_table(c, passes=3)
        elif wt_type == 25:
            raw = circular_smooth_table(a - b, passes=3)
        elif wt_type == 26:
            raw = np.sin(c * (math.pi / 2.0))
        elif wt_type == 27:
            raw = np.tanh(1.8 * c)
        elif wt_type == 28:
            raw = 0.65 * fold_bipolar(0.85 * (a + b)) + 0.35 * c
        elif wt_type == 29:
            # TUTORIAL: ------------------------------------------------------------------------
            # TUTORIAL: Clip the signal/control range after math that might overshoot. This
            # TUTORIAL: keeps audio buffers and tables bounded.
            # TUTORIAL: ------------------------------------------------------------------------
            raw = np.sign(c) * np.sqrt(np.clip(np.abs(c), 0.0, 1.0))
        elif wt_type == 30:
            # TUTORIAL: ------------------------------------------------------------------------
            # TUTORIAL: Clip the signal/control range after math that might overshoot. This
            # TUTORIAL: keeps audio buffers and tables bounded.
            # TUTORIAL: ------------------------------------------------------------------------
            raw = np.sign(c) * (np.clip(np.abs(c), 0.0, 1.0) ** 2.0)
        elif wt_type == 31:
            sine = np.sin(phase)
            # TUTORIAL: ------------------------------------------------------------------------
            # TUTORIAL: Clip the signal/control range after math that might overshoot. This
            # TUTORIAL: keeps audio buffers and tables bounded.
            # TUTORIAL: ------------------------------------------------------------------------
            zero_weight = np.clip(1.0 - (np.abs(c) * 2.0), 0.0, 1.0)
            raw = (1.0 - 0.45 * zero_weight) * c + (0.45 * zero_weight) * sine
        elif wt_type == 32:
            raw = c * np.hanning(TABLE_SIZE)
        elif wt_type == 33:
            raw = phase_blur_table(c)
        elif wt_type == 34:
            raw = 0.5 * (c - c[::-1])
        elif wt_type == 35:
            raw = 0.5 * (c + c[::-1])
        elif wt_type == 36:
            raw = np.where(a > 0.0, b, 0.0)
        elif wt_type == 37:
            raw = np.where(a >= 0.0, 1.0, -1.0) * b
        elif wt_type == 38:
            raw = np.tanh(3.0 * (a + b))
        elif wt_type == 39:
            # TUTORIAL: ------------------------------------------------------------------------
            # TUTORIAL: Clip the signal/control range after math that might overshoot. This
            # TUTORIAL: keeps audio buffers and tables bounded.
            # TUTORIAL: ------------------------------------------------------------------------
            raw = np.clip(2.5 * (a + b), -1.0, 1.0)
        else:
            raw = iter2

    if not is_full_window(after_start, after_length):
        raw = window_table_linear(raw, after_start, after_length)

    return finalise_raw_table_checked_windowed(
        raw,
        before_start=before_start,
        before_length=before_length,
        after_start=after_start,
        after_length=after_length,
    )


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Export helper: _wavetable_type_option_changed turns in-memory rendered
# TUTORIAL: audio/settings into files on disk. This is separate from live playback by design.
# TUTORIAL: ------------------------------------------------------------------------
def _wavetable_type_option_changed(self, value: str) -> None:
    wt_type = wavetable_type_from_option(value)
    self.vars["type"].set(wt_type)
    self.schedule_update()


App._wavetable_type_option_changed = _wavetable_type_option_changed


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Export helper: _replace_wavetable_type_control turns in-memory rendered
# TUTORIAL: audio/settings into files on disk. This is separate from live playback by design.
# TUTORIAL: ------------------------------------------------------------------------
def _replace_wavetable_type_control(self) -> None:
    if getattr(self, "_wavetable_type_dropdown_added", False):
        return
    self._wavetable_type_dropdown_added = True

    current_type = clamp_int(int(self.vars["type"].get()), 0, WAVETABLE_TYPE_MAX)
    self.vars["type"].set(current_type)
    self.wavetable_type_var = tk.StringVar(value=wavetable_type_option(current_type))

    old_frame = None
    try:
        type_var_name = str(self.vars["type"])
        for frame in self.control_parent.winfo_children():
            if not isinstance(frame, tk.Frame):
                continue
            for child in frame.winfo_children():
                if isinstance(child, tk.Scale) and str(child.cget("variable")) == type_var_name:
                    old_frame = frame
                    break
            if old_frame is not None:
                break
    except Exception:
        old_frame = None

    frame = tk.Frame(self.control_parent)
    pack_kwargs = {"fill": "x", "padx": 12, "pady": 4}
    if old_frame is not None:
        frame.pack(**pack_kwargs, before=old_frame)
    elif hasattr(self, "type_description"):
        frame.pack(**pack_kwargs, before=self.type_description)
    else:
        frame.pack(**pack_kwargs)

    tk.Label(frame, text="Wavetable type", width=16, anchor="w").pack(side="left")
    options = [wavetable_type_option(i) for i in sorted(WAVETABLE_MODE_INFO)]
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: OptionMenu is used for compact dropdowns such as wavetable type, pitch range,
    # TUTORIAL: and export settings.
    # TUTORIAL: ------------------------------------------------------------------------
    menu = tk.OptionMenu(
        frame,
        self.wavetable_type_var,
        *options,
        command=lambda value: self._wavetable_type_option_changed(value),
    )
    menu.config(width=84, anchor="w")
    menu.pack(side="left", fill="x", expand=True)
    self.wavetable_type_menu = menu

    if old_frame is not None:
        try:
            old_frame.destroy()
        except Exception:
            pass

    self._update_type_description()


App._replace_wavetable_type_control = _replace_wavetable_type_control


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _update_type_description_expanded: small named steps make the signal
# TUTORIAL: path, UI path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _update_type_description_expanded(self) -> None:
    if not hasattr(self, "type_description"):
        return
    wt_type = clamp_int(int(self.vars["type"].get()), 0, WAVETABLE_TYPE_MAX)
    self.vars["type"].set(wt_type)
    description = WAVETABLE_DESCRIPTIONS.get(wt_type, "Unknown wavetable type.")
    self.type_description.config(text=description)
    if hasattr(self, "wavetable_type_var"):
        option = wavetable_type_option(wt_type)
        if self.wavetable_type_var.get() != option:
            self.wavetable_type_var.set(option)


App._update_type_description = _update_type_description_expanded


_previous_app_init_for_expanded_wavetable_modes = App.__init__


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Export helper: _app_init_for_expanded_wavetable_modes turns in-memory rendered
# TUTORIAL: audio/settings into files on disk. This is separate from live playback by design.
# TUTORIAL: ------------------------------------------------------------------------
def _app_init_for_expanded_wavetable_modes(self, *args, **kwargs):
    _previous_app_init_for_expanded_wavetable_modes(self, *args, **kwargs)
    self._replace_wavetable_type_control()
    self._update_type_description()


App.__init__ = _app_init_for_expanded_wavetable_modes



# -----------------------------------------------------------------------------
# Tabbed START/END morph-export application
#
# This is a cleaner UI layer built on the existing generator, safety, FM range,
# and AudioEngine code above. It keeps the original single-engine playground as
# one tab, and adds a START→END morph/export tab with WAV + JSON sidecar output.
# Selecting a tab makes that tab the only live-audition source.
# -----------------------------------------------------------------------------

import json as _json
import os as _os
import time as _time
import wave as _wave
from collections import OrderedDict as _OrderedDict
from dataclasses import asdict as _asdict
from tkinter import filedialog as _filedialog
from tkinter import ttk as _ttk


MORPH_MAX_SECONDS = 60.0
MORPH_DEFAULT_CYCLES = 64
MORPH_MAX_TABLE_FRAMES = 4096
MORPH_TABLE_CACHE_LIMIT = 512
MORPH_HEADROOM_DEFAULT_DB = -0.1


@dataclass
# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: A complete patch endpoint for the tabbed START/END system. These values are the
# TUTORIAL: things that can be rendered, exported, saved in JSON, and later imported as a patch.
# TUTORIAL: ------------------------------------------------------------------------
class PatchSettings:
    pot1: int = DEFAULT_POT1
    pot2: int = DEFAULT_POT2
    pot3: int = DEFAULT_POT3
    wavetable_type: int = 3
    fm: int = 0
    pwm: int = 0
    am: int = 0
    before_start: int = 0
    before_end: int = int(WINDOW_STEPS)
    after_start: int = 0
    after_end: int = int(WINDOW_STEPS)
    fm_range_label: str = DEFAULT_FM_RANGE_LABEL


@dataclass
# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Summary of the requested export converted into concrete facts: sample count,
# TUTORIAL: duration, cycles, warnings, and suggestions. This is useful for both UI display and
# TUTORIAL: JSON sidecars.
# TUTORIAL: ------------------------------------------------------------------------
class RenderSummary:
    requested_mode: str
    requested_value: float
    sample_rate: int
    bit_depth: str
    frequency: float
    sample_count: int
    duration_seconds: float
    nominal_cycles: float
    samples_per_cycle: float
    warnings: list[str]
    suggestions: list[str]


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _safe_float_from_var: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _safe_float_from_var(var: tk.Variable, default: float) -> float:
    try:
        return float(str(var.get()).strip())
    except Exception:
        return float(default)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _safe_int_from_var: small named steps make the signal path, UI path,
# TUTORIAL: and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _safe_int_from_var(var: tk.Variable, default: int) -> int:
    try:
        return int(float(str(var.get()).strip()))
    except Exception:
        return int(default)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _format_seconds_compact: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _format_seconds_compact(seconds: float) -> str:
    if seconds >= 1.0:
        return f"{seconds:.6f} s"
    return f"{seconds * 1000.0:.3f} ms"


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Maps a linear 0..1 progress value into the selected transition curve. This is the
# TUTORIAL: one scalar that drives parameter morphing and wavetable-type crossfades.
# TUTORIAL: The UI exposes 1..255, with 127 as linear. The math here turns that compact control
# TUTORIAL: into bend or S-curve behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _curve_value(x: float, amount: int = 127, mode: str = "Bend") -> float:
    """Map 0..1 through the user-facing transition curve.

    Bend mode:
      amount < 127 = slow start / fast finish
      amount = 127 = linear
      amount > 127 = fast start / slow finish

    S-curve mode:
      amount controls how strongly the curve eases both ends. 127 is linear;
      either extreme approaches a smoothstep-like S curve.
    """
    t = clamp_float(x, 0.0, 1.0)
    a = clamp_int(amount, 1, 255)
    if a == 127:
        return t

    mode = str(mode or "Bend")
    if mode.startswith("S"):
        strength = min(abs(a - 127) / 128.0, 1.0)
        smooth = t * t * t * (t * (t * 6.0 - 15.0) + 10.0)
        return clamp_float((1.0 - strength) * t + strength * smooth, 0.0, 1.0)

    if a < 127:
        # Slow early, faster late. The left extreme is deliberately strong but
        # finite so the endpoint is still reached exactly.
        power = 2.0 ** ((127 - a) / 32.0)
        return clamp_float(t ** power, 0.0, 1.0)

    # Fast early, slower late.
    power = 2.0 ** ((a - 127) / 32.0)
    return clamp_float(1.0 - ((1.0 - t) ** power), 0.0, 1.0)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _lerp: small named steps make the signal path, UI path, and export
# TUTORIAL: path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _lerp(a: float, b: float, t: float) -> float:
    return float(a) + (float(b) - float(a)) * float(t)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _lerp_int: small named steps make the signal path, UI path, and
# TUTORIAL: export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _lerp_int(a: int, b: int, t: float, lo: int, hi: int) -> int:
    return clamp_int(int(round(_lerp(a, b, t))), lo, hi)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Morph/patch helper: _patch_window_lengths moves between UI variables, PatchSettings
# TUTORIAL: objects, generated tables, and rendered audio.
# TUTORIAL: ------------------------------------------------------------------------
def _patch_window_lengths(patch: PatchSettings) -> tuple[float, float, float, float, list[str]]:
    before_start, before_length, before_notes = clamp_window_pair(
        patch.before_start, patch.before_end, "before"
    )
    after_start, after_length, after_notes = clamp_window_pair(
        patch.after_start, patch.after_end, "after"
    )
    return before_start, before_length, after_start, after_length, before_notes + after_notes


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Converts a PatchSettings object into an actual audio table, collecting any safety or
# TUTORIAL: clamp notes produced along the way.
# TUTORIAL: ------------------------------------------------------------------------
def _table_for_patch(patch: PatchSettings, *, wavetable_type: int | None = None) -> tuple[np.ndarray, list[str]]:
    wt_type = patch.wavetable_type if wavetable_type is None else int(wavetable_type)
    before_start, before_length, after_start, after_length, window_notes = _patch_window_lengths(patch)
    p1, p2, p3, eff_type, _vol, notes = clamp_control_values(
        patch.pot1, patch.pot2, patch.pot3, wt_type, 1.0
    )
    table = generate_wavetable(
        p1,
        p2,
        p3,
        eff_type,
        before_start=before_start,
        before_length=before_length,
        after_start=after_start,
        after_length=after_length,
    )
    gen_notes = list(globals().get("LAST_WAVETABLE_NOTES", []))
    if not table_is_usable(table):
        table = fallback_table()
        gen_notes.append("unsafe table replaced with fallback square")
    return table.astype(np.float32), notes + window_notes + gen_notes


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Creates an in-between patch by interpolating numeric controls from START to END.
# TUTORIAL: This is parameter morphing: one engine whose knobs move over time.
# TUTORIAL: ------------------------------------------------------------------------
def _interpolated_patch(start: PatchSettings, end: PatchSettings, t: float, *, shared_type: int | None) -> PatchSettings:
    wt = start.wavetable_type if shared_type is None else int(shared_type)
    return PatchSettings(
        pot1=_lerp_int(start.pot1, end.pot1, t, 0, POT_MAX_12BIT),
        pot2=_lerp_int(start.pot2, end.pot2, t, 0, POT_MAX_12BIT),
        pot3=_lerp_int(start.pot3, end.pot3, t, 0, POT_MAX_12BIT),
        wavetable_type=clamp_int(wt, 0, WAVETABLE_TYPE_MAX),
        fm=_lerp_int(start.fm, end.fm, t, 0, 255),
        pwm=_lerp_int(start.pwm, end.pwm, t, 0, 255),
        am=_lerp_int(start.am, end.am, t, 0, 255),
        before_start=_lerp_int(start.before_start, end.before_start, t, 0, int(WINDOW_STEPS)),
        before_end=_lerp_int(start.before_end, end.before_end, t, 0, int(WINDOW_STEPS)),
        after_start=_lerp_int(start.after_start, end.after_start, t, 0, int(WINDOW_STEPS)),
        after_end=_lerp_int(start.after_end, end.after_end, t, 0, int(WINDOW_STEPS)),
        fm_range_label=start.fm_range_label,
    )


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Helper function _render_length_summary: small named steps make the signal path, UI
# TUTORIAL: path, and export path easier to inspect and test.
# TUTORIAL: ------------------------------------------------------------------------
def _render_length_summary(mode: str, value: float, frequency: float, sample_rate: int, bit_depth: str) -> RenderSummary:
    warnings: list[str] = []
    suggestions: list[str] = []
    sr = max(1, int(sample_rate))
    freq = clamp_float(frequency, MIN_FREQUENCY, MAX_FREQUENCY)
    samples_per_cycle = sr / freq
    one_cycle_seconds = 1.0 / freq
    max_samples = int(round(MORPH_MAX_SECONDS * sr))

    mode = str(mode or "Cycles")
    requested = float(value)
    if mode == "Seconds":
        seconds = clamp_float(requested, one_cycle_seconds, MORPH_MAX_SECONDS)
        sample_count = clamp_int(int(round(seconds * sr)), max(1, int(math.ceil(samples_per_cycle))), max_samples)
    elif mode == "Samples":
        minimum = max(1, int(math.ceil(samples_per_cycle)))
        sample_count = clamp_int(int(round(requested)), minimum, max_samples)
    else:
        cycles = max(1.0, float(requested))
        sample_count = clamp_int(int(round((cycles / freq) * sr)), max(1, int(math.ceil(samples_per_cycle))), max_samples)

    duration = sample_count / float(sr)
    nominal_cycles = duration * freq

    if sample_count >= max_samples and duration >= MORPH_MAX_SECONDS - 1e-9:
        warnings.append("duration clamped to 60 seconds")
    if samples_per_cycle < 32:
        warnings.append(f"very low cycle resolution: {samples_per_cycle:.2f} samples/cycle")
    elif samples_per_cycle < 128:
        warnings.append(f"low cycle resolution: {samples_per_cycle:.2f} samples/cycle")

    for spp in (256, 512, 1024):
        suggested_freq = sr / float(spp)
        if MIN_FREQUENCY <= suggested_freq <= MAX_FREQUENCY:
            suggestions.append(f"{suggested_freq:.6g} Hz gives exactly {spp} samples/cycle at {sr} Hz")

    return RenderSummary(
        requested_mode=mode,
        requested_value=requested,
        sample_rate=sr,
        bit_depth=str(bit_depth),
        frequency=freq,
        sample_count=sample_count,
        duration_seconds=duration,
        nominal_cycles=nominal_cycles,
        samples_per_cycle=samples_per_cycle,
        warnings=warnings,
        suggestions=suggestions,
    )


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Per-cycle cleanup stage for exported audio. This supports the sound-design goal:
# TUTORIAL: every cycle can become a flat, centred oscillator/wavetable frame.
# TUTORIAL: ------------------------------------------------------------------------
def _apply_cycle_dc_and_normalise(
    audio: np.ndarray,
    *,
    frequency: float,
    sample_rate: int,
    dc_mode: str,
    norm_mode: str,
    headroom_db: float,
) -> np.ndarray:
    y = np.asarray(audio, dtype=np.float64).copy()
    dc_mode = str(dc_mode or "Per cycle")
    norm_mode = str(norm_mode or "Per cycle")
    gain_target = 10.0 ** (float(headroom_db) / 20.0)
    gain_target = clamp_float(gain_target, 0.0, 1.0)

    if dc_mode == "Whole file":
        y -= float(np.mean(y)) if y.size else 0.0
    elif dc_mode == "Per cycle":
        samples_per_cycle = sample_rate / max(float(frequency), MIN_FREQUENCY)
        cycle_count = int(math.ceil(y.size / max(samples_per_cycle, 1.0)))
        # Above this, per-cycle post-processing is either too slow or has too few
        # samples per cycle to be meaningful. The render itself still succeeds.
        if cycle_count <= 200_000 and samples_per_cycle >= 4:
            for c in range(cycle_count):
                a = int(round(c * samples_per_cycle))
                b = int(round((c + 1) * samples_per_cycle))
                if b <= a:
                    continue
                segment = y[a:min(b, y.size)]
                if segment.size:
                    y[a:min(b, y.size)] = segment - float(np.mean(segment))
        else:
            y -= float(np.mean(y)) if y.size else 0.0

    if norm_mode == "Whole file peak":
        peak = float(np.max(np.abs(y))) if y.size else 0.0
        if math.isfinite(peak) and peak > EPSILON:
            y *= gain_target / peak
    elif norm_mode == "Per cycle peak":
        samples_per_cycle = sample_rate / max(float(frequency), MIN_FREQUENCY)
        cycle_count = int(math.ceil(y.size / max(samples_per_cycle, 1.0)))
        if cycle_count <= 200_000 and samples_per_cycle >= 4:
            for c in range(cycle_count):
                a = int(round(c * samples_per_cycle))
                b = int(round((c + 1) * samples_per_cycle))
                if b <= a:
                    continue
                segment = y[a:min(b, y.size)]
                if segment.size:
                    peak = float(np.max(np.abs(segment)))
                    if math.isfinite(peak) and peak > EPSILON:
                        y[a:min(b, y.size)] = segment * (gain_target / peak)
        else:
            peak = float(np.max(np.abs(y))) if y.size else 0.0
            if math.isfinite(peak) and peak > EPSILON:
                y *= gain_target / peak

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
    # TUTORIAL: samples, and table generation can sometimes hit extreme math.
    # TUTORIAL: ------------------------------------------------------------------------
    return np.clip(np.nan_to_num(y, nan=0.0, posinf=1.0, neginf=-1.0), -1.0, 1.0).astype(np.float32)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Writes the rendered mono audio buffer to a WAV file. The code handles unusual
# TUTORIAL: requested depths such as 8-bit and 12-bit by mapping them into WAV-compatible
# TUTORIAL: containers.
# TUTORIAL: ------------------------------------------------------------------------
def _write_wav_mono(path: str, audio: np.ndarray, sample_rate: int, bit_depth: str) -> None:
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Clip the signal/control range after math that might overshoot. This keeps audio
    # TUTORIAL: buffers and tables bounded.
    # TUTORIAL: ------------------------------------------------------------------------
    y = np.clip(np.asarray(audio, dtype=np.float64), -1.0, 1.0)
    bit_depth = str(bit_depth)
    with _wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setframerate(int(sample_rate))
        if bit_depth.startswith("16"):
            wf.setsampwidth(2)
            data = (y * 32767.0).astype("<i2").tobytes()
        elif bit_depth.startswith("24"):
            wf.setsampwidth(3)
            # TUTORIAL: ------------------------------------------------------------------------
            # TUTORIAL: Clip the signal/control range after math that might overshoot. This
            # TUTORIAL: keeps audio buffers and tables bounded.
            # TUTORIAL: ------------------------------------------------------------------------
            vals = np.clip(np.round(y * 8388607.0), -8388608, 8388607).astype(np.int32)
            b = bytearray()
            for v in vals:
                iv = int(v)
                if iv < 0:
                    iv += 1 << 24
                b.extend((iv & 0xFF, (iv >> 8) & 0xFF, (iv >> 16) & 0xFF))
            data = bytes(b)
        else:
            wf.setsampwidth(4)
            data = (y * 2147483647.0).astype("<i4").tobytes()
        wf.writeframes(data)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Class _TableCache: groups state and behaviour that would otherwise be scattered
# TUTORIAL: through globals and callbacks.
# TUTORIAL: ------------------------------------------------------------------------
class _TableCache:
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function __init__: small named steps make the signal path, UI path, and
    # TUTORIAL: export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def __init__(
        self,
        start: PatchSettings,
        end: PatchSettings,
        *,
        shared_type: int | None,
        different_types: bool,
        different_fm_ranges: bool,
        frame_count: int,
        curve_amount: int,
        curve_mode: str,
    ):
        self.start = start
        self.end = end
        self.shared_type = shared_type
        self.different_types = bool(different_types)
        self.different_fm_ranges = bool(different_fm_ranges)
        self.frame_count = max(1, int(frame_count))
        self.curve_amount = int(curve_amount)
        self.curve_mode = curve_mode
        self.cache: _OrderedDict[int, tuple[np.ndarray, int, int, int, float]] = _OrderedDict()
        self.notes: list[str] = []

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function frame: small named steps make the signal path, UI path, and
    # TUTORIAL: export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def frame(self, index: int) -> tuple[np.ndarray, int, int, int, float]:
        idx = clamp_int(index, 0, self.frame_count - 1)
        if idx in self.cache:
            value = self.cache.pop(idx)
            self.cache[idx] = value
            return value

        raw_t = 0.0 if self.frame_count <= 1 else idx / float(self.frame_count - 1)
        t = _curve_value(raw_t, self.curve_amount, self.curve_mode)

        shared_type = None if self.different_types else self.shared_type
        patch = _interpolated_patch(self.start, self.end, t, shared_type=shared_type)

        if self.different_types:
            patch_a = _interpolated_patch(self.start, self.end, t, shared_type=self.start.wavetable_type)
            patch_b = _interpolated_patch(self.start, self.end, t, shared_type=self.end.wavetable_type)
            table_a, notes_a = _table_for_patch(patch_a, wavetable_type=self.start.wavetable_type)
            table_b, notes_b = _table_for_patch(patch_b, wavetable_type=self.end.wavetable_type)
            table = ((1.0 - t) * table_a + t * table_b).astype(np.float32)
            table = normalise_bipolar(table).astype(np.float32) if table_is_usable(table) else fallback_table()
            self.notes.extend(notes_a + notes_b)
        else:
            table, notes = _table_for_patch(patch, wavetable_type=patch.wavetable_type)
            self.notes.extend(notes)

        if self.different_fm_ranges:
            start_cents = fm_range_label_to_cents(self.start.fm_range_label)
            end_cents = fm_range_label_to_cents(self.end.fm_range_label)
            fm_range_cents = _lerp(start_cents, end_cents, t)
        else:
            fm_range_cents = fm_range_label_to_cents(self.start.fm_range_label)

        value = (table.astype(np.float32), patch.fm, patch.pwm, patch.am, fm_range_cents)
        self.cache[idx] = value
        while len(self.cache) > MORPH_TABLE_CACHE_LIMIT:
            self.cache.popitem(last=False)
        return value


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Offline renderer for the START→END morph export. Unlike the live audio callback, it
# TUTORIAL: can do heavier work because it is not constrained by real-time deadlines.
# TUTORIAL: ------------------------------------------------------------------------
def _render_morph_audio(
    start: PatchSettings,
    end: PatchSettings,
    *,
    summary: RenderSummary,
    shared_type: int | None,
    different_types: bool,
    different_fm_ranges: bool,
    curve_amount: int,
    curve_mode: str,
    cycle_stepped: bool,
    reverse: bool,
    dc_mode: str,
    norm_mode: str,
    headroom_db: float,
) -> tuple[np.ndarray, list[str]]:
    n = max(1, int(summary.sample_count))
    sr = int(summary.sample_rate)
    freq = clamp_float(summary.frequency, MIN_FREQUENCY, MAX_FREQUENCY)
    nominal_cycles = max(1.0, summary.nominal_cycles)

    if cycle_stepped:
        desired_frames = max(1, int(round(nominal_cycles)))
    else:
        # Smooth enough for ordinary morphs while keeping render time sane.
        desired_frames = min(MORPH_MAX_TABLE_FRAMES, max(2, int(round(min(nominal_cycles * 4.0, MORPH_MAX_TABLE_FRAMES)))))
    frame_count = clamp_int(desired_frames, 1, MORPH_MAX_TABLE_FRAMES)

    warnings = list(summary.warnings)
    if desired_frames > frame_count:
        warnings.append(f"transition frames clamped to {frame_count}")
    if cycle_stepped and int(round(nominal_cycles)) > MORPH_MAX_TABLE_FRAMES:
        warnings.append(f"cycle-stepped render uses {frame_count} transition frames for {nominal_cycles:.0f} cycles")

    cache = _TableCache(
        start,
        end,
        shared_type=shared_type,
        different_types=different_types,
        different_fm_ranges=different_fm_ranges,
        frame_count=frame_count,
        curve_amount=curve_amount,
        curve_mode=curve_mode,
    )

    out = np.empty(n, dtype=np.float32)
    base_phase_inc = freq / float(sr)
    phase = 0.0
    for i in range(n):
        raw_pos = 0.0 if n <= 1 else i / float(n - 1)
        if reverse:
            raw_pos = 1.0 - raw_pos

        if cycle_stepped:
            cycle = int(math.floor((i / float(sr)) * freq))
            if reverse:
                cycle = int(max(0, round(nominal_cycles) - 1 - cycle))
            idx = clamp_int(cycle, 0, frame_count - 1)
        else:
            idx = clamp_int(int(round(raw_pos * (frame_count - 1))), 0, frame_count - 1)

        table, fm, pwm, am, fm_range_cents = cache.frame(idx)
        mod_sample = read_table_linear(table, phase)
        am_depth = clamp_float(am, 0.0, 255.0) / 255.0
        am_gain = (1.0 - am_depth) + am_depth * (0.5 + 0.5 * mod_sample)
        am_gain = clamp_float(am_gain, 0.0, 1.5)
        mod_signal = clamp_float(mod_sample * am_gain, -1.0, 1.0)

        pwm_depth = clamp_float(pwm, 0.0, 255.0) / 255.0 * 0.48
        width = clamp_float(0.5 + pwm_depth * mod_signal, 0.02, 0.98)
        if phase < width:
            read_phase = (phase / width) * 0.5
        else:
            read_phase = 0.5 + ((phase - width) / (1.0 - width)) * 0.5

        out[i] = read_table_linear(table, read_phase) * am_gain
        fm_scale = bounded_fm_phase_scale(mod_signal, fm, fm_range_cents)
        phase += min(base_phase_inc * fm_scale, 0.49)
        if phase >= 1.0:
            phase -= math.floor(phase)

    out = _apply_cycle_dc_and_normalise(
        out,
        frequency=freq,
        sample_rate=sr,
        dc_mode=dc_mode,
        norm_mode=norm_mode,
        headroom_db=headroom_db,
    )
    notes = warnings + sorted(set(str(n) for n in cache.notes if n))[:20]
    return out, notes


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Class TabbedFractalApp: groups state and behaviour that would otherwise be scattered
# TUTORIAL: through globals and callbacks.
# TUTORIAL: ------------------------------------------------------------------------
class TabbedFractalApp:
    """Two-mode UI: single-engine playground plus START→END morph export."""

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function __init__: small named steps make the signal path, UI path, and
    # TUTORIAL: export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def __init__(self, root: tk.Tk, state: SharedState, initial_frequency: float, sample_rate: int):
        self.root = root
        self.state = state
        self.sample_rate = int(sample_rate)
        self.default_frequency = clamp_float(initial_frequency, MIN_FREQUENCY, MAX_FREQUENCY)
        self.audio_engine = None
        self._suppress = False
        self.root.title("Fractal Wavetable — Playground + START→END Export")
        self.root.geometry("1800x1000")
        self.root.minsize(1280, 780)

        self.notebook = _ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True)
        self.playground_tab = tk.Frame(self.notebook)
        self.morph_tab = tk.Frame(self.notebook)
        self.notebook.add(self.playground_tab, text="Single Engine / Playground")
        self.notebook.add(self.morph_tab, text="START→END Morph Export")
        self.notebook.bind("<<NotebookTabChanged>>", self._tab_changed)

        self.status = tk.Label(root, anchor="w", text="Ready.")
        self.status.pack(fill="x", padx=10, pady=(4, 8))

        self.visualisers_visible = tk.BooleanVar(value=True)
        self.single_vars = self._new_patch_vars(PatchSettings())
        self.start_vars = self._new_patch_vars(PatchSettings())
        self.end_vars = self._new_patch_vars(PatchSettings(pot1=3072, pot2=1600, pot3=3000, wavetable_type=9, fm=48, pwm=32, am=0))

        self._build_playground_tab()
        self._build_morph_tab()
        self._push_playground_to_audio()
        self._update_all_visualisers()
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: Tk uses scheduled callbacks instead of long blocking loops. after(...) lets
        # TUTORIAL: the UI stay responsive.
        # TUTORIAL: ------------------------------------------------------------------------
        self.root.after(1000, self._audio_health_tick)

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Morph/patch helper: _new_patch_vars moves between UI variables, PatchSettings
    # TUTORIAL: objects, generated tables, and rendered audio.
    # TUTORIAL: ------------------------------------------------------------------------
    def _new_patch_vars(self, patch: PatchSettings) -> dict[str, tk.Variable]:
        return {
            "pot1": tk.IntVar(value=patch.pot1),
            "pot2": tk.IntVar(value=patch.pot2),
            "pot3": tk.IntVar(value=patch.pot3),
            "wavetable_type": tk.IntVar(value=patch.wavetable_type),
            "fm": tk.IntVar(value=patch.fm),
            "pwm": tk.IntVar(value=patch.pwm),
            "am": tk.IntVar(value=patch.am),
            "before_start": tk.IntVar(value=patch.before_start),
            "before_end": tk.IntVar(value=patch.before_end),
            "after_start": tk.IntVar(value=patch.after_start),
            "after_end": tk.IntVar(value=patch.after_end),
            "fm_range_label": tk.StringVar(value=patch.fm_range_label),
        }

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Morph/patch helper: _patch_from_vars moves between UI variables, PatchSettings
    # TUTORIAL: objects, generated tables, and rendered audio.
    # TUTORIAL: ------------------------------------------------------------------------
    def _patch_from_vars(self, vars_dict: dict[str, tk.Variable]) -> PatchSettings:
        return PatchSettings(
            pot1=clamp_int(_safe_int_from_var(vars_dict["pot1"], DEFAULT_POT1), 0, POT_MAX_12BIT),
            pot2=clamp_int(_safe_int_from_var(vars_dict["pot2"], DEFAULT_POT2), 0, POT_MAX_12BIT),
            pot3=clamp_int(_safe_int_from_var(vars_dict["pot3"], DEFAULT_POT3), 0, POT_MAX_12BIT),
            wavetable_type=clamp_int(_safe_int_from_var(vars_dict["wavetable_type"], 3), 0, WAVETABLE_TYPE_MAX),
            fm=clamp_int(_safe_int_from_var(vars_dict["fm"], 0), 0, 255),
            pwm=clamp_int(_safe_int_from_var(vars_dict["pwm"], 0), 0, 255),
            am=clamp_int(_safe_int_from_var(vars_dict["am"], 0), 0, 255),
            before_start=clamp_int(_safe_int_from_var(vars_dict["before_start"], 0), 0, int(WINDOW_STEPS)),
            before_end=clamp_int(_safe_int_from_var(vars_dict["before_end"], int(WINDOW_STEPS)), 0, int(WINDOW_STEPS)),
            after_start=clamp_int(_safe_int_from_var(vars_dict["after_start"], 0), 0, int(WINDOW_STEPS)),
            after_end=clamp_int(_safe_int_from_var(vars_dict["after_end"], int(WINDOW_STEPS)), 0, int(WINDOW_STEPS)),
            fm_range_label=str(vars_dict["fm_range_label"].get()),
        )

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: UI construction helper: _add_slider creates a reusable group of Tk controls.
    # TUTORIAL: Keeping these small avoids one enormous constructor.
    # TUTORIAL: ------------------------------------------------------------------------
    def _add_slider(self, parent, label: str, var: tk.Variable, lo: int | float, hi: int | float, resolution: int | float, command, width: int = 15):
        row = tk.Frame(parent)
        row.pack(fill="x", padx=8, pady=2)
        tk.Label(row, text=label, width=width, anchor="w").pack(side="left")
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: Tk Scale is the slider widget. In this app, slider callbacks push new values
        # TUTORIAL: into patch or audio state.
        # TUTORIAL: ------------------------------------------------------------------------
        scale = tk.Scale(
            row,
            variable=var,
            from_=lo,
            to=hi,
            resolution=resolution,
            orient="horizontal",
            showvalue=True,
            length=240,
            command=lambda _v: command(),
        )
        scale.pack(side="left", fill="x", expand=True)
        return scale

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Morph/patch helper: _add_patch_controls moves between UI variables,
    # TUTORIAL: PatchSettings objects, generated tables, and rendered audio.
    # TUTORIAL: ------------------------------------------------------------------------
    def _add_patch_controls(self, parent, vars_dict: dict[str, tk.Variable], *, command, include_fm_range: bool = False):
        self._add_slider(parent, "Core Seed", vars_dict["pot1"], 0, POT_MAX_12BIT, 1, command)
        self._add_slider(parent, "3-Step Growth", vars_dict["pot2"], 0, POT_MAX_12BIT, 1, command)
        self._add_slider(parent, "9-Step Growth", vars_dict["pot3"], 0, POT_MAX_12BIT, 1, command)

        row = tk.Frame(parent)
        row.pack(fill="x", padx=8, pady=2)
        tk.Label(row, text="Wavetable Type", width=15, anchor="w").pack(side="left")
        spin = tk.Spinbox(
            row,
            from_=0,
            to=WAVETABLE_TYPE_MAX,
            increment=1,
            width=6,
            textvariable=vars_dict["wavetable_type"],
            command=command,
        )
        spin.pack(side="left")
        spin.bind("<Return>", lambda _e: command())
        spin.bind("<FocusOut>", lambda _e: command())
        desc = tk.Label(row, anchor="w", text="", wraplength=420)
        desc.pack(side="left", padx=(8, 0), fill="x", expand=True)
        vars_dict["_type_desc"] = desc  # type: ignore[assignment]

        self._add_slider(parent, "FM", vars_dict["fm"], 0, 255, 1, command)
        self._add_slider(parent, "PWM", vars_dict["pwm"], 0, 255, 1, command)
        self._add_slider(parent, "AM", vars_dict["am"], 0, 255, 1, command)

        if include_fm_range:
            row = tk.Frame(parent)
            row.pack(fill="x", padx=8, pady=2)
            tk.Label(row, text="FM Range", width=15, anchor="w").pack(side="left")
            # TUTORIAL: ------------------------------------------------------------------------
            # TUTORIAL: OptionMenu is used for compact dropdowns such as wavetable type, pitch
            # TUTORIAL: range, and export settings.
            # TUTORIAL: ------------------------------------------------------------------------
            menu = tk.OptionMenu(row, vars_dict["fm_range_label"], *[label for label, _c in FM_RANGE_OPTIONS], command=lambda _v: command())
            menu.config(width=24)
            menu.pack(side="left")

        for label, key in (
            ("Source Start", "before_start"),
            ("Source End", "before_end"),
            ("Result Start", "after_start"),
            ("Result End", "after_end"),
        ):
            self._add_slider(parent, label, vars_dict[key], 0, int(WINDOW_STEPS), 1, command)

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: UI construction helper: _build_playground_tab creates a reusable group of Tk
    # TUTORIAL: controls. Keeping these small avoids one enormous constructor.
    # TUTORIAL: ------------------------------------------------------------------------
    def _build_playground_tab(self) -> None:
        outer = tk.Frame(self.playground_tab)
        outer.pack(fill="both", expand=True)
        controls = tk.Frame(outer)
        controls.pack(side="left", fill="both", expand=True, padx=8, pady=8)
        visual = tk.Frame(outer, width=560)
        visual.pack(side="right", fill="both", padx=8, pady=8)
        visual.pack_propagate(False)
        self.single_visual_parent = visual

        top = tk.Frame(controls)
        top.pack(fill="x", pady=(0, 6))
        tk.Button(top, text="Randomise", command=self._randomise_single).pack(side="left")
        tk.Button(top, text="Reset", command=self._reset_single).pack(side="left", padx=(8, 0))
        tk.Button(top, text="Panic audio", command=self._panic_audio).pack(side="left", padx=(8, 0))

        self._add_patch_controls(controls, self.single_vars, command=self._playground_changed, include_fm_range=True)
        self._add_shared_live_controls(controls, command=self._playground_changed)

        tk.Label(visual, text="Playground waveform", anchor="center").pack(fill="x")
        self.single_canvas = tk.Canvas(visual, width=520, height=420, bg="black", highlightthickness=1, highlightbackground="#555555")
        self.single_canvas.pack(fill="both", expand=True, pady=(6, 0))

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: UI construction helper: _add_shared_live_controls creates a reusable group of Tk
    # TUTORIAL: controls. Keeping these small avoids one enormous constructor.
    # TUTORIAL: ------------------------------------------------------------------------
    def _add_shared_live_controls(self, parent, *, command) -> None:
        self.frequency_var = tk.DoubleVar(value=self.default_frequency)
        self.volume_var = tk.DoubleVar(value=0.20)
        row = tk.Frame(parent)
        row.pack(fill="x", padx=8, pady=(8, 2))
        tk.Label(row, text="Frequency", width=15, anchor="w").pack(side="left")
        freq = tk.Entry(row, textvariable=self.frequency_var, width=12, justify="right")
        freq.pack(side="left")
        freq.bind("<Return>", lambda _e: command())
        freq.bind("<FocusOut>", lambda _e: command())
        tk.Label(row, text="Hz").pack(side="left", padx=(4, 0))
        self._add_slider(parent, "Volume", self.volume_var, 0.0, 1.0, 0.01, command)

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Morph/patch helper: _build_morph_tab moves between UI variables, PatchSettings
    # TUTORIAL: objects, generated tables, and rendered audio.
    # TUTORIAL: ------------------------------------------------------------------------
    def _build_morph_tab(self) -> None:
        outer = tk.Frame(self.morph_tab)
        outer.pack(fill="both", expand=True)

        toolbar = tk.Frame(outer)
        toolbar.pack(fill="x", padx=8, pady=(8, 4))
        tk.Button(toolbar, text="Randomise START", command=lambda: self._randomise_morph("start")).pack(side="left")
        tk.Button(toolbar, text="Randomise END", command=lambda: self._randomise_morph("end")).pack(side="left", padx=(6, 0))
        tk.Button(toolbar, text="Randomise BOTH", command=lambda: self._randomise_morph("both")).pack(side="left", padx=(6, 0))
        tk.Button(toolbar, text="Copy START→END", command=self._copy_start_to_end).pack(side="left", padx=(12, 0))
        tk.Button(toolbar, text="Copy END→START", command=self._copy_end_to_start).pack(side="left", padx=(6, 0))
        tk.Button(toolbar, text="Swap", command=self._swap_start_end).pack(side="left", padx=(6, 0))
        tk.Checkbutton(toolbar, text="Show visualisers", variable=self.visualisers_visible, command=self._toggle_visualisers).pack(side="right")

        panels = tk.Frame(outer)
        panels.pack(fill="both", expand=True, padx=8, pady=4)
        self.start_panel = self._build_endpoint_panel(panels, "START", self.start_vars)
        self.start_panel.pack(side="left", fill="both", expand=True, padx=(0, 4))
        self.end_panel = self._build_endpoint_panel(panels, "END", self.end_vars)
        self.end_panel.pack(side="left", fill="both", expand=True, padx=(4, 0))

        shared = tk.LabelFrame(outer, text="Shared transition / output / export")
        shared.pack(fill="x", padx=8, pady=(4, 8))
        self._build_morph_shared_controls(shared)

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: UI construction helper: _build_endpoint_panel creates a reusable group of Tk
    # TUTORIAL: controls. Keeping these small avoids one enormous constructor.
    # TUTORIAL: ------------------------------------------------------------------------
    def _build_endpoint_panel(self, parent, title: str, vars_dict: dict[str, tk.Variable]):
        panel = tk.LabelFrame(parent, text=title)
        visual_frame = tk.Frame(panel)
        visual_frame.pack(fill="x", padx=6, pady=(6, 0))
        canvas = tk.Canvas(visual_frame, width=420, height=190, bg="black", highlightthickness=1, highlightbackground="#555555")
        canvas.pack(fill="x", expand=True)
        vars_dict["_canvas"] = canvas  # type: ignore[assignment]
        self._add_patch_controls(panel, vars_dict, command=self._morph_changed, include_fm_range=False)
        return panel

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Morph/patch helper: _build_morph_shared_controls moves between UI variables,
    # TUTORIAL: PatchSettings objects, generated tables, and rendered audio.
    # TUTORIAL: ------------------------------------------------------------------------
    def _build_morph_shared_controls(self, parent) -> None:
        self.shared_type_var = tk.IntVar(value=3)
        self.different_types_var = tk.BooleanVar(value=False)
        self.shared_fm_range_var = tk.StringVar(value=DEFAULT_FM_RANGE_LABEL)
        self.different_fm_ranges_var = tk.BooleanVar(value=False)
        self.duration_mode_var = tk.StringVar(value="Cycles")
        self.duration_value_var = tk.StringVar(value=str(MORPH_DEFAULT_CYCLES))
        self.sample_rate_var = tk.IntVar(value=self.sample_rate)
        self.bit_depth_var = tk.StringVar(value="24-bit PCM")
        self.curve_mode_var = tk.StringVar(value="Bend")
        self.curve_amount_var = tk.IntVar(value=127)
        self.cycle_stepped_var = tk.BooleanVar(value=True)
        self.direction_var = tk.StringVar(value="START→END")
        self.dc_mode_var = tk.StringVar(value="Per cycle")
        self.normalise_mode_var = tk.StringVar(value="Per cycle peak")
        self.headroom_var = tk.DoubleVar(value=MORPH_HEADROOM_DEFAULT_DB)
        self.filename_var = tk.StringVar(value=self._default_export_name())
        self.consequence_var = tk.StringVar(value="")

        line1 = tk.Frame(parent); line1.pack(fill="x", padx=8, pady=3)
        tk.Checkbutton(line1, text="Different START/END wavetable types", variable=self.different_types_var, command=self._morph_changed).pack(side="left")
        tk.Label(line1, text="Shared type").pack(side="left", padx=(12, 4))
        tk.Spinbox(line1, from_=0, to=WAVETABLE_TYPE_MAX, width=6, textvariable=self.shared_type_var, command=self._morph_changed).pack(side="left")
        tk.Label(line1, text="FM Range").pack(side="left", padx=(18, 4))
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: OptionMenu is used for compact dropdowns such as wavetable type, pitch
        # TUTORIAL: range, and export settings.
        # TUTORIAL: ------------------------------------------------------------------------
        tk.OptionMenu(line1, self.shared_fm_range_var, *[label for label, _c in FM_RANGE_OPTIONS], command=lambda _v: self._morph_changed()).pack(side="left")
        tk.Checkbutton(line1, text="Different START/END FM ranges", variable=self.different_fm_ranges_var, command=self._morph_changed).pack(side="left", padx=(12, 0))

        line2 = tk.Frame(parent); line2.pack(fill="x", padx=8, pady=3)
        tk.Label(line2, text="Duration").pack(side="left")
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: OptionMenu is used for compact dropdowns such as wavetable type, pitch
        # TUTORIAL: range, and export settings.
        # TUTORIAL: ------------------------------------------------------------------------
        tk.OptionMenu(line2, self.duration_mode_var, "Seconds", "Cycles", "Samples", command=lambda _v: self._morph_changed()).pack(side="left", padx=(6, 0))
        tk.Entry(line2, textvariable=self.duration_value_var, width=10, justify="right").pack(side="left", padx=(6, 0))
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: OptionMenu is used for compact dropdowns such as wavetable type, pitch
        # TUTORIAL: range, and export settings.
        # TUTORIAL: ------------------------------------------------------------------------
        tk.OptionMenu(line2, self.direction_var, "START→END", "END→START", "Both", command=lambda _v: self._morph_changed()).pack(side="left", padx=(14, 0))
        tk.Checkbutton(line2, text="Cycle-stepped transition", variable=self.cycle_stepped_var, command=self._morph_changed).pack(side="left", padx=(14, 0))
        tk.Label(line2, text="Curve").pack(side="left", padx=(14, 4))
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: OptionMenu is used for compact dropdowns such as wavetable type, pitch
        # TUTORIAL: range, and export settings.
        # TUTORIAL: ------------------------------------------------------------------------
        tk.OptionMenu(line2, self.curve_mode_var, "Bend", "S-curve", command=lambda _v: self._morph_changed()).pack(side="left")
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: Tk Scale is the slider widget. In this app, slider callbacks push new values
        # TUTORIAL: into patch or audio state.
        # TUTORIAL: ------------------------------------------------------------------------
        tk.Scale(line2, variable=self.curve_amount_var, from_=1, to=255, resolution=1, orient="horizontal", length=180, command=lambda _v: self._morph_changed()).pack(side="left", padx=(6, 0))

        line3 = tk.Frame(parent); line3.pack(fill="x", padx=8, pady=3)
        tk.Label(line3, text="Sample rate").pack(side="left")
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: OptionMenu is used for compact dropdowns such as wavetable type, pitch
        # TUTORIAL: range, and export settings.
        # TUTORIAL: ------------------------------------------------------------------------
        tk.OptionMenu(line3, self.sample_rate_var, 44100, 48000, 88200, 96000, command=lambda _v: self._morph_changed()).pack(side="left", padx=(6, 0))
        tk.Label(line3, text="Bit depth").pack(side="left", padx=(14, 4))
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: OptionMenu is used for compact dropdowns such as wavetable type, pitch
        # TUTORIAL: range, and export settings.
        # TUTORIAL: ------------------------------------------------------------------------
        tk.OptionMenu(line3, self.bit_depth_var, "16-bit PCM", "24-bit PCM", "32-bit PCM", command=lambda _v: self._morph_changed()).pack(side="left")
        tk.Label(line3, text="DC removal").pack(side="left", padx=(14, 4))
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: OptionMenu is used for compact dropdowns such as wavetable type, pitch
        # TUTORIAL: range, and export settings.
        # TUTORIAL: ------------------------------------------------------------------------
        tk.OptionMenu(line3, self.dc_mode_var, "Off", "Whole file", "Per cycle", command=lambda _v: self._morph_changed()).pack(side="left")
        tk.Label(line3, text="Normalise").pack(side="left", padx=(14, 4))
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: OptionMenu is used for compact dropdowns such as wavetable type, pitch
        # TUTORIAL: range, and export settings.
        # TUTORIAL: ------------------------------------------------------------------------
        tk.OptionMenu(line3, self.normalise_mode_var, "Off", "Whole file peak", "Per cycle peak", command=lambda _v: self._morph_changed()).pack(side="left")
        tk.Label(line3, text="Headroom dB").pack(side="left", padx=(14, 4))
        tk.Entry(line3, textvariable=self.headroom_var, width=7, justify="right").pack(side="left")

        line4 = tk.Frame(parent); line4.pack(fill="x", padx=8, pady=3)
        tk.Label(line4, text="Filename").pack(side="left")
        tk.Entry(line4, textvariable=self.filename_var, width=52).pack(side="left", padx=(6, 0), fill="x", expand=True)
        tk.Button(line4, text="Choose…", command=self._choose_export_file).pack(side="left", padx=(6, 0))
        tk.Button(line4, text="Audition START", command=lambda: self._push_morph_endpoint_to_audio("start")).pack(side="left", padx=(12, 0))
        tk.Button(line4, text="Audition END", command=lambda: self._push_morph_endpoint_to_audio("end")).pack(side="left", padx=(6, 0))
        tk.Button(line4, text="Create WAV + JSON", command=self._create_wav_clicked).pack(side="left", padx=(12, 0))

        tk.Label(parent, textvariable=self.consequence_var, anchor="w", justify="left", wraplength=1600).pack(fill="x", padx=8, pady=(2, 6))
        self._morph_changed()

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Export helper: _default_export_name turns in-memory rendered audio/settings into
    # TUTORIAL: files on disk. This is separate from live playback by design.
    # TUTORIAL: ------------------------------------------------------------------------
    def _default_export_name(self) -> str:
        return f"fractal_wt_morph_{int(_time.time())}.wav"

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function _summary_from_ui: small named steps make the signal path, UI
    # TUTORIAL: path, and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def _summary_from_ui(self) -> RenderSummary:
        mode = str(self.duration_mode_var.get())
        value = _safe_float_from_var(self.duration_value_var, MORPH_DEFAULT_CYCLES)
        freq = _safe_float_from_var(self.frequency_var, self.default_frequency)
        sr = _safe_int_from_var(self.sample_rate_var, self.sample_rate)
        return _render_length_summary(mode, value, freq, sr, str(self.bit_depth_var.get()))

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function _update_type_descriptions: small named steps make the signal
    # TUTORIAL: path, UI path, and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def _update_type_descriptions(self) -> None:
        for vars_dict in (self.single_vars, self.start_vars, self.end_vars):
            label = vars_dict.get("_type_desc")
            if isinstance(label, tk.Label):
                wt = clamp_int(_safe_int_from_var(vars_dict["wavetable_type"], 3), 0, WAVETABLE_TYPE_MAX)
                name, desc = WAVETABLE_MODE_INFO.get(wt, ("Unknown", "")) if "WAVETABLE_MODE_INFO" in globals() else (str(wt), "")
                label.config(text=f"{name}: {desc}")

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Visualiser helper: _draw_table draws waveform previews. Visualiser code should
    # TUTORIAL: be informative, but it should not be the source of truth for rendering.
    # TUTORIAL: ------------------------------------------------------------------------
    def _draw_table(self, canvas: tk.Canvas, table: np.ndarray, label: str) -> None:
        if not bool(self.visualisers_visible.get()):
            return
        w = max(int(canvas.winfo_width()), 200)
        h = max(int(canvas.winfo_height()), 120)
        pad = 14
        canvas.delete("all")
        canvas.create_rectangle(pad, pad, w - pad, h - pad, outline="#444444")
        mid = h / 2.0
        canvas.create_line(pad, mid, w - pad, mid, fill="#333333")
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
        # TUTORIAL: samples, and table generation can sometimes hit extreme math.
        # TUTORIAL: ------------------------------------------------------------------------
        y = np.nan_to_num(np.asarray(table, dtype=np.float64).reshape(-1), nan=0.0, posinf=1.0, neginf=-1.0)
        if y.size < 2:
            y = fallback_table().astype(np.float64)
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: Clip the signal/control range after math that might overshoot. This keeps
        # TUTORIAL: audio buffers and tables bounded.
        # TUTORIAL: ------------------------------------------------------------------------
        y = np.clip(y, -1.0, 1.0)
        pts = []
        for i, sample in enumerate(y):
            x = pad + (w - 2 * pad) * (i / max(y.size - 1, 1))
            yy = mid - float(sample) * ((h - 2 * pad) * 0.46)
            pts.extend([x, yy])
        canvas.create_line(*pts, fill="#00d0ff", width=2)
        canvas.create_text(pad, h - 4, anchor="sw", fill="#999999", text=label)

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Visualiser helper: _update_all_visualisers draws waveform previews. Visualiser
    # TUTORIAL: code should be informative, but it should not be the source of truth for
    # TUTORIAL: rendering.
    # TUTORIAL: ------------------------------------------------------------------------
    def _update_all_visualisers(self) -> None:
        self._update_type_descriptions()
        try:
            table, _notes = _table_for_patch(self._patch_from_vars(self.single_vars))
            self._draw_table(self.single_canvas, table, "single-engine table")
            st, _ = _table_for_patch(self._patch_from_vars(self.start_vars), wavetable_type=self._effective_start_type())
            en, _ = _table_for_patch(self._patch_from_vars(self.end_vars), wavetable_type=self._effective_end_type())
            start_canvas = self.start_vars.get("_canvas")
            end_canvas = self.end_vars.get("_canvas")
            if isinstance(start_canvas, tk.Canvas):
                self._draw_table(start_canvas, st, "START")
            if isinstance(end_canvas, tk.Canvas):
                self._draw_table(end_canvas, en, "END")
        except Exception as exc:
            self.status.config(text=f"VISUAL ERROR  {type(exc).__name__}: {exc}")

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function _effective_start_type: small named steps make the signal path,
    # TUTORIAL: UI path, and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def _effective_start_type(self) -> int:
        if bool(self.different_types_var.get()):
            return clamp_int(_safe_int_from_var(self.start_vars["wavetable_type"], 3), 0, WAVETABLE_TYPE_MAX)
        return clamp_int(_safe_int_from_var(self.shared_type_var, 3), 0, WAVETABLE_TYPE_MAX)

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function _effective_end_type: small named steps make the signal path, UI
    # TUTORIAL: path, and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def _effective_end_type(self) -> int:
        if bool(self.different_types_var.get()):
            return clamp_int(_safe_int_from_var(self.end_vars["wavetable_type"], 3), 0, WAVETABLE_TYPE_MAX)
        return clamp_int(_safe_int_from_var(self.shared_type_var, 3), 0, WAVETABLE_TYPE_MAX)

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function _playground_changed: small named steps make the signal path, UI
    # TUTORIAL: path, and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def _playground_changed(self) -> None:
        if self._suppress:
            return
        self._update_all_visualisers()
        if self._active_tab_name() == "Single Engine / Playground":
            self._push_playground_to_audio()

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Morph/patch helper: _morph_changed moves between UI variables, PatchSettings
    # TUTORIAL: objects, generated tables, and rendered audio.
    # TUTORIAL: ------------------------------------------------------------------------
    def _morph_changed(self) -> None:
        if self._suppress:
            return
        try:
            summary = self._summary_from_ui()
            lines = [
                f"Requested: {summary.requested_value:g} {summary.requested_mode.lower()} → "
                f"{summary.sample_count} samples, {_format_seconds_compact(summary.duration_seconds)}, "
                f"{summary.nominal_cycles:.3f} nominal cycles, {summary.samples_per_cycle:.2f} samples/cycle."
            ]
            if summary.warnings:
                lines.append("Warnings: " + "; ".join(summary.warnings))
            if summary.suggestions:
                lines.append("Suggestions: " + "; ".join(summary.suggestions[:3]))
            self.consequence_var.set("\n".join(lines))
        except Exception as exc:
            self.consequence_var.set(f"Duration calculation error: {type(exc).__name__}: {exc}")
        self._update_all_visualisers()

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Audio helper: _push_patch_to_audio bridges UI state and the audio engine. The
    # TUTORIAL: main rule is to keep blocking work out of the real-time callback.
    # TUTORIAL: ------------------------------------------------------------------------
    def _push_patch_to_audio(self, patch: PatchSettings, *, volume: float | None = None, wavetable_type: int | None = None) -> None:
        table, notes = _table_for_patch(patch, wavetable_type=wavetable_type)
        freq = clamp_float(_safe_float_from_var(self.frequency_var, self.default_frequency), MIN_FREQUENCY, MAX_FREQUENCY)
        vol = clamp_float(_safe_float_from_var(self.volume_var, 0.20) if volume is None else volume, 0.0, 1.0)
        fm_range = fm_range_label_to_cents(patch.fm_range_label)
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
        # TUTORIAL: callback may touch it at the same time.
        # TUTORIAL: ------------------------------------------------------------------------
        with self.state.lock:
            self.state.target_table = table
            self.state.target_volume = vol
            self.state.target_frequency = freq
            self.state.target_fm = patch.fm
            self.state.target_pwm = patch.pwm
            self.state.target_am = patch.am
            self.state.target_fm_range_cents = fm_range
            self.state.target_before_start = 0.0
            self.state.target_before_length = WINDOW_STEPS
            self.state.target_after_start = 0.0
            self.state.target_after_length = WINDOW_STEPS
            self.state.reset_requested = True
            sanitise_shared_state(self.state)
        suffix = ""
        if notes:
            suffix = "  Notes: " + "; ".join(sorted(set(notes))[:4])
        self.status.config(text=f"AUDITION  frequency={freq:.3f} Hz volume={vol:.2f}" + suffix)

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Audio helper: _push_playground_to_audio bridges UI state and the audio engine.
    # TUTORIAL: The main rule is to keep blocking work out of the real-time callback.
    # TUTORIAL: ------------------------------------------------------------------------
    def _push_playground_to_audio(self) -> None:
        self._push_patch_to_audio(self._patch_from_vars(self.single_vars))

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Audio helper: _push_morph_endpoint_to_audio bridges UI state and the audio
    # TUTORIAL: engine. The main rule is to keep blocking work out of the real-time callback.
    # TUTORIAL: ------------------------------------------------------------------------
    def _push_morph_endpoint_to_audio(self, endpoint: str) -> None:
        if endpoint == "end":
            patch = self._patch_from_vars(self.end_vars)
            patch.wavetable_type = self._effective_end_type()
            if not bool(self.different_fm_ranges_var.get()):
                patch.fm_range_label = str(self.shared_fm_range_var.get())
        else:
            patch = self._patch_from_vars(self.start_vars)
            patch.wavetable_type = self._effective_start_type()
            patch.fm_range_label = str(self.shared_fm_range_var.get())
        self.notebook.select(self.morph_tab)
        self._push_patch_to_audio(patch, wavetable_type=patch.wavetable_type)

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function _tab_changed: small named steps make the signal path, UI path,
    # TUTORIAL: and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def _tab_changed(self, _event=None) -> None:
        # Only the selected tab can drive the shared live audio state.
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
        # TUTORIAL: callback may touch it at the same time.
        # TUTORIAL: ------------------------------------------------------------------------
        with self.state.lock:
            self.state.target_volume = 0.0
            self.state.reset_requested = True
        if self._active_tab_name() == "Single Engine / Playground":
            self._push_playground_to_audio()
        else:
            self.status.config(text="MORPH TAB ACTIVE  other tab muted; choose Audition START/END or Create WAV")

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function _active_tab_name: small named steps make the signal path, UI
    # TUTORIAL: path, and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def _active_tab_name(self) -> str:
        try:
            return str(self.notebook.tab(self.notebook.select(), "text"))
        except Exception:
            return ""

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Morph/patch helper: _random_patch_into moves between UI variables, PatchSettings
    # TUTORIAL: objects, generated tables, and rendered audio.
    # TUTORIAL: ------------------------------------------------------------------------
    def _random_patch_into(self, vars_dict: dict[str, tk.Variable]) -> None:
        before_start, before_end = _random_window_pair()
        after_start, after_end = _random_window_pair()
        vars_dict["pot1"].set(_random.randint(0, POT_MAX_12BIT))
        vars_dict["pot2"].set(_random.randint(0, POT_MAX_12BIT))
        vars_dict["pot3"].set(_random.randint(0, POT_MAX_12BIT))
        vars_dict["wavetable_type"].set(_random.randint(0, WAVETABLE_TYPE_MAX))
        vars_dict["fm"].set(_random.randint(0, 255))
        vars_dict["pwm"].set(_random.randint(0, 255))
        vars_dict["am"].set(_random.randint(0, 255))
        vars_dict["before_start"].set(before_start)
        vars_dict["before_end"].set(before_end)
        vars_dict["after_start"].set(after_start)
        vars_dict["after_end"].set(after_end)

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function _randomise_single: small named steps make the signal path, UI
    # TUTORIAL: path, and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def _randomise_single(self) -> None:
        self._random_patch_into(self.single_vars)
        self._playground_changed()
        self.status.config(text="RANDOMISE  single-engine patch controls changed")

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Morph/patch helper: _randomise_morph moves between UI variables, PatchSettings
    # TUTORIAL: objects, generated tables, and rendered audio.
    # TUTORIAL: ------------------------------------------------------------------------
    def _randomise_morph(self, which: str) -> None:
        if which in {"start", "both"}:
            self._random_patch_into(self.start_vars)
        if which in {"end", "both"}:
            self._random_patch_into(self.end_vars)
        self._morph_changed()
        self.status.config(text=f"RANDOMISE  {which.upper()} patch controls changed")

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function _reset_single: small named steps make the signal path, UI path,
    # TUTORIAL: and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def _reset_single(self) -> None:
        self._suppress = True
        try:
            defaults = self._new_patch_vars(PatchSettings())
            for key, var in self.single_vars.items():
                if key in defaults and isinstance(var, tk.Variable):
                    var.set(defaults[key].get())
            self.frequency_var.set(self.default_frequency)
            self.volume_var.set(0.20)
        finally:
            self._suppress = False
        self._playground_changed()

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Audio helper: _panic_audio bridges UI state and the audio engine. The main rule
    # TUTORIAL: is to keep blocking work out of the real-time callback.
    # TUTORIAL: ------------------------------------------------------------------------
    def _panic_audio(self) -> None:
        table = fallback_table()
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
        # TUTORIAL: callback may touch it at the same time.
        # TUTORIAL: ------------------------------------------------------------------------
        with self.state.lock:
            self.state.target_table = table
            self.state.target_volume = 0.20
            self.state.target_frequency = self.default_frequency
            self.state.target_fm = 0.0
            self.state.target_pwm = 0.0
            self.state.target_am = 0.0
            self.state.target_fm_range_cents = DEFAULT_FM_RANGE_CENTS
            self.state.target_before_start = 0.0
            self.state.target_before_length = WINDOW_STEPS
            self.state.target_after_start = 0.0
            self.state.target_after_length = WINDOW_STEPS
            self.state.reset_requested = True
            sanitise_shared_state(self.state)
        engine = getattr(self, "audio_engine", None)
        if engine is not None:
            try:
                engine.hard_restart("panic audio", force_fallback_state=True)
            except Exception:
                pass
        self.status.config(text="PANIC AUDIO  fallback square pushed and stream restart requested")

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Morph/patch helper: _copy_patch_vars moves between UI variables, PatchSettings
    # TUTORIAL: objects, generated tables, and rendered audio.
    # TUTORIAL: ------------------------------------------------------------------------
    def _copy_patch_vars(self, source: dict[str, tk.Variable], dest: dict[str, tk.Variable]) -> None:
        for key in ("pot1", "pot2", "pot3", "wavetable_type", "fm", "pwm", "am", "before_start", "before_end", "after_start", "after_end", "fm_range_label"):
            dest[key].set(source[key].get())

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function _copy_start_to_end: small named steps make the signal path, UI
    # TUTORIAL: path, and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def _copy_start_to_end(self) -> None:
        self._copy_patch_vars(self.start_vars, self.end_vars)
        self._morph_changed()

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function _copy_end_to_start: small named steps make the signal path, UI
    # TUTORIAL: path, and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def _copy_end_to_start(self) -> None:
        self._copy_patch_vars(self.end_vars, self.start_vars)
        self._morph_changed()

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function _swap_start_end: small named steps make the signal path, UI
    # TUTORIAL: path, and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def _swap_start_end(self) -> None:
        tmp = {key: self.start_vars[key].get() for key in ("pot1", "pot2", "pot3", "wavetable_type", "fm", "pwm", "am", "before_start", "before_end", "after_start", "after_end", "fm_range_label")}
        self._copy_patch_vars(self.end_vars, self.start_vars)
        for key, value in tmp.items():
            self.end_vars[key].set(value)
        self._morph_changed()

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Visualiser helper: _toggle_visualisers draws waveform previews. Visualiser code
    # TUTORIAL: should be informative, but it should not be the source of truth for rendering.
    # TUTORIAL: ------------------------------------------------------------------------
    def _toggle_visualisers(self) -> None:
        visible = bool(self.visualisers_visible.get())
        for vars_dict in (self.start_vars, self.end_vars):
            canvas = vars_dict.get("_canvas")
            if isinstance(canvas, tk.Canvas):
                if visible:
                    canvas.master.pack(fill="x", padx=6, pady=(6, 0))
                else:
                    canvas.master.pack_forget()
        if visible:
            self.single_visual_parent.pack(side="right", fill="both", padx=8, pady=8)
        else:
            self.single_visual_parent.pack_forget()
        self._update_all_visualisers()

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Export helper: _choose_export_file turns in-memory rendered audio/settings into
    # TUTORIAL: files on disk. This is separate from live playback by design.
    # TUTORIAL: ------------------------------------------------------------------------
    def _choose_export_file(self) -> None:
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: File dialogs are UI operations, so they happen outside the audio callback
        # TUTORIAL: and before/after offline rendering.
        # TUTORIAL: ------------------------------------------------------------------------
        path = _filedialog.asksaveasfilename(
            title="Create WAV",
            defaultextension=".wav",
            initialfile=_os.path.basename(self.filename_var.get() or self._default_export_name()),
            filetypes=(("WAV audio", "*.wav"), ("All files", "*.*")),
        )
        if path:
            self.filename_var.set(path)

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Export helper: _export_paths turns in-memory rendered audio/settings into files
    # TUTORIAL: on disk. This is separate from live playback by design.
    # TUTORIAL: ------------------------------------------------------------------------
    def _export_paths(self, base_path: str, direction: str) -> tuple[str, str]:
        root, ext = _os.path.splitext(base_path)
        if not ext:
            ext = ".wav"
        if direction == "reverse":
            wav_path = f"{root}_REVERSE{ext}"
        else:
            wav_path = f"{root}{ext}"
        json_path = _os.path.splitext(wav_path)[0] + ".json"
        return wav_path, json_path

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Export helper: _create_wav_clicked turns in-memory rendered audio/settings into
    # TUTORIAL: files on disk. This is separate from live playback by design.
    # TUTORIAL: ------------------------------------------------------------------------
    def _create_wav_clicked(self) -> None:
        base_path = self.filename_var.get().strip() or self._default_export_name()
        if not _os.path.isabs(base_path):
            base_path = _os.path.join(_os.getcwd(), base_path)
        summary = self._summary_from_ui()
        start = self._patch_from_vars(self.start_vars)
        end = self._patch_from_vars(self.end_vars)
        start.wavetable_type = self._effective_start_type()
        end.wavetable_type = self._effective_end_type()
        if not bool(self.different_fm_ranges_var.get()):
            start.fm_range_label = str(self.shared_fm_range_var.get())
            end.fm_range_label = str(self.shared_fm_range_var.get())

        directions = ["forward", "reverse"] if str(self.direction_var.get()) == "Both" else (["reverse"] if str(self.direction_var.get()) == "END→START" else ["forward"])
        created: list[str] = []
        try:
            for direction in directions:
                reverse = direction == "reverse"
                audio, notes = _render_morph_audio(
                    start,
                    end,
                    summary=summary,
                    shared_type=None if bool(self.different_types_var.get()) else self._effective_start_type(),
                    different_types=bool(self.different_types_var.get()),
                    different_fm_ranges=bool(self.different_fm_ranges_var.get()),
                    curve_amount=clamp_int(_safe_int_from_var(self.curve_amount_var, 127), 1, 255),
                    curve_mode=str(self.curve_mode_var.get()),
                    cycle_stepped=bool(self.cycle_stepped_var.get()),
                    reverse=reverse,
                    dc_mode=str(self.dc_mode_var.get()),
                    norm_mode=str(self.normalise_mode_var.get()),
                    headroom_db=_safe_float_from_var(self.headroom_var, MORPH_HEADROOM_DEFAULT_DB),
                )
                wav_path, json_path = self._export_paths(base_path, direction)
                _os.makedirs(_os.path.dirname(wav_path) or ".", exist_ok=True)
                _write_wav_mono(wav_path, audio, summary.sample_rate, summary.bit_depth)
                metadata = {
                    "app": "Fractal Wavetable START-END Morph Export",
                    "created_epoch": int(_time.time()),
                    "direction": "END_TO_START" if reverse else "START_TO_END",
                    "summary": _asdict(summary),
                    "start_patch": _asdict(start),
                    "end_patch": _asdict(end),
                    "transition": {
                        "curve_mode": str(self.curve_mode_var.get()),
                        "curve_amount": clamp_int(_safe_int_from_var(self.curve_amount_var, 127), 1, 255),
                        "cycle_stepped": bool(self.cycle_stepped_var.get()),
                        "different_wavetable_types": bool(self.different_types_var.get()),
                        "different_fm_ranges": bool(self.different_fm_ranges_var.get()),
                    },
                    "output_processing": {
                        "dc_removal": str(self.dc_mode_var.get()),
                        "normalise": str(self.normalise_mode_var.get()),
                        "headroom_db": _safe_float_from_var(self.headroom_var, MORPH_HEADROOM_DEFAULT_DB),
                    },
                    "render_notes": notes,
                }
                with open(json_path, "w", encoding="utf-8") as f:
                    # TUTORIAL: ------------------------------------------------------------------------
                    # TUTORIAL: JSON sidecars are written beside WAV files so settings can be
                    # TUTORIAL: inspected, archived, and later imported.
                    # TUTORIAL: ------------------------------------------------------------------------
                    _json.dump(metadata, f, indent=2, sort_keys=True)
                created.extend([wav_path, json_path])
            self.status.config(text="EXPORT COMPLETE  " + "  ".join(_os.path.basename(x) for x in created))
            self.filename_var.set(self._default_export_name())
        except Exception as exc:
            self.status.config(text=f"EXPORT ERROR  {type(exc).__name__}: {exc}")

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Audio helper: _audio_health_tick bridges UI state and the audio engine. The main
    # TUTORIAL: rule is to keep blocking work out of the real-time callback.
    # TUTORIAL: ------------------------------------------------------------------------
    def _audio_health_tick(self) -> None:
        try:
            engine = getattr(self, "audio_engine", None)
            if engine is not None:
                engine.check_health()
        except Exception as exc:
            self.status.config(text=f"AUDIO HEALTH CHECK ERROR  {type(exc).__name__}: {exc}")
        finally:
            try:
                # TUTORIAL: ------------------------------------------------------------------------
                # TUTORIAL: Tk uses scheduled callbacks instead of long blocking loops.
                # TUTORIAL: after(...) lets the UI stay responsive.
                # TUTORIAL: ------------------------------------------------------------------------
                self.root.after(1000, self._audio_health_tick)
            except Exception:
                pass



# -----------------------------------------------------------------------------
# Tabbed UI/audio corrections: transport, morph audition buffers, dropdowns,
# restored visualiser modes, note controls, expanded WAV export choices.
# -----------------------------------------------------------------------------

EXPORT_SAMPLE_RATES = (44100, 48000, 96000, 192000)
EXPORT_BIT_DEPTHS = (
    "8-bit PCM",
    "12-bit PCM (16-bit container)",
    "16-bit PCM",
    "24-bit PCM",
    "32-bit PCM",
)
VISUALIZER_MODE_OPTIONS = (
    "Base table",
    "Phase-locked rendered FM/PWM/AM",
    "Animated stable FM/PWM/AM",
)
WT_OPTION_LIST = [wavetable_type_option(i) for i in sorted(WAVETABLE_MODE_INFO)] if "WAVETABLE_MODE_INFO" in globals() else [str(i) for i in range(WAVETABLE_TYPE_MAX + 1)]


_previous_audio_callback_for_tabbed_preview_buffer = WavetableOscillator.callback


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Audio helper: _preview_buffer_audio_callback bridges UI state and the audio engine.
# TUTORIAL: The main rule is to keep blocking work out of the real-time callback.
# TUTORIAL: ------------------------------------------------------------------------
def _preview_buffer_audio_callback(self, outdata, frames, time, status):
    """Final audio wrapper: play a pre-rendered audition buffer when present."""
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
    # TUTORIAL: callback may touch it at the same time.
    # TUTORIAL: ------------------------------------------------------------------------
    with self.state.lock:
        buffer = getattr(self.state, "target_preview_buffer", None)
        pos = int(getattr(self.state, "target_preview_buffer_pos", 0) or 0)
        active = bool(getattr(self.state, "target_preview_buffer_active", False))
        gate = clamp_float(getattr(self.state, "target_audio_gate", TRANSPORT_DEFAULT_GATE), 0.0, 1.0)

    if active and buffer is not None:
        try:
            y = np.asarray(buffer, dtype=np.float32).reshape(-1)
            outdata.fill(0.0)
            if gate <= 0.0 or pos >= y.size:
                # TUTORIAL: ------------------------------------------------------------------------
                # TUTORIAL: SharedState is read or written under a lock because the GUI thread
                # TUTORIAL: and audio callback may touch it at the same time.
                # TUTORIAL: ------------------------------------------------------------------------
                with self.state.lock:
                    self.state.target_preview_buffer_active = False
                    self.state.target_preview_buffer = None
                    self.state.target_preview_buffer_pos = 0
                    self.state.target_transport_mode = "stopped"
                return
            end = min(pos + int(frames), y.size)
            chunk = y[pos:end]
            outdata[:chunk.size, 0] = chunk
            if outdata.shape[1] > 1:
                outdata[:chunk.size, 1] = chunk
            # TUTORIAL: ------------------------------------------------------------------------
            # TUTORIAL: SharedState is read or written under a lock because the GUI thread and
            # TUTORIAL: audio callback may touch it at the same time.
            # TUTORIAL: ------------------------------------------------------------------------
            with self.state.lock:
                if end >= y.size:
                    self.state.target_preview_buffer_active = False
                    self.state.target_preview_buffer = None
                    self.state.target_preview_buffer_pos = 0
                    self.state.target_audio_gate = 0.0
                    self.state.target_transport_mode = "stopped"
                    self.state.audio_watchdog_message = "Morph audition ended"
                else:
                    self.state.target_preview_buffer_pos = end
            return
        except Exception:
            # TUTORIAL: ------------------------------------------------------------------------
            # TUTORIAL: SharedState is read or written under a lock because the GUI thread and
            # TUTORIAL: audio callback may touch it at the same time.
            # TUTORIAL: ------------------------------------------------------------------------
            with self.state.lock:
                self.state.target_preview_buffer_active = False
                self.state.target_preview_buffer = None
                self.state.target_preview_buffer_pos = 0
            try:
                outdata.fill(0.0)
            except Exception:
                pass
            return

    return _previous_audio_callback_for_tabbed_preview_buffer(self, outdata, frames, time, status)


WavetableOscillator.callback = _preview_buffer_audio_callback


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Writes the rendered mono audio buffer to a WAV file. The code handles unusual
# TUTORIAL: requested depths such as 8-bit and 12-bit by mapping them into WAV-compatible
# TUTORIAL: containers.
# TUTORIAL: ------------------------------------------------------------------------
def _write_wav_mono(path: str, audio: np.ndarray, sample_rate: int, bit_depth: str) -> None:  # type: ignore[override]
    """Write mono PCM WAV, including 8-bit and quantised 12-bit export."""
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Clip the signal/control range after math that might overshoot. This keeps audio
    # TUTORIAL: buffers and tables bounded.
    # TUTORIAL: ------------------------------------------------------------------------
    y = np.clip(np.asarray(audio, dtype=np.float64), -1.0, 1.0)
    bit_depth = str(bit_depth)
    with _wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setframerate(int(sample_rate))
        if bit_depth.startswith("8"):
            # Standard PCM WAV 8-bit is unsigned offset binary.
            wf.setsampwidth(1)
            # TUTORIAL: ------------------------------------------------------------------------
            # TUTORIAL: Clip the signal/control range after math that might overshoot. This
            # TUTORIAL: keeps audio buffers and tables bounded.
            # TUTORIAL: ------------------------------------------------------------------------
            data = np.clip(np.round((y * 127.0) + 128.0), 0, 255).astype(np.uint8).tobytes()
        elif bit_depth.startswith("12"):
            # WAV has no normal 12-bit PCM container here, so export a 16-bit WAV
            # quantised to 12-bit levels. The JSON sidecar records the requested label.
            wf.setsampwidth(2)
            q = np.round(y * 2047.0) / 2047.0
            # TUTORIAL: ------------------------------------------------------------------------
            # TUTORIAL: Clip the signal/control range after math that might overshoot. This
            # TUTORIAL: keeps audio buffers and tables bounded.
            # TUTORIAL: ------------------------------------------------------------------------
            data = np.clip(np.round(q * 32767.0), -32768, 32767).astype("<i2").tobytes()
        elif bit_depth.startswith("16"):
            wf.setsampwidth(2)
            # TUTORIAL: ------------------------------------------------------------------------
            # TUTORIAL: Clip the signal/control range after math that might overshoot. This
            # TUTORIAL: keeps audio buffers and tables bounded.
            # TUTORIAL: ------------------------------------------------------------------------
            data = np.clip(np.round(y * 32767.0), -32768, 32767).astype("<i2").tobytes()
        elif bit_depth.startswith("24"):
            wf.setsampwidth(3)
            # TUTORIAL: ------------------------------------------------------------------------
            # TUTORIAL: Clip the signal/control range after math that might overshoot. This
            # TUTORIAL: keeps audio buffers and tables bounded.
            # TUTORIAL: ------------------------------------------------------------------------
            vals = np.clip(np.round(y * 8388607.0), -8388608, 8388607).astype(np.int32)
            b = bytearray()
            for v in vals:
                iv = int(v)
                if iv < 0:
                    iv += 1 << 24
                b.extend((iv & 0xFF, (iv >> 8) & 0xFF, (iv >> 16) & 0xFF))
            data = bytes(b)
        else:
            wf.setsampwidth(4)
            # TUTORIAL: ------------------------------------------------------------------------
            # TUTORIAL: Clip the signal/control range after math that might overshoot. This
            # TUTORIAL: keeps audio buffers and tables bounded.
            # TUTORIAL: ------------------------------------------------------------------------
            data = np.clip(np.round(y * 2147483647.0), -2147483648, 2147483647).astype("<i4").tobytes()
        wf.writeframes(data)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Initial tabbed-UI helper: _tabbed_ensure_common_vars is part of the first START/END
# TUTORIAL: tab implementation. Later v3-v7 helpers may replace parts of this behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _tabbed_ensure_common_vars(self) -> None:
    if not hasattr(self, "frequency_var"):
        self.frequency_var = tk.DoubleVar(value=self.default_frequency)
    if not hasattr(self, "volume_var"):
        self.volume_var = tk.DoubleVar(value=0.20)
    if not hasattr(self, "note_var"):
        note, octave = nearest_note_for_frequency(self.default_frequency)
        self.note_var = tk.StringVar(value=note)
        self.octave_var = tk.StringVar(value=str(octave))
        self.note_frequency_label_var = tk.StringVar(value="")
    if not hasattr(self, "visualizer_mode"):
        self.visualizer_mode = tk.StringVar(value="Base table")
        self.visual_anim_phase = 0.0
        self.visual_anim_after_id = None
    self._tabbed_update_note_label()


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Initial tabbed-UI helper: _tabbed_update_note_label is part of the first START/END
# TUTORIAL: tab implementation. Later v3-v7 helpers may replace parts of this behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _tabbed_update_note_label(self) -> None:
    try:
        freq = clamp_float(_safe_float_from_var(self.frequency_var, self.default_frequency), MIN_FREQUENCY, MAX_FREQUENCY)
        note, octave = nearest_note_for_frequency(freq)
        if hasattr(self, "note_frequency_label_var"):
            self.note_frequency_label_var.set(f"nearest: {note}{octave}  ({freq:.3f} Hz)")
    except Exception:
        pass


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Initial tabbed-UI helper: _tabbed_frequency_committed is part of the first START/END
# TUTORIAL: tab implementation. Later v3-v7 helpers may replace parts of this behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _tabbed_frequency_committed(self, _event=None) -> None:
    self._tabbed_ensure_common_vars()
    freq = clamp_float(_safe_float_from_var(self.frequency_var, self.default_frequency), MIN_FREQUENCY, MAX_FREQUENCY)
    self.frequency_var.set(round(freq, 6))
    note, octave = nearest_note_for_frequency(freq)
    try:
        self.note_var.set(note)
        self.octave_var.set(str(octave))
    except Exception:
        pass
    self._tabbed_update_note_label()
    self._morph_changed()
    if self._active_tab_name() == "Single Engine / Playground":
        self._push_playground_to_audio()


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Initial tabbed-UI helper: _tabbed_note_changed is part of the first START/END tab
# TUTORIAL: implementation. Later v3-v7 helpers may replace parts of this behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _tabbed_note_changed(self, _value=None) -> None:
    self._tabbed_ensure_common_vars()
    try:
        freq = note_to_frequency(str(self.note_var.get()), int(str(self.octave_var.get())))
    except Exception:
        freq = self.default_frequency
    freq = clamp_float(freq, MIN_FREQUENCY, MAX_FREQUENCY)
    self.frequency_var.set(round(freq, 6))
    self._tabbed_update_note_label()
    self._morph_changed()
    if self._active_tab_name() == "Single Engine / Playground":
        self._push_playground_to_audio()


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Initial tabbed-UI helper: _tabbed_add_frequency_note_row is part of the first
# TUTORIAL: START/END tab implementation. Later v3-v7 helpers may replace parts of this
# TUTORIAL: behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _tabbed_add_frequency_note_row(self, parent, *, command) -> None:
    self._tabbed_ensure_common_vars()
    row = tk.Frame(parent)
    row.pack(fill="x", padx=8, pady=(8, 2))
    tk.Label(row, text="Frequency", width=15, anchor="w").pack(side="left")
    freq = tk.Entry(row, textvariable=self.frequency_var, width=12, justify="right")
    freq.pack(side="left")
    freq.bind("<Return>", self._tabbed_frequency_committed)
    freq.bind("<FocusOut>", self._tabbed_frequency_committed)
    tk.Label(row, text="Hz").pack(side="left", padx=(4, 10))
    tk.Label(row, text="Note").pack(side="left")
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: OptionMenu is used for compact dropdowns such as wavetable type, pitch range,
    # TUTORIAL: and export settings.
    # TUTORIAL: ------------------------------------------------------------------------
    note_menu = tk.OptionMenu(row, self.note_var, *NOTE_NAMES, command=self._tabbed_note_changed)
    note_menu.config(width=4)
    note_menu.pack(side="left", padx=(4, 0))
    tk.Label(row, text="Octave").pack(side="left", padx=(10, 4))
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: OptionMenu is used for compact dropdowns such as wavetable type, pitch range,
    # TUTORIAL: and export settings.
    # TUTORIAL: ------------------------------------------------------------------------
    octave_menu = tk.OptionMenu(row, self.octave_var, *[str(i) for i in range(0, 11)], command=self._tabbed_note_changed)
    octave_menu.config(width=4)
    octave_menu.pack(side="left")
    tk.Label(row, textvariable=self.note_frequency_label_var, anchor="w").pack(side="left", padx=(12, 0), fill="x", expand=True)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Initial tabbed-UI helper: _tabbed_add_transport_row is part of the first START/END
# TUTORIAL: tab implementation. Later v3-v7 helpers may replace parts of this behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _tabbed_add_transport_row(self, parent, *, label_width: int = 15) -> None:
    row = tk.Frame(parent)
    row.pack(fill="x", padx=8, pady=(2, 6))
    tk.Label(row, text="Transport", width=label_width, anchor="w").pack(side="left")
    tk.Button(row, text="Drone", command=self.drone_audio).pack(side="left")
    tk.Button(row, text="Stop", command=self.stop_audio).pack(side="left", padx=(8, 0))


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Initial tabbed-UI helper: _tabbed_set_transport_state is part of the first START/END
# TUTORIAL: tab implementation. Later v3-v7 helpers may replace parts of this behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _tabbed_set_transport_state(self, mode: str) -> None:
    mode = str(mode)
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
    # TUTORIAL: callback may touch it at the same time.
    # TUTORIAL: ------------------------------------------------------------------------
    with self.state.lock:
        sanitise_shared_state(self.state)
        if mode == "stop":
            self.state.target_audio_gate = 0.0
            self.state.target_play_remaining_samples = 0
            self.state.target_transport_mode = "stopped"
            self.state.target_preview_buffer_active = False
            self.state.target_preview_buffer = None
            self.state.target_preview_buffer_pos = 0
        else:
            self.state.target_audio_gate = 1.0
            self.state.target_play_remaining_samples = None
            self.state.target_transport_mode = "drone"
            self.state.reset_requested = True


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Initial tabbed-UI helper: _tabbed_drone_audio is part of the first START/END tab
# TUTORIAL: implementation. Later v3-v7 helpers may replace parts of this behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _tabbed_drone_audio(self) -> None:
    self._set_transport_state("drone")
    self.status.config(text="DRONE  continuous audio gate open")


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Initial tabbed-UI helper: _tabbed_stop_audio is part of the first START/END tab
# TUTORIAL: implementation. Later v3-v7 helpers may replace parts of this behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _tabbed_stop_audio(self) -> None:
    self._set_transport_state("stop")
    self.status.config(text="STOP  audio gated off; stream remains alive")


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Initial tabbed-UI helper: _tabbed_wt_dropdown is part of the first START/END tab
# TUTORIAL: implementation. Later v3-v7 helpers may replace parts of this behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _tabbed_wt_dropdown(self, row, int_var: tk.Variable, command, *, width: int = 46):
    option_var = tk.StringVar(value=wavetable_type_option(_safe_int_from_var(int_var, 0)))
    combo = _ttk.Combobox(row, textvariable=option_var, values=WT_OPTION_LIST, state="readonly", width=width)
    combo.pack(side="left", fill="x", expand=True)

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function changed: small named steps make the signal path, UI path, and
    # TUTORIAL: export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def changed(_event=None):
        int_var.set(wavetable_type_from_option(option_var.get()))
        command()

    combo.bind("<<ComboboxSelected>>", changed)
    return combo, option_var


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Initial tabbed-UI helper: _tabbed_add_patch_controls is part of the first START/END
# TUTORIAL: tab implementation. Later v3-v7 helpers may replace parts of this behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _tabbed_add_patch_controls(self, parent, vars_dict: dict[str, tk.Variable], *, command, include_fm_range: bool = False, endpoint_panel: bool = False):
    self._add_slider(parent, "Core Seed", vars_dict["pot1"], 0, POT_MAX_12BIT, 1, command)
    self._add_slider(parent, "3-Step Growth", vars_dict["pot2"], 0, POT_MAX_12BIT, 1, command)
    self._add_slider(parent, "9-Step Growth", vars_dict["pot3"], 0, POT_MAX_12BIT, 1, command)

    row = tk.Frame(parent)
    row.pack(fill="x", padx=8, pady=2)
    tk.Label(row, text="Wavetable Type", width=15, anchor="w").pack(side="left")
    combo, option_var = self._tabbed_wt_dropdown(row, vars_dict["wavetable_type"], command)
    vars_dict["_type_combo"] = combo  # type: ignore[assignment]
    vars_dict["_type_option_var"] = option_var  # type: ignore[assignment]

    desc = tk.Label(parent, anchor="w", text="", justify="left", wraplength=720)
    desc.pack(fill="x", padx=8, pady=(0, 2))
    vars_dict["_type_desc"] = desc  # type: ignore[assignment]

    self._add_slider(parent, "FM", vars_dict["fm"], 0, 255, 1, command)
    self._add_slider(parent, "PWM", vars_dict["pwm"], 0, 255, 1, command)
    self._add_slider(parent, "AM", vars_dict["am"], 0, 255, 1, command)

    if include_fm_range:
        row = tk.Frame(parent)
        row.pack(fill="x", padx=8, pady=2)
        tk.Label(row, text="FM Range", width=15, anchor="w").pack(side="left")
        fm_combo = _ttk.Combobox(row, textvariable=vars_dict["fm_range_label"], values=[label for label, _c in FM_RANGE_OPTIONS], state="readonly", width=26)
        fm_combo.pack(side="left")
        fm_combo.bind("<<ComboboxSelected>>", lambda _e: command())
        vars_dict["_fm_range_combo"] = fm_combo  # type: ignore[assignment]

    for label, key in (
        ("Source Start", "before_start"),
        ("Source End", "before_end"),
        ("Result Start", "after_start"),
        ("Result End", "after_end"),
    ):
        self._add_slider(parent, label, vars_dict[key], 0, int(WINDOW_STEPS), 1, command)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Initial tabbed-UI helper: _tabbed_add_visual_mode_controls is part of the first
# TUTORIAL: START/END tab implementation. Later v3-v7 helpers may replace parts of this
# TUTORIAL: behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _tabbed_add_visual_mode_controls(self, parent) -> None:
    self._tabbed_ensure_common_vars()
    row = tk.Frame(parent)
    row.pack(fill="x", padx=6, pady=(4, 2))
    tk.Label(row, text="Visualiser").pack(side="left")
    for text in VISUALIZER_MODE_OPTIONS:
        tk.Radiobutton(row, text=text, variable=self.visualizer_mode, value=text, command=self._visual_mode_changed).pack(side="left", padx=(8, 0))


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Initial tabbed-UI helper: _tabbed_visual_mode_changed is part of the first START/END
# TUTORIAL: tab implementation. Later v3-v7 helpers may replace parts of this behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _tabbed_visual_mode_changed(self) -> None:
    self._update_all_visualisers()
    self._schedule_visual_animation_if_needed()


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Initial tabbed-UI helper: _tabbed_build_playground_tab is part of the first
# TUTORIAL: START/END tab implementation. Later v3-v7 helpers may replace parts of this
# TUTORIAL: behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _tabbed_build_playground_tab(self) -> None:
    self._tabbed_ensure_common_vars()
    outer = tk.Frame(self.playground_tab)
    outer.pack(fill="both", expand=True)
    controls = tk.Frame(outer)
    controls.pack(side="left", fill="both", expand=True, padx=8, pady=8)
    visual = tk.Frame(outer, width=620)
    visual.pack(side="right", fill="both", padx=8, pady=8)
    visual.pack_propagate(False)
    self.single_visual_parent = visual

    top = tk.Frame(controls)
    top.pack(fill="x", pady=(0, 6))
    tk.Button(top, text="Randomise", command=self._randomise_single).pack(side="left")
    tk.Button(top, text="Reset", command=self._reset_single).pack(side="left", padx=(8, 0))
    tk.Button(top, text="Panic audio", command=self._panic_audio).pack(side="left", padx=(8, 0))

    self._add_patch_controls(controls, self.single_vars, command=self._playground_changed, include_fm_range=True)
    self._add_shared_live_controls(controls, command=self._playground_changed)
    self._add_transport_row(controls)

    tk.Label(visual, text="Playground waveform", anchor="center").pack(fill="x")
    self._add_visual_mode_controls(visual)
    self.single_canvas = tk.Canvas(visual, width=560, height=420, bg="black", highlightthickness=1, highlightbackground="#555555")
    self.single_canvas.pack(fill="both", expand=True, pady=(6, 0))


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Initial tabbed-UI helper: _tabbed_add_shared_live_controls is part of the first
# TUTORIAL: START/END tab implementation. Later v3-v7 helpers may replace parts of this
# TUTORIAL: behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _tabbed_add_shared_live_controls(self, parent, *, command) -> None:
    self._tabbed_ensure_common_vars()
    self._add_frequency_note_row(parent, command=command)
    self._add_slider(parent, "Volume", self.volume_var, 0.0, 1.0, 0.01, command)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Initial tabbed-UI helper: _tabbed_build_endpoint_panel is part of the first
# TUTORIAL: START/END tab implementation. Later v3-v7 helpers may replace parts of this
# TUTORIAL: behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _tabbed_build_endpoint_panel(self, parent, title: str, vars_dict: dict[str, tk.Variable]):
    panel = tk.LabelFrame(parent, text=title)
    visual_frame = tk.Frame(panel)
    visual_frame.pack(fill="x", padx=6, pady=(6, 0))
    canvas = tk.Canvas(visual_frame, width=420, height=190, bg="black", highlightthickness=1, highlightbackground="#555555")
    canvas.pack(fill="x", expand=True)
    vars_dict["_canvas"] = canvas  # type: ignore[assignment]
    self._add_patch_controls(panel, vars_dict, command=self._morph_changed, include_fm_range=True, endpoint_panel=True)
    return panel


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Initial tabbed-UI helper: _tabbed_build_morph_shared_controls is part of the first
# TUTORIAL: START/END tab implementation. Later v3-v7 helpers may replace parts of this
# TUTORIAL: behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _tabbed_build_morph_shared_controls(self, parent) -> None:
    self._tabbed_ensure_common_vars()
    self.shared_type_var = tk.IntVar(value=3)
    self.shared_type_option_var = tk.StringVar(value=wavetable_type_option(3))
    self.different_types_var = tk.BooleanVar(value=False)
    self.shared_fm_range_var = tk.StringVar(value=DEFAULT_FM_RANGE_LABEL)
    self.different_fm_ranges_var = tk.BooleanVar(value=False)
    self.duration_mode_var = tk.StringVar(value="Cycles")
    self.duration_value_var = tk.StringVar(value=str(MORPH_DEFAULT_CYCLES))
    self.sample_rate_var = tk.IntVar(value=self.sample_rate if int(self.sample_rate) in EXPORT_SAMPLE_RATES else DEFAULT_SAMPLE_RATE)
    self.bit_depth_var = tk.StringVar(value="24-bit PCM")
    self.curve_mode_var = tk.StringVar(value="Bend")
    self.curve_amount_var = tk.IntVar(value=127)
    self.cycle_stepped_var = tk.BooleanVar(value=True)
    self.direction_var = tk.StringVar(value="START→END")
    self.dc_mode_var = tk.StringVar(value="Per cycle")
    self.normalise_mode_var = tk.StringVar(value="Per cycle peak")
    self.headroom_var = tk.DoubleVar(value=MORPH_HEADROOM_DEFAULT_DB)
    self.filename_var = tk.StringVar(value=self._default_export_name())
    self.consequence_var = tk.StringVar(value="")

    line0 = tk.Frame(parent); line0.pack(fill="x", padx=8, pady=3)
    self._add_frequency_note_row(line0, command=self._morph_changed)
    # _add_frequency_note_row packs as a full row; line0 is just a container.

    line1 = tk.Frame(parent); line1.pack(fill="x", padx=8, pady=3)
    tk.Checkbutton(line1, text="Different START/END wavetable types", variable=self.different_types_var, command=self._morph_changed).pack(side="left")
    tk.Label(line1, text="Shared type").pack(side="left", padx=(12, 4))
    shared_combo = _ttk.Combobox(line1, textvariable=self.shared_type_option_var, values=WT_OPTION_LIST, state="readonly", width=58)
    shared_combo.pack(side="left", fill="x", expand=True)

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function shared_type_changed: small named steps make the signal path, UI
    # TUTORIAL: path, and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def shared_type_changed(_event=None):
        self.shared_type_var.set(wavetable_type_from_option(self.shared_type_option_var.get()))
        self._morph_changed()

    shared_combo.bind("<<ComboboxSelected>>", shared_type_changed)
    self.shared_type_combo = shared_combo

    line1b = tk.Frame(parent); line1b.pack(fill="x", padx=8, pady=3)
    tk.Label(line1b, text="Shared FM Range").pack(side="left")
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: OptionMenu is used for compact dropdowns such as wavetable type, pitch range,
    # TUTORIAL: and export settings.
    # TUTORIAL: ------------------------------------------------------------------------
    tk.OptionMenu(line1b, self.shared_fm_range_var, *[label for label, _c in FM_RANGE_OPTIONS], command=lambda _v: self._morph_changed()).pack(side="left", padx=(6, 0))
    tk.Checkbutton(line1b, text="Different START/END FM ranges", variable=self.different_fm_ranges_var, command=self._morph_changed).pack(side="left", padx=(12, 0))

    line2 = tk.Frame(parent); line2.pack(fill="x", padx=8, pady=3)
    tk.Label(line2, text="Duration").pack(side="left")
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: OptionMenu is used for compact dropdowns such as wavetable type, pitch range,
    # TUTORIAL: and export settings.
    # TUTORIAL: ------------------------------------------------------------------------
    tk.OptionMenu(line2, self.duration_mode_var, "Seconds", "Cycles", "Samples", command=lambda _v: self._morph_changed()).pack(side="left", padx=(6, 0))
    entry = tk.Entry(line2, textvariable=self.duration_value_var, width=10, justify="right")
    entry.pack(side="left", padx=(6, 0))
    entry.bind("<Return>", lambda _e: self._morph_changed())
    entry.bind("<FocusOut>", lambda _e: self._morph_changed())
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: OptionMenu is used for compact dropdowns such as wavetable type, pitch range,
    # TUTORIAL: and export settings.
    # TUTORIAL: ------------------------------------------------------------------------
    tk.OptionMenu(line2, self.direction_var, "START→END", "END→START", "Both", command=lambda _v: self._morph_changed()).pack(side="left", padx=(14, 0))
    tk.Checkbutton(line2, text="Cycle-stepped transition", variable=self.cycle_stepped_var, command=self._morph_changed).pack(side="left", padx=(14, 0))
    tk.Label(line2, text="Curve").pack(side="left", padx=(14, 4))
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: OptionMenu is used for compact dropdowns such as wavetable type, pitch range,
    # TUTORIAL: and export settings.
    # TUTORIAL: ------------------------------------------------------------------------
    tk.OptionMenu(line2, self.curve_mode_var, "Bend", "S-curve", command=lambda _v: self._morph_changed()).pack(side="left")
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Tk Scale is the slider widget. In this app, slider callbacks push new values
    # TUTORIAL: into patch or audio state.
    # TUTORIAL: ------------------------------------------------------------------------
    tk.Scale(line2, variable=self.curve_amount_var, from_=1, to=255, resolution=1, orient="horizontal", length=180, command=lambda _v: self._morph_changed()).pack(side="left", padx=(6, 0))

    line3 = tk.Frame(parent); line3.pack(fill="x", padx=8, pady=3)
    tk.Label(line3, text="Sample rate").pack(side="left")
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: OptionMenu is used for compact dropdowns such as wavetable type, pitch range,
    # TUTORIAL: and export settings.
    # TUTORIAL: ------------------------------------------------------------------------
    tk.OptionMenu(line3, self.sample_rate_var, *EXPORT_SAMPLE_RATES, command=lambda _v: self._morph_changed()).pack(side="left", padx=(6, 0))
    tk.Label(line3, text="Bit depth").pack(side="left", padx=(14, 4))
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: OptionMenu is used for compact dropdowns such as wavetable type, pitch range,
    # TUTORIAL: and export settings.
    # TUTORIAL: ------------------------------------------------------------------------
    tk.OptionMenu(line3, self.bit_depth_var, *EXPORT_BIT_DEPTHS, command=lambda _v: self._morph_changed()).pack(side="left")
    tk.Label(line3, text="DC removal").pack(side="left", padx=(14, 4))
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: OptionMenu is used for compact dropdowns such as wavetable type, pitch range,
    # TUTORIAL: and export settings.
    # TUTORIAL: ------------------------------------------------------------------------
    tk.OptionMenu(line3, self.dc_mode_var, "Off", "Whole file", "Per cycle", command=lambda _v: self._morph_changed()).pack(side="left")
    tk.Label(line3, text="Normalise").pack(side="left", padx=(14, 4))
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: OptionMenu is used for compact dropdowns such as wavetable type, pitch range,
    # TUTORIAL: and export settings.
    # TUTORIAL: ------------------------------------------------------------------------
    tk.OptionMenu(line3, self.normalise_mode_var, "Off", "Whole file peak", "Per cycle peak", command=lambda _v: self._morph_changed()).pack(side="left")
    tk.Label(line3, text="Headroom dB").pack(side="left", padx=(14, 4))
    tk.Entry(line3, textvariable=self.headroom_var, width=7, justify="right").pack(side="left")

    line4 = tk.Frame(parent); line4.pack(fill="x", padx=8, pady=3)
    tk.Label(line4, text="Filename").pack(side="left")
    tk.Entry(line4, textvariable=self.filename_var, width=52).pack(side="left", padx=(6, 0), fill="x", expand=True)
    tk.Button(line4, text="Choose…", command=self._choose_export_file).pack(side="left", padx=(6, 0))

    line5 = tk.Frame(parent); line5.pack(fill="x", padx=8, pady=3)
    tk.Button(line5, text="Audition START", command=lambda: self._push_morph_endpoint_to_audio("start")).pack(side="left")
    tk.Button(line5, text="Audition END", command=lambda: self._push_morph_endpoint_to_audio("end")).pack(side="left", padx=(6, 0))
    tk.Button(line5, text="Audition START→END", command=lambda: self._audition_morph_transition(False)).pack(side="left", padx=(12, 0))
    tk.Button(line5, text="Audition END→START", command=lambda: self._audition_morph_transition(True)).pack(side="left", padx=(6, 0))
    tk.Button(line5, text="Drone", command=self.drone_audio).pack(side="left", padx=(18, 0))
    tk.Button(line5, text="Stop", command=self.stop_audio).pack(side="left", padx=(6, 0))
    tk.Button(line5, text="Create WAV + JSON", command=self._create_wav_clicked).pack(side="right")

    tk.Label(parent, textvariable=self.consequence_var, anchor="w", justify="left", wraplength=1600).pack(fill="x", padx=8, pady=(2, 6))
    self._morph_changed()


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Initial tabbed-UI helper: _tabbed_update_type_descriptions is part of the first
# TUTORIAL: START/END tab implementation. Later v3-v7 helpers may replace parts of this
# TUTORIAL: behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _tabbed_update_type_descriptions(self) -> None:
    for vars_dict in (self.single_vars, self.start_vars, self.end_vars):
        wt = clamp_int(_safe_int_from_var(vars_dict["wavetable_type"], 3), 0, WAVETABLE_TYPE_MAX)
        opt = wavetable_type_option(wt)
        opt_var = vars_dict.get("_type_option_var")
        if isinstance(opt_var, tk.StringVar) and opt_var.get() != opt:
            opt_var.set(opt)
        label = vars_dict.get("_type_desc")
        if isinstance(label, tk.Label):
            name, desc = WAVETABLE_MODE_INFO.get(wt, ("Unknown", "")) if "WAVETABLE_MODE_INFO" in globals() else (str(wt), "")
            label.config(text=f"{wt} - {name}: {desc}")
    if hasattr(self, "shared_type_option_var"):
        wt = clamp_int(_safe_int_from_var(self.shared_type_var, 3), 0, WAVETABLE_TYPE_MAX)
        opt = wavetable_type_option(wt)
        if self.shared_type_option_var.get() != opt:
            self.shared_type_option_var.set(opt)
    self._refresh_endpoint_unlocks()


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Initial tabbed-UI helper: _tabbed_refresh_endpoint_unlocks is part of the first
# TUTORIAL: START/END tab implementation. Later v3-v7 helpers may replace parts of this
# TUTORIAL: behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _tabbed_refresh_endpoint_unlocks(self) -> None:
    different_types = bool(getattr(self, "different_types_var", tk.BooleanVar(value=True)).get()) if hasattr(self, "different_types_var") else True
    different_fm = bool(getattr(self, "different_fm_ranges_var", tk.BooleanVar(value=True)).get()) if hasattr(self, "different_fm_ranges_var") else True
    if not different_types and hasattr(self, "shared_type_var"):
        shared = clamp_int(_safe_int_from_var(self.shared_type_var, 3), 0, WAVETABLE_TYPE_MAX)
        # START and END display the shared value while locked.
        for vars_dict in (getattr(self, "start_vars", {}), getattr(self, "end_vars", {})):
            try:
                vars_dict["wavetable_type"].set(shared)
            except Exception:
                pass
    if not different_fm and hasattr(self, "shared_fm_range_var"):
        shared_fm = str(self.shared_fm_range_var.get())
        for vars_dict in (getattr(self, "start_vars", {}), getattr(self, "end_vars", {})):
            try:
                vars_dict["fm_range_label"].set(shared_fm)
            except Exception:
                pass
    for vars_dict in (getattr(self, "start_vars", {}), getattr(self, "end_vars", {})):
        combo = vars_dict.get("_type_combo") if isinstance(vars_dict, dict) else None
        if combo is not None:
            try:
                combo.configure(state="readonly" if different_types else "disabled")
            except Exception:
                pass
        fm_combo = vars_dict.get("_fm_range_combo") if isinstance(vars_dict, dict) else None
        if fm_combo is not None:
            try:
                fm_combo.configure(state="readonly" if different_fm else "disabled")
            except Exception:
                pass


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Initial tabbed-UI helper: _tabbed_draw_table is part of the first START/END tab
# TUTORIAL: implementation. Later v3-v7 helpers may replace parts of this behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _tabbed_draw_table(self, canvas: tk.Canvas, table: np.ndarray, label: str, *, patch: PatchSettings | None = None) -> None:
    if not bool(self.visualisers_visible.get()):
        return
    mode = str(getattr(self, "visualizer_mode", tk.StringVar(value="Base table")).get())
    y = np.asarray(table, dtype=np.float32)
    draw_label = label
    if patch is not None and mode != "Base table":
        depth_scale = 1.0
        if mode.startswith("Animated"):
            phase = float(getattr(self, "visual_anim_phase", 0.0))
            depth_scale = 0.5 - 0.5 * math.cos(2.0 * math.pi * phase)
            draw_label = f"{label}: animated stable FM/PWM/AM depth {depth_scale:.2f}×"
        else:
            draw_label = f"{label}: phase-locked rendered FM/PWM/AM"
        y = render_selfmod_phase_locked(
            np.asarray(table, dtype=np.float32),
            fm=float(patch.fm),
            pwm=float(patch.pwm),
            am=float(patch.am),
            depth_scale=depth_scale,
        )
    else:
        draw_label = f"{label}: base table"

    w = max(int(canvas.winfo_width()), 200)
    h = max(int(canvas.winfo_height()), 120)
    pad = 14
    canvas.delete("all")
    canvas.create_rectangle(pad, pad, w - pad, h - pad, outline="#444444")
    mid = h / 2.0
    canvas.create_line(pad, mid, w - pad, mid, fill="#333333")
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
    # TUTORIAL: samples, and table generation can sometimes hit extreme math.
    # TUTORIAL: ------------------------------------------------------------------------
    y = np.nan_to_num(np.asarray(y, dtype=np.float64).reshape(-1), nan=0.0, posinf=1.0, neginf=-1.0)
    if y.size < 2:
        y = fallback_table().astype(np.float64)
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Clip the signal/control range after math that might overshoot. This keeps audio
    # TUTORIAL: buffers and tables bounded.
    # TUTORIAL: ------------------------------------------------------------------------
    y = np.clip(y, -1.0, 1.0)
    pts = []
    for i, sample in enumerate(y):
        x = pad + (w - 2 * pad) * (i / max(y.size - 1, 1))
        yy = mid - float(sample) * ((h - 2 * pad) * 0.46)
        pts.extend([x, yy])
    canvas.create_line(*pts, fill="#00d0ff", width=2)
    canvas.create_text(pad, h - 4, anchor="sw", fill="#999999", text=draw_label)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Initial tabbed-UI helper: _tabbed_update_all_visualisers is part of the first
# TUTORIAL: START/END tab implementation. Later v3-v7 helpers may replace parts of this
# TUTORIAL: behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _tabbed_update_all_visualisers(self) -> None:
    self._update_type_descriptions()
    try:
        single_patch = self._patch_from_vars(self.single_vars)
        table, _notes = _table_for_patch(single_patch)
        self._draw_table(self.single_canvas, table, "Playground", patch=single_patch)

        st_patch = self._patch_from_vars(self.start_vars)
        en_patch = self._patch_from_vars(self.end_vars)
        st_patch.wavetable_type = self._effective_start_type()
        en_patch.wavetable_type = self._effective_end_type()
        if hasattr(self, "different_fm_ranges_var") and not bool(self.different_fm_ranges_var.get()):
            st_patch.fm_range_label = str(self.shared_fm_range_var.get())
            en_patch.fm_range_label = str(self.shared_fm_range_var.get())
        st, _ = _table_for_patch(st_patch, wavetable_type=st_patch.wavetable_type)
        en, _ = _table_for_patch(en_patch, wavetable_type=en_patch.wavetable_type)
        start_canvas = self.start_vars.get("_canvas")
        end_canvas = self.end_vars.get("_canvas")
        if isinstance(start_canvas, tk.Canvas):
            self._draw_table(start_canvas, st, "START", patch=st_patch)
        if isinstance(end_canvas, tk.Canvas):
            self._draw_table(end_canvas, en, "END", patch=en_patch)
    except Exception as exc:
        self.status.config(text=f"VISUAL ERROR  {type(exc).__name__}: {exc}")


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Initial tabbed-UI helper: _tabbed_schedule_visual_animation_if_needed is part of the
# TUTORIAL: first START/END tab implementation. Later v3-v7 helpers may replace parts of this
# TUTORIAL: behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _tabbed_schedule_visual_animation_if_needed(self) -> None:
    mode = str(getattr(self, "visualizer_mode", tk.StringVar(value="Base table")).get())
    if not mode.startswith("Animated"):
        after_id = getattr(self, "visual_anim_after_id", None)
        if after_id is not None:
            try:
                # TUTORIAL: ------------------------------------------------------------------------
                # TUTORIAL: Tk uses scheduled callbacks instead of long blocking loops.
                # TUTORIAL: after(...) lets the UI stay responsive.
                # TUTORIAL: ------------------------------------------------------------------------
                self.root.after_cancel(after_id)
            except Exception:
                pass
            self.visual_anim_after_id = None
        return
    if getattr(self, "visual_anim_after_id", None) is None:
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: Tk uses scheduled callbacks instead of long blocking loops. after(...) lets
        # TUTORIAL: the UI stay responsive.
        # TUTORIAL: ------------------------------------------------------------------------
        self.visual_anim_after_id = self.root.after(80, self._visual_animation_tick)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Initial tabbed-UI helper: _tabbed_visual_animation_tick is part of the first
# TUTORIAL: START/END tab implementation. Later v3-v7 helpers may replace parts of this
# TUTORIAL: behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _tabbed_visual_animation_tick(self) -> None:
    self.visual_anim_after_id = None
    mode = str(getattr(self, "visualizer_mode", tk.StringVar(value="Base table")).get())
    if not mode.startswith("Animated"):
        return
    self.visual_anim_phase = (float(getattr(self, "visual_anim_phase", 0.0)) + 0.018) % 1.0
    self._update_all_visualisers()
    self._schedule_visual_animation_if_needed()


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Initial tabbed-UI helper: _tabbed_morph_changed is part of the first START/END tab
# TUTORIAL: implementation. Later v3-v7 helpers may replace parts of this behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _tabbed_morph_changed(self) -> None:
    if self._suppress:
        return
    try:
        self._refresh_endpoint_unlocks()
        self._tabbed_update_note_label()
        summary = self._summary_from_ui()
        lines = [
            f"Requested: {summary.requested_value:g} {summary.requested_mode.lower()} → "
            f"{summary.sample_count} samples, {_format_seconds_compact(summary.duration_seconds)}, "
            f"{summary.nominal_cycles:.3f} nominal cycles, {summary.samples_per_cycle:.2f} samples/cycle."
        ]
        if summary.warnings:
            lines.append("Warnings: " + "; ".join(summary.warnings))
        if summary.suggestions:
            lines.append("Suggestions: " + "; ".join(summary.suggestions[:3]))
        self.consequence_var.set("\n".join(lines))
    except Exception as exc:
        self.consequence_var.set(f"Duration calculation error: {type(exc).__name__}: {exc}")
    self._update_all_visualisers()
    self._schedule_visual_animation_if_needed()


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Initial tabbed-UI helper: _tabbed_push_patch_to_audio is part of the first START/END
# TUTORIAL: tab implementation. Later v3-v7 helpers may replace parts of this behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _tabbed_push_patch_to_audio(self, patch: PatchSettings, *, volume: float | None = None, wavetable_type: int | None = None) -> None:
    # Endpoint audition uses the live oscillator, not the morph preview buffer.
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
    # TUTORIAL: callback may touch it at the same time.
    # TUTORIAL: ------------------------------------------------------------------------
    with self.state.lock:
        self.state.target_preview_buffer_active = False
        self.state.target_preview_buffer = None
        self.state.target_preview_buffer_pos = 0
    table, notes = _table_for_patch(patch, wavetable_type=wavetable_type)
    freq = clamp_float(_safe_float_from_var(self.frequency_var, self.default_frequency), MIN_FREQUENCY, MAX_FREQUENCY)
    vol = clamp_float(_safe_float_from_var(self.volume_var, 0.20) if volume is None else volume, 0.0, 1.0)
    fm_range = fm_range_label_to_cents(patch.fm_range_label)
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
    # TUTORIAL: callback may touch it at the same time.
    # TUTORIAL: ------------------------------------------------------------------------
    with self.state.lock:
        self.state.target_table = table
        self.state.target_volume = vol
        self.state.target_frequency = freq
        self.state.target_fm = patch.fm
        self.state.target_pwm = patch.pwm
        self.state.target_am = patch.am
        self.state.target_fm_range_cents = fm_range
        self.state.target_before_start = 0.0
        self.state.target_before_length = WINDOW_STEPS
        self.state.target_after_start = 0.0
        self.state.target_after_length = WINDOW_STEPS
        self.state.target_audio_gate = 1.0
        self.state.target_play_remaining_samples = None
        self.state.target_transport_mode = "drone"
        self.state.reset_requested = True
        sanitise_shared_state(self.state)
    suffix = ""
    if notes:
        suffix = "  Notes: " + "; ".join(sorted(set(notes))[:4])
    self.status.config(text=f"AUDITION  frequency={freq:.3f} Hz volume={vol:.2f} transport=Drone" + suffix)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Initial tabbed-UI helper: _tabbed_audition_morph_transition is part of the first
# TUTORIAL: START/END tab implementation. Later v3-v7 helpers may replace parts of this
# TUTORIAL: behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _tabbed_audition_morph_transition(self, reverse: bool = False) -> None:
    self.notebook.select(self.morph_tab)
    summary = self._summary_from_ui()
    start = self._patch_from_vars(self.start_vars)
    end = self._patch_from_vars(self.end_vars)
    start.wavetable_type = self._effective_start_type()
    end.wavetable_type = self._effective_end_type()
    if not bool(self.different_fm_ranges_var.get()):
        start.fm_range_label = str(self.shared_fm_range_var.get())
        end.fm_range_label = str(self.shared_fm_range_var.get())
    try:
        audio, notes = _render_morph_audio(
            start,
            end,
            summary=summary,
            shared_type=None if bool(self.different_types_var.get()) else self._effective_start_type(),
            different_types=bool(self.different_types_var.get()),
            different_fm_ranges=bool(self.different_fm_ranges_var.get()),
            curve_amount=clamp_int(_safe_int_from_var(self.curve_amount_var, 127), 1, 255),
            curve_mode=str(self.curve_mode_var.get()),
            cycle_stepped=bool(self.cycle_stepped_var.get()),
            reverse=bool(reverse),
            dc_mode=str(self.dc_mode_var.get()),
            norm_mode=str(self.normalise_mode_var.get()),
            headroom_db=_safe_float_from_var(self.headroom_var, MORPH_HEADROOM_DEFAULT_DB),
        )
        vol = clamp_float(_safe_float_from_var(self.volume_var, 0.20), 0.0, 1.0)
        audio = np.asarray(audio, dtype=np.float32) * vol
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
        # TUTORIAL: callback may touch it at the same time.
        # TUTORIAL: ------------------------------------------------------------------------
        with self.state.lock:
            self.state.target_preview_buffer = audio.astype(np.float32)
            self.state.target_preview_buffer_pos = 0
            self.state.target_preview_buffer_active = True
            self.state.target_audio_gate = 1.0
            self.state.target_play_remaining_samples = None
            self.state.target_transport_mode = "preview"
        direction = "END→START" if reverse else "START→END"
        suffix = ("  Notes: " + "; ".join(notes[:4])) if notes else ""
        self.status.config(text=f"AUDITION {direction}  {_format_seconds_compact(summary.duration_seconds)} buffer rendered and playing" + suffix)
    except Exception as exc:
        self.status.config(text=f"AUDITION ERROR  {type(exc).__name__}: {exc}")


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Initial tabbed-UI helper: _tabbed_choose_export_file is part of the first START/END
# TUTORIAL: tab implementation. Later v3-v7 helpers may replace parts of this behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _tabbed_choose_export_file(self) -> None:
    current = self.filename_var.get().strip() or self._default_export_name()
    initialdir = _os.path.dirname(current) if _os.path.isabs(current) else _os.getcwd()
    initialfile = _os.path.basename(current)
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: File dialogs are UI operations, so they happen outside the audio callback and
    # TUTORIAL: before/after offline rendering.
    # TUTORIAL: ------------------------------------------------------------------------
    path = _filedialog.asksaveasfilename(
        title="Create WAV",
        defaultextension=".wav",
        initialdir=initialdir,
        initialfile=initialfile,
        filetypes=(("WAV audio", "*.wav"), ("All files", "*.*")),
    )
    if path:
        self.filename_var.set(path)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Initial tabbed-UI helper: _tabbed_create_wav_clicked is part of the first START/END
# TUTORIAL: tab implementation. Later v3-v7 helpers may replace parts of this behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _tabbed_create_wav_clicked(self) -> None:
    # Always ask for confirmation so the text field, folder, and actual export
    # target cannot silently diverge.
    current = self.filename_var.get().strip() or self._default_export_name()
    initialdir = _os.path.dirname(current) if _os.path.isabs(current) else _os.getcwd()
    initialfile = _os.path.basename(current)
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: File dialogs are UI operations, so they happen outside the audio callback and
    # TUTORIAL: before/after offline rendering.
    # TUTORIAL: ------------------------------------------------------------------------
    chosen = _filedialog.asksaveasfilename(
        title="Create WAV + JSON",
        defaultextension=".wav",
        initialdir=initialdir,
        initialfile=initialfile,
        filetypes=(("WAV audio", "*.wav"), ("All files", "*.*")),
    )
    if not chosen:
        self.status.config(text="EXPORT CANCELLED")
        return
    self.filename_var.set(chosen)
    base_path = chosen
    summary = self._summary_from_ui()
    start = self._patch_from_vars(self.start_vars)
    end = self._patch_from_vars(self.end_vars)
    start.wavetable_type = self._effective_start_type()
    end.wavetable_type = self._effective_end_type()
    if not bool(self.different_fm_ranges_var.get()):
        start.fm_range_label = str(self.shared_fm_range_var.get())
        end.fm_range_label = str(self.shared_fm_range_var.get())

    directions = ["forward", "reverse"] if str(self.direction_var.get()) == "Both" else (["reverse"] if str(self.direction_var.get()) == "END→START" else ["forward"])
    created: list[str] = []
    try:
        for direction in directions:
            reverse = direction == "reverse"
            audio, notes = _render_morph_audio(
                start,
                end,
                summary=summary,
                shared_type=None if bool(self.different_types_var.get()) else self._effective_start_type(),
                different_types=bool(self.different_types_var.get()),
                different_fm_ranges=bool(self.different_fm_ranges_var.get()),
                curve_amount=clamp_int(_safe_int_from_var(self.curve_amount_var, 127), 1, 255),
                curve_mode=str(self.curve_mode_var.get()),
                cycle_stepped=bool(self.cycle_stepped_var.get()),
                reverse=reverse,
                dc_mode=str(self.dc_mode_var.get()),
                norm_mode=str(self.normalise_mode_var.get()),
                headroom_db=_safe_float_from_var(self.headroom_var, MORPH_HEADROOM_DEFAULT_DB),
            )
            wav_path, json_path = self._export_paths(base_path, direction)
            _os.makedirs(_os.path.dirname(wav_path) or ".", exist_ok=True)
            _write_wav_mono(wav_path, audio, summary.sample_rate, summary.bit_depth)
            metadata = {
                "app": "Fractal Wavetable START-END Morph Export",
                "created_epoch": int(_time.time()),
                "direction": "END_TO_START" if reverse else "START_TO_END",
                "summary": _asdict(summary),
                "start_patch": _asdict(start),
                "end_patch": _asdict(end),
                "transition": {
                    "curve_mode": str(self.curve_mode_var.get()),
                    "curve_amount": clamp_int(_safe_int_from_var(self.curve_amount_var, 127), 1, 255),
                    "cycle_stepped": bool(self.cycle_stepped_var.get()),
                    "different_wavetable_types": bool(self.different_types_var.get()),
                    "different_fm_ranges": bool(self.different_fm_ranges_var.get()),
                },
                "output_processing": {
                    "dc_removal": str(self.dc_mode_var.get()),
                    "normalise": str(self.normalise_mode_var.get()),
                    "headroom_db": _safe_float_from_var(self.headroom_var, MORPH_HEADROOM_DEFAULT_DB),
                },
                "render_notes": notes,
            }
            if str(summary.bit_depth).startswith("12"):
                metadata["bit_depth_note"] = "12-bit quantisation written in a 16-bit PCM WAV container"
            with open(json_path, "w", encoding="utf-8") as f:
                # TUTORIAL: ------------------------------------------------------------------------
                # TUTORIAL: JSON sidecars are written beside WAV files so settings can be
                # TUTORIAL: inspected, archived, and later imported.
                # TUTORIAL: ------------------------------------------------------------------------
                _json.dump(metadata, f, indent=2, sort_keys=True)
            created.extend([wav_path, json_path])
        self.status.config(text="EXPORT COMPLETE  " + "  ".join(_os.path.basename(x) for x in created))
    except Exception as exc:
        self.status.config(text=f"EXPORT ERROR  {type(exc).__name__}: {exc}")


TabbedFractalApp._tabbed_ensure_common_vars = _tabbed_ensure_common_vars
TabbedFractalApp._tabbed_update_note_label = _tabbed_update_note_label
TabbedFractalApp._tabbed_frequency_committed = _tabbed_frequency_committed
TabbedFractalApp._tabbed_note_changed = _tabbed_note_changed
TabbedFractalApp._add_frequency_note_row = _tabbed_add_frequency_note_row
TabbedFractalApp._add_transport_row = _tabbed_add_transport_row
TabbedFractalApp._set_transport_state = _tabbed_set_transport_state
TabbedFractalApp.drone_audio = _tabbed_drone_audio
TabbedFractalApp.stop_audio = _tabbed_stop_audio
TabbedFractalApp._tabbed_wt_dropdown = _tabbed_wt_dropdown
TabbedFractalApp._add_patch_controls = _tabbed_add_patch_controls
TabbedFractalApp._add_visual_mode_controls = _tabbed_add_visual_mode_controls
TabbedFractalApp._visual_mode_changed = _tabbed_visual_mode_changed
TabbedFractalApp._build_playground_tab = _tabbed_build_playground_tab
TabbedFractalApp._add_shared_live_controls = _tabbed_add_shared_live_controls
TabbedFractalApp._build_endpoint_panel = _tabbed_build_endpoint_panel
TabbedFractalApp._build_morph_shared_controls = _tabbed_build_morph_shared_controls
TabbedFractalApp._update_type_descriptions = _tabbed_update_type_descriptions
TabbedFractalApp._refresh_endpoint_unlocks = _tabbed_refresh_endpoint_unlocks
TabbedFractalApp._draw_table = _tabbed_draw_table
TabbedFractalApp._update_all_visualisers = _tabbed_update_all_visualisers
TabbedFractalApp._schedule_visual_animation_if_needed = _tabbed_schedule_visual_animation_if_needed
TabbedFractalApp._visual_animation_tick = _tabbed_visual_animation_tick
TabbedFractalApp._morph_changed = _tabbed_morph_changed
TabbedFractalApp._push_patch_to_audio = _tabbed_push_patch_to_audio
TabbedFractalApp._audition_morph_transition = _tabbed_audition_morph_transition
TabbedFractalApp._choose_export_file = _tabbed_choose_export_file
TabbedFractalApp._create_wav_clicked = _tabbed_create_wav_clicked



# -----------------------------------------------------------------------------
# v3 UI/transport corrections
#
# - Morph tab has independent scrollable content.
# - Playground and Morph visualiser visibility/mode are independent.
# - Morph transport has Drone START, Drone END, and Stop.
# - Tab changes preserve each tab's stopped/drone state instead of reopening audio.
# - Morph endpoint edits drone the edited endpoint while the Morph tab is active.
# - Export filename field is now a prefix only; each save dialog gets a fresh
#   timestamped suggested filename.
# - Nearest pitch label shows the nearest note's frequency, not the typed value.
# -----------------------------------------------------------------------------


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_compact_number belongs to the scrollable Morph UI,
# TUTORIAL: independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_compact_number(value: float, *, max_decimals: int = 3) -> str:
    try:
        v = float(value)
    except Exception:
        return str(value)
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    s = f"{v:.{max_decimals}f}".rstrip("0").rstrip(".")
    return s if s else "0"


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_active_key belongs to the scrollable Morph UI,
# TUTORIAL: independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_active_key(self) -> str:
    return "playground" if self._active_tab_name() == "Single Engine / Playground" else "morph"


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_ensure_common_vars belongs to the scrollable Morph
# TUTORIAL: UI, independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_ensure_common_vars(self) -> None:
    if not hasattr(self, "frequency_var"):
        self.frequency_var = tk.DoubleVar(value=self.default_frequency)
    if not hasattr(self, "volume_var"):
        self.volume_var = tk.DoubleVar(value=0.20)
    if not hasattr(self, "note_var"):
        note, octave = nearest_note_for_frequency(self.default_frequency)
        self.note_var = tk.StringVar(value=note)
        self.octave_var = tk.StringVar(value=str(octave))
        self.note_frequency_label_var = tk.StringVar(value="")

    # Per-tab transport memory. Initial playground matches the startup behaviour;
    # Morph starts muted until a START/END/transition audition is chosen.
    if not hasattr(self, "playground_transport_mode"):
        self.playground_transport_mode = "drone"
    if not hasattr(self, "morph_transport_mode"):
        self.morph_transport_mode = "stopped"
    if not hasattr(self, "morph_drone_endpoint"):
        self.morph_drone_endpoint = "start"

    # Independent visualiser state per tab.
    if not hasattr(self, "playground_visualisers_visible"):
        self.playground_visualisers_visible = tk.BooleanVar(value=True)
    if not hasattr(self, "morph_visualisers_visible"):
        self.morph_visualisers_visible = tk.BooleanVar(value=True)
    if not hasattr(self, "playground_visualizer_mode"):
        self.playground_visualizer_mode = tk.StringVar(value="Base table")
    if not hasattr(self, "morph_visualizer_mode"):
        self.morph_visualizer_mode = tk.StringVar(value="Base table")
    if not hasattr(self, "visual_anim_phase"):
        self.visual_anim_phase = 0.0
    if not hasattr(self, "visual_anim_after_id"):
        self.visual_anim_after_id = None
    if not hasattr(self, "last_export_dir"):
        self.last_export_dir = _os.getcwd()

    self._tabbed_update_note_label()


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_update_note_label belongs to the scrollable Morph UI,
# TUTORIAL: independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_update_note_label(self) -> None:
    try:
        freq = clamp_float(_safe_float_from_var(self.frequency_var, self.default_frequency), MIN_FREQUENCY, MAX_FREQUENCY)
        note, octave = nearest_note_for_frequency(freq)
        nearest_freq = note_to_frequency(note, octave)
        if hasattr(self, "note_frequency_label_var"):
            self.note_frequency_label_var.set(f"nearest: {note}{octave} = {_v3_compact_number(nearest_freq)} Hz")
    except Exception:
        pass


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_frequency_committed belongs to the scrollable Morph
# TUTORIAL: UI, independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_frequency_committed(self, _event=None) -> None:
    self._tabbed_ensure_common_vars()
    freq = clamp_float(_safe_float_from_var(self.frequency_var, self.default_frequency), MIN_FREQUENCY, MAX_FREQUENCY)
    self.frequency_var.set(round(freq, 6))
    note, octave = nearest_note_for_frequency(freq)
    try:
        self.note_var.set(note)
        self.octave_var.set(str(octave))
    except Exception:
        pass
    self._tabbed_update_note_label()
    if self._active_tab_name() == "Single Engine / Playground":
        self._playground_changed()
    else:
        self._morph_shared_changed()


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_note_changed belongs to the scrollable Morph UI,
# TUTORIAL: independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_note_changed(self, _value=None) -> None:
    self._tabbed_ensure_common_vars()
    try:
        freq = note_to_frequency(str(self.note_var.get()), int(str(self.octave_var.get())))
    except Exception:
        freq = self.default_frequency
    freq = clamp_float(freq, MIN_FREQUENCY, MAX_FREQUENCY)
    self.frequency_var.set(round(freq, 6))
    self._tabbed_update_note_label()
    if self._active_tab_name() == "Single Engine / Playground":
        self._playground_changed()
    else:
        self._morph_shared_changed()


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_add_frequency_note_row belongs to the scrollable
# TUTORIAL: Morph UI, independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_add_frequency_note_row(self, parent, *, command) -> None:
    self._tabbed_ensure_common_vars()
    row = tk.Frame(parent)
    row.pack(fill="x", padx=8, pady=(8, 2))
    tk.Label(row, text="Frequency", width=15, anchor="w").pack(side="left")
    freq = tk.Entry(row, textvariable=self.frequency_var, width=12, justify="right")
    freq.pack(side="left")
    freq.bind("<Return>", self._tabbed_frequency_committed)
    freq.bind("<FocusOut>", self._tabbed_frequency_committed)
    tk.Label(row, text="Hz").pack(side="left", padx=(4, 10))
    tk.Label(row, text="Note").pack(side="left")
    note_menu = tk.OptionMenu(row, self.note_var, *NOTE_NAMES, command=self._tabbed_note_changed)
    note_menu.config(width=4)
    note_menu.pack(side="left", padx=(4, 0))
    tk.Label(row, text="Octave").pack(side="left", padx=(10, 4))
    octave_menu = tk.OptionMenu(row, self.octave_var, *[str(i) for i in range(0, 11)], command=self._tabbed_note_changed)
    octave_menu.config(width=4)
    octave_menu.pack(side="left")
    tk.Label(row, textvariable=self.note_frequency_label_var, anchor="w").pack(side="left", padx=(12, 0), fill="x", expand=True)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_set_audio_stopped belongs to the scrollable Morph UI,
# TUTORIAL: independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_set_audio_stopped(self, *, status: str | None = None) -> None:
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
    # TUTORIAL: callback may touch it at the same time.
    # TUTORIAL: ------------------------------------------------------------------------
    with self.state.lock:
        sanitise_shared_state(self.state)
        self.state.target_audio_gate = 0.0
        self.state.target_play_remaining_samples = 0
        self.state.target_transport_mode = "stopped"
        self.state.target_preview_buffer_active = False
        self.state.target_preview_buffer = None
        self.state.target_preview_buffer_pos = 0
        self.state.reset_requested = True
    if status:
        self.status.config(text=status)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_set_transport_state belongs to the scrollable Morph
# TUTORIAL: UI, independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_set_transport_state(self, mode: str) -> None:
    # Kept for compatibility with older button callbacks, but now per-tab aware.
    key = _v3_active_key(self)
    if str(mode) == "stop":
        if key == "playground":
            self.playground_transport_mode = "stopped"
        else:
            self.morph_transport_mode = "stopped"
        self._set_audio_stopped(status="STOP  audio gated off; stream remains alive")
        return

    if key == "playground":
        self.playground_transport_mode = "drone"
        self._push_playground_to_audio()
    else:
        self.morph_transport_mode = "drone"
        self._push_morph_endpoint_to_audio(getattr(self, "morph_drone_endpoint", "start"), select_tab=False)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_drone_audio belongs to the scrollable Morph UI,
# TUTORIAL: independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_drone_audio(self) -> None:
    self._set_transport_state("drone")


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_stop_audio belongs to the scrollable Morph UI,
# TUTORIAL: independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_stop_audio(self) -> None:
    self._set_transport_state("stop")


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_add_transport_row belongs to the scrollable Morph UI,
# TUTORIAL: independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_add_transport_row(self, parent, *, label_width: int = 15) -> None:
    row = tk.Frame(parent)
    row.pack(fill="x", padx=8, pady=(2, 6))
    tk.Label(row, text="Transport", width=label_width, anchor="w").pack(side="left")
    tk.Button(row, text="Drone", command=self.drone_audio).pack(side="left")
    tk.Button(row, text="Stop", command=self.stop_audio).pack(side="left", padx=(8, 0))


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_add_visual_mode_controls belongs to the scrollable
# TUTORIAL: Morph UI, independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_add_visual_mode_controls(self, parent, mode_var: tk.StringVar, command, *, include_show: tk.BooleanVar | None = None, show_command=None) -> None:
    row = tk.Frame(parent)
    row.pack(fill="x", padx=6, pady=(4, 2))
    if include_show is not None:
        tk.Checkbutton(row, text="Show visualiser", variable=include_show, command=show_command).pack(side="left")
        tk.Label(row, text="Mode").pack(side="left", padx=(12, 0))
    else:
        tk.Label(row, text="Visualiser").pack(side="left")
    for text in VISUALIZER_MODE_OPTIONS:
        tk.Radiobutton(row, text=text, variable=mode_var, value=text, command=command).pack(side="left", padx=(8, 0))


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_visual_mode_changed belongs to the scrollable Morph
# TUTORIAL: UI, independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_visual_mode_changed(self) -> None:
    self._update_all_visualisers()
    self._schedule_visual_animation_if_needed()


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_build_playground_tab belongs to the scrollable Morph
# TUTORIAL: UI, independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_build_playground_tab(self) -> None:
    self._tabbed_ensure_common_vars()
    outer = tk.Frame(self.playground_tab)
    outer.pack(fill="both", expand=True)
    controls = tk.Frame(outer)
    controls.pack(side="left", fill="both", expand=True, padx=8, pady=8)
    visual = tk.Frame(outer, width=620)
    visual.pack(side="right", fill="both", padx=8, pady=8)
    visual.pack_propagate(False)
    self.single_visual_parent = visual

    top = tk.Frame(controls)
    top.pack(fill="x", pady=(0, 6))
    tk.Button(top, text="Randomise", command=self._randomise_single).pack(side="left")
    tk.Button(top, text="Reset", command=self._reset_single).pack(side="left", padx=(8, 0))
    tk.Button(top, text="Panic audio", command=self._panic_audio).pack(side="left", padx=(8, 0))

    self._add_patch_controls(controls, self.single_vars, command=self._playground_changed, include_fm_range=True)
    self._add_shared_live_controls(controls, command=self._playground_changed)
    self._add_transport_row(controls)

    tk.Label(visual, text="Playground waveform", anchor="center").pack(fill="x")
    self._add_visual_mode_controls(
        visual,
        self.playground_visualizer_mode,
        self._visual_mode_changed,
        include_show=self.playground_visualisers_visible,
        show_command=self._toggle_playground_visualiser,
    )
    self.single_canvas = tk.Canvas(visual, width=560, height=420, bg="black", highlightthickness=1, highlightbackground="#555555")
    self.single_canvas.pack(fill="both", expand=True, pady=(6, 0))


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_make_scrollable_tab belongs to the scrollable Morph
# TUTORIAL: UI, independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_make_scrollable_tab(self, parent: tk.Frame) -> tk.Frame:
    canvas = tk.Canvas(parent, highlightthickness=0)
    scrollbar = tk.Scrollbar(parent, orient="vertical", command=canvas.yview)
    inner = tk.Frame(canvas)
    win_id = canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function on_inner_config: small named steps make the signal path, UI
    # TUTORIAL: path, and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def on_inner_config(_event=None):
        canvas.configure(scrollregion=canvas.bbox("all"))

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function on_canvas_config: small named steps make the signal path, UI
    # TUTORIAL: path, and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def on_canvas_config(event):
        try:
            canvas.itemconfigure(win_id, width=event.width)
        except Exception:
            pass

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function on_mousewheel: small named steps make the signal path, UI path,
    # TUTORIAL: and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def on_mousewheel(event):
        # Linux uses Button-4/5; Windows/macOS commonly use MouseWheel.
        if getattr(event, "num", None) == 4:
            canvas.yview_scroll(-3, "units")
        elif getattr(event, "num", None) == 5:
            canvas.yview_scroll(3, "units")
        else:
            delta = int(getattr(event, "delta", 0))
            if delta:
                canvas.yview_scroll(int(-delta / 120), "units")

    inner.bind("<Configure>", on_inner_config)
    canvas.bind("<Configure>", on_canvas_config)
    canvas.bind_all("<MouseWheel>", on_mousewheel)
    canvas.bind_all("<Button-4>", on_mousewheel)
    canvas.bind_all("<Button-5>", on_mousewheel)
    self.morph_scroll_canvas = canvas
    return inner


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_build_morph_tab belongs to the scrollable Morph UI,
# TUTORIAL: independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_build_morph_tab(self) -> None:
    self._tabbed_ensure_common_vars()
    outer = tk.Frame(self.morph_tab)
    outer.pack(fill="both", expand=True)
    content = self._make_scrollable_tab(outer)

    toolbar = tk.Frame(content)
    toolbar.pack(fill="x", padx=8, pady=(8, 4))
    tk.Button(toolbar, text="Randomise START", command=lambda: self._randomise_morph("start")).pack(side="left")
    tk.Button(toolbar, text="Randomise END", command=lambda: self._randomise_morph("end")).pack(side="left", padx=(6, 0))
    tk.Button(toolbar, text="Randomise BOTH", command=lambda: self._randomise_morph("both")).pack(side="left", padx=(6, 0))
    tk.Button(toolbar, text="Copy START→END", command=self._copy_start_to_end).pack(side="left", padx=(12, 0))
    tk.Button(toolbar, text="Copy END→START", command=self._copy_end_to_start).pack(side="left", padx=(6, 0))
    tk.Button(toolbar, text="Swap", command=self._swap_start_end).pack(side="left", padx=(6, 0))

    visual_row = tk.Frame(content)
    visual_row.pack(fill="x", padx=8, pady=(0, 4))
    self._add_visual_mode_controls(
        visual_row,
        self.morph_visualizer_mode,
        self._visual_mode_changed,
        include_show=self.morph_visualisers_visible,
        show_command=self._toggle_morph_visualisers,
    )

    panels = tk.Frame(content)
    panels.pack(fill="both", expand=True, padx=8, pady=4)
    self.start_panel = self._build_endpoint_panel(panels, "START", self.start_vars, endpoint="start")
    self.start_panel.pack(side="left", fill="both", expand=True, padx=(0, 4))
    self.end_panel = self._build_endpoint_panel(panels, "END", self.end_vars, endpoint="end")
    self.end_panel.pack(side="left", fill="both", expand=True, padx=(4, 0))

    shared = tk.LabelFrame(content, text="Shared transition / output / export")
    shared.pack(fill="x", padx=8, pady=(4, 8))
    self._build_morph_shared_controls(shared)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_build_endpoint_panel belongs to the scrollable Morph
# TUTORIAL: UI, independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_build_endpoint_panel(self, parent, title: str, vars_dict: dict[str, tk.Variable], endpoint: str | None = None):
    panel = tk.LabelFrame(parent, text=title)
    visual_frame = tk.Frame(panel)
    visual_frame.pack(fill="x", padx=6, pady=(6, 0))
    canvas = tk.Canvas(visual_frame, width=420, height=190, bg="black", highlightthickness=1, highlightbackground="#555555")
    canvas.pack(fill="x", expand=True)
    vars_dict["_canvas"] = canvas  # type: ignore[assignment]
    vars_dict["_visual_frame"] = visual_frame  # type: ignore[assignment]
    ep = endpoint or ("end" if title.upper().startswith("END") else "start")
    self._add_patch_controls(panel, vars_dict, command=lambda ep=ep: self._morph_patch_changed(ep), include_fm_range=True, endpoint_panel=True)
    return panel


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_build_morph_shared_controls belongs to the scrollable
# TUTORIAL: Morph UI, independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_build_morph_shared_controls(self, parent) -> None:
    self._tabbed_ensure_common_vars()
    self.shared_type_var = tk.IntVar(value=3)
    self.shared_type_option_var = tk.StringVar(value=wavetable_type_option(3))
    self.different_types_var = tk.BooleanVar(value=False)
    self.shared_fm_range_var = tk.StringVar(value=DEFAULT_FM_RANGE_LABEL)
    self.different_fm_ranges_var = tk.BooleanVar(value=False)
    self.duration_mode_var = tk.StringVar(value="Cycles")
    self.duration_value_var = tk.StringVar(value=str(MORPH_DEFAULT_CYCLES))
    self.sample_rate_var = tk.IntVar(value=self.sample_rate if int(self.sample_rate) in EXPORT_SAMPLE_RATES else DEFAULT_SAMPLE_RATE)
    self.bit_depth_var = tk.StringVar(value="24-bit PCM")
    self.curve_mode_var = tk.StringVar(value="Bend")
    self.curve_amount_var = tk.IntVar(value=127)
    self.cycle_stepped_var = tk.BooleanVar(value=True)
    self.direction_var = tk.StringVar(value="START→END")
    self.dc_mode_var = tk.StringVar(value="Per cycle")
    self.normalise_mode_var = tk.StringVar(value="Per cycle peak")
    self.headroom_var = tk.DoubleVar(value=MORPH_HEADROOM_DEFAULT_DB)
    self.filename_prefix_var = tk.StringVar(value="fractal_wt_morph")
    self.consequence_var = tk.StringVar(value="")

    freq_box = tk.Frame(parent); freq_box.pack(fill="x", padx=8, pady=3)
    self._add_frequency_note_row(freq_box, command=self._morph_shared_changed)

    line1 = tk.Frame(parent); line1.pack(fill="x", padx=8, pady=3)
    tk.Checkbutton(line1, text="Different START/END wavetable types", variable=self.different_types_var, command=self._morph_shared_changed).pack(side="left")
    tk.Label(line1, text="Shared type").pack(side="left", padx=(12, 4))
    shared_combo = _ttk.Combobox(line1, textvariable=self.shared_type_option_var, values=WT_OPTION_LIST, state="readonly", width=58)
    shared_combo.pack(side="left", fill="x", expand=True)

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function shared_type_changed: small named steps make the signal path, UI
    # TUTORIAL: path, and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def shared_type_changed(_event=None):
        self.shared_type_var.set(wavetable_type_from_option(self.shared_type_option_var.get()))
        self._morph_shared_changed()

    shared_combo.bind("<<ComboboxSelected>>", shared_type_changed)
    self.shared_type_combo = shared_combo

    line1b = tk.Frame(parent); line1b.pack(fill="x", padx=8, pady=3)
    tk.Label(line1b, text="Shared FM Range").pack(side="left")
    tk.OptionMenu(line1b, self.shared_fm_range_var, *[label for label, _c in FM_RANGE_OPTIONS], command=lambda _v: self._morph_shared_changed()).pack(side="left", padx=(6, 0))
    tk.Checkbutton(line1b, text="Different START/END FM ranges", variable=self.different_fm_ranges_var, command=self._morph_shared_changed).pack(side="left", padx=(12, 0))

    line2 = tk.Frame(parent); line2.pack(fill="x", padx=8, pady=3)
    tk.Label(line2, text="Duration").pack(side="left")
    tk.OptionMenu(line2, self.duration_mode_var, "Seconds", "Cycles", "Samples", command=lambda _v: self._morph_shared_changed()).pack(side="left", padx=(6, 0))
    entry = tk.Entry(line2, textvariable=self.duration_value_var, width=10, justify="right")
    entry.pack(side="left", padx=(6, 0))
    entry.bind("<Return>", lambda _e: self._morph_shared_changed())
    entry.bind("<FocusOut>", lambda _e: self._morph_shared_changed())
    tk.OptionMenu(line2, self.direction_var, "START→END", "END→START", "Both", command=lambda _v: self._morph_shared_changed()).pack(side="left", padx=(14, 0))
    tk.Checkbutton(line2, text="Cycle-stepped transition", variable=self.cycle_stepped_var, command=self._morph_shared_changed).pack(side="left", padx=(14, 0))
    tk.Label(line2, text="Curve").pack(side="left", padx=(14, 4))
    tk.OptionMenu(line2, self.curve_mode_var, "Bend", "S-curve", command=lambda _v: self._morph_shared_changed()).pack(side="left")
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Tk Scale is the slider widget. In this app, slider callbacks push new values
    # TUTORIAL: into patch or audio state.
    # TUTORIAL: ------------------------------------------------------------------------
    tk.Scale(line2, variable=self.curve_amount_var, from_=1, to=255, resolution=1, orient="horizontal", length=180, command=lambda _v: self._morph_shared_changed()).pack(side="left", padx=(6, 0))

    line3 = tk.Frame(parent); line3.pack(fill="x", padx=8, pady=3)
    tk.Label(line3, text="Sample rate").pack(side="left")
    tk.OptionMenu(line3, self.sample_rate_var, *EXPORT_SAMPLE_RATES, command=lambda _v: self._morph_shared_changed()).pack(side="left", padx=(6, 0))
    tk.Label(line3, text="Bit depth").pack(side="left", padx=(14, 4))
    tk.OptionMenu(line3, self.bit_depth_var, *EXPORT_BIT_DEPTHS, command=lambda _v: self._morph_shared_changed()).pack(side="left")
    tk.Label(line3, text="DC removal").pack(side="left", padx=(14, 4))
    tk.OptionMenu(line3, self.dc_mode_var, "Off", "Whole file", "Per cycle", command=lambda _v: self._morph_shared_changed()).pack(side="left")
    tk.Label(line3, text="Normalise").pack(side="left", padx=(14, 4))
    tk.OptionMenu(line3, self.normalise_mode_var, "Off", "Whole file peak", "Per cycle peak", command=lambda _v: self._morph_shared_changed()).pack(side="left")
    tk.Label(line3, text="Headroom dB").pack(side="left", padx=(14, 4))
    tk.Entry(line3, textvariable=self.headroom_var, width=7, justify="right").pack(side="left")

    line4 = tk.Frame(parent); line4.pack(fill="x", padx=8, pady=3)
    tk.Label(line4, text="Filename prefix").pack(side="left")
    tk.Entry(line4, textvariable=self.filename_prefix_var, width=32).pack(side="left", padx=(6, 0))
    tk.Label(line4, text="Save dialog suggests: <prefix>_<current epoch>.wav").pack(side="left", padx=(10, 0))

    line5 = tk.Frame(parent); line5.pack(fill="x", padx=8, pady=3)
    tk.Button(line5, text="Drone START", command=lambda: self._drone_morph_endpoint("start")).pack(side="left")
    tk.Button(line5, text="Drone END", command=lambda: self._drone_morph_endpoint("end")).pack(side="left", padx=(6, 0))
    tk.Button(line5, text="Stop", command=self.stop_audio).pack(side="left", padx=(6, 0))
    tk.Button(line5, text="Audition START→END", command=lambda: self._audition_morph_transition(False)).pack(side="left", padx=(18, 0))
    tk.Button(line5, text="Audition END→START", command=lambda: self._audition_morph_transition(True)).pack(side="left", padx=(6, 0))
    tk.Button(line5, text="Create WAV + JSON", command=self._create_wav_clicked).pack(side="right")

    tk.Label(parent, textvariable=self.consequence_var, anchor="w", justify="left", wraplength=1600).pack(fill="x", padx=8, pady=(2, 6))
    self._morph_changed()


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_toggle_playground_visualiser belongs to the
# TUTORIAL: scrollable Morph UI, independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_toggle_playground_visualiser(self) -> None:
    visible = bool(self.playground_visualisers_visible.get())
    if hasattr(self, "single_canvas"):
        if visible:
            try:
                self.single_canvas.pack(fill="both", expand=True, pady=(6, 0))
            except Exception:
                pass
        else:
            try:
                self.single_canvas.pack_forget()
            except Exception:
                pass
    self._update_all_visualisers()
    self._schedule_visual_animation_if_needed()


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_toggle_morph_visualisers belongs to the scrollable
# TUTORIAL: Morph UI, independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_toggle_morph_visualisers(self) -> None:
    visible = bool(self.morph_visualisers_visible.get())
    for vars_dict in (getattr(self, "start_vars", {}), getattr(self, "end_vars", {})):
        frame = vars_dict.get("_visual_frame") if isinstance(vars_dict, dict) else None
        if frame is not None:
            try:
                if visible:
                    frame.pack(fill="x", padx=6, pady=(6, 0))
                else:
                    frame.pack_forget()
            except Exception:
                pass
    self._update_all_visualisers()
    self._schedule_visual_animation_if_needed()


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_toggle_visualisers belongs to the scrollable Morph
# TUTORIAL: UI, independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_toggle_visualisers(self) -> None:
    # Compatibility shim for any older callbacks: only affects the active tab.
    if _v3_active_key(self) == "playground":
        self.playground_visualisers_visible.set(not bool(self.playground_visualisers_visible.get()))
        self._toggle_playground_visualiser()
    else:
        self.morph_visualisers_visible.set(not bool(self.morph_visualisers_visible.get()))
        self._toggle_morph_visualisers()


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_draw_table belongs to the scrollable Morph UI,
# TUTORIAL: independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_draw_table(self, canvas: tk.Canvas, table: np.ndarray, label: str, *, patch: PatchSettings | None = None, mode: str = "Base table", visible: bool = True) -> None:
    if not visible:
        return
    y = np.asarray(table, dtype=np.float32)
    draw_label = label
    if patch is not None and str(mode) != "Base table":
        depth_scale = 1.0
        if str(mode).startswith("Animated"):
            phase = float(getattr(self, "visual_anim_phase", 0.0))
            depth_scale = 0.5 - 0.5 * math.cos(2.0 * math.pi * phase)
            draw_label = f"{label}: animated stable FM/PWM/AM depth {depth_scale:.2f}×"
        else:
            draw_label = f"{label}: phase-locked rendered FM/PWM/AM"
        y = render_selfmod_phase_locked(
            np.asarray(table, dtype=np.float32),
            fm=float(patch.fm),
            pwm=float(patch.pwm),
            am=float(patch.am),
            depth_scale=depth_scale,
        )
    else:
        draw_label = f"{label}: base table"

    w = max(int(canvas.winfo_width()), 200)
    h = max(int(canvas.winfo_height()), 120)
    pad = 14
    canvas.delete("all")
    canvas.create_rectangle(pad, pad, w - pad, h - pad, outline="#444444")
    mid = h / 2.0
    canvas.create_line(pad, mid, w - pad, mid, fill="#333333")
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Sanitise NaN/Inf values here. Audio callbacks must not receive non-finite
    # TUTORIAL: samples, and table generation can sometimes hit extreme math.
    # TUTORIAL: ------------------------------------------------------------------------
    y = np.nan_to_num(np.asarray(y, dtype=np.float64).reshape(-1), nan=0.0, posinf=1.0, neginf=-1.0)
    if y.size < 2:
        y = fallback_table().astype(np.float64)
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Clip the signal/control range after math that might overshoot. This keeps audio
    # TUTORIAL: buffers and tables bounded.
    # TUTORIAL: ------------------------------------------------------------------------
    y = np.clip(y, -1.0, 1.0)
    pts = []
    for i, sample in enumerate(y):
        x = pad + (w - 2 * pad) * (i / max(y.size - 1, 1))
        yy = mid - float(sample) * ((h - 2 * pad) * 0.46)
        pts.extend([x, yy])
    canvas.create_line(*pts, fill="#00d0ff", width=2)
    canvas.create_text(pad, h - 4, anchor="sw", fill="#999999", text=draw_label)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_update_all_visualisers belongs to the scrollable
# TUTORIAL: Morph UI, independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_update_all_visualisers(self) -> None:
    self._update_type_descriptions()
    try:
        single_patch = self._patch_from_vars(self.single_vars)
        table, _notes = _table_for_patch(single_patch)
        if hasattr(self, "single_canvas") and bool(self.playground_visualisers_visible.get()):
            self._draw_table(
                self.single_canvas,
                table,
                "Playground",
                patch=single_patch,
                mode=str(self.playground_visualizer_mode.get()),
                visible=True,
            )

        st_patch = self._patch_from_vars(self.start_vars)
        en_patch = self._patch_from_vars(self.end_vars)
        st_patch.wavetable_type = self._effective_start_type()
        en_patch.wavetable_type = self._effective_end_type()
        if hasattr(self, "different_fm_ranges_var") and not bool(self.different_fm_ranges_var.get()):
            st_patch.fm_range_label = str(self.shared_fm_range_var.get())
            en_patch.fm_range_label = str(self.shared_fm_range_var.get())
        st, _ = _table_for_patch(st_patch, wavetable_type=st_patch.wavetable_type)
        en, _ = _table_for_patch(en_patch, wavetable_type=en_patch.wavetable_type)
        start_canvas = self.start_vars.get("_canvas")
        end_canvas = self.end_vars.get("_canvas")
        if bool(getattr(self, "morph_visualisers_visible", tk.BooleanVar(value=True)).get()):
            if isinstance(start_canvas, tk.Canvas):
                self._draw_table(start_canvas, st, "START", patch=st_patch, mode=str(self.morph_visualizer_mode.get()), visible=True)
            if isinstance(end_canvas, tk.Canvas):
                self._draw_table(end_canvas, en, "END", patch=en_patch, mode=str(self.morph_visualizer_mode.get()), visible=True)
    except Exception as exc:
        self.status.config(text=f"VISUAL ERROR  {type(exc).__name__}: {exc}")


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_schedule_visual_animation_if_needed belongs to the
# TUTORIAL: scrollable Morph UI, independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_schedule_visual_animation_if_needed(self) -> None:
    playground_animated = bool(getattr(self, "playground_visualisers_visible", tk.BooleanVar(value=False)).get()) and str(getattr(self, "playground_visualizer_mode", tk.StringVar(value="")).get()).startswith("Animated")
    morph_animated = bool(getattr(self, "morph_visualisers_visible", tk.BooleanVar(value=False)).get()) and str(getattr(self, "morph_visualizer_mode", tk.StringVar(value="")).get()).startswith("Animated")
    if not (playground_animated or morph_animated):
        after_id = getattr(self, "visual_anim_after_id", None)
        if after_id is not None:
            try:
                # TUTORIAL: ------------------------------------------------------------------------
                # TUTORIAL: Tk uses scheduled callbacks instead of long blocking loops.
                # TUTORIAL: after(...) lets the UI stay responsive.
                # TUTORIAL: ------------------------------------------------------------------------
                self.root.after_cancel(after_id)
            except Exception:
                pass
            self.visual_anim_after_id = None
        return
    if getattr(self, "visual_anim_after_id", None) is None:
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: Tk uses scheduled callbacks instead of long blocking loops. after(...) lets
        # TUTORIAL: the UI stay responsive.
        # TUTORIAL: ------------------------------------------------------------------------
        self.visual_anim_after_id = self.root.after(80, self._visual_animation_tick)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_visual_animation_tick belongs to the scrollable Morph
# TUTORIAL: UI, independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_visual_animation_tick(self) -> None:
    self.visual_anim_after_id = None
    playground_animated = bool(getattr(self, "playground_visualisers_visible", tk.BooleanVar(value=False)).get()) and str(getattr(self, "playground_visualizer_mode", tk.StringVar(value="")).get()).startswith("Animated")
    morph_animated = bool(getattr(self, "morph_visualisers_visible", tk.BooleanVar(value=False)).get()) and str(getattr(self, "morph_visualizer_mode", tk.StringVar(value="")).get()).startswith("Animated")
    if not (playground_animated or morph_animated):
        return
    self.visual_anim_phase = (float(getattr(self, "visual_anim_phase", 0.0)) + 0.018) % 1.0
    self._update_all_visualisers()
    self._schedule_visual_animation_if_needed()


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_update_morph_consequence belongs to the scrollable
# TUTORIAL: Morph UI, independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_update_morph_consequence(self) -> None:
    try:
        self._refresh_endpoint_unlocks()
        self._tabbed_update_note_label()
        summary = self._summary_from_ui()
        lines = [
            f"Requested: {summary.requested_value:g} {summary.requested_mode.lower()} → "
            f"{summary.sample_count} samples, {_format_seconds_compact(summary.duration_seconds)}, "
            f"{summary.nominal_cycles:.3f} nominal cycles, {summary.samples_per_cycle:.2f} samples/cycle."
        ]
        if summary.warnings:
            lines.append("Warnings: " + "; ".join(summary.warnings))
        if summary.suggestions:
            lines.append("Suggestions: " + "; ".join(summary.suggestions[:3]))
        self.consequence_var.set("\n".join(lines))
    except Exception as exc:
        if hasattr(self, "consequence_var"):
            self.consequence_var.set(f"Duration calculation error: {type(exc).__name__}: {exc}")


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_morph_changed belongs to the scrollable Morph UI,
# TUTORIAL: independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_morph_changed(self) -> None:
    if self._suppress:
        return
    self._update_morph_consequence()
    self._update_all_visualisers()
    self._schedule_visual_animation_if_needed()


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_morph_patch_changed belongs to the scrollable Morph
# TUTORIAL: UI, independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_morph_patch_changed(self, endpoint: str) -> None:
    if self._suppress:
        return
    endpoint = "end" if str(endpoint).lower().startswith("end") else "start"
    self.morph_drone_endpoint = endpoint
    self._morph_changed()
    if self._active_tab_name() == "START→END Morph Export":
        self.morph_transport_mode = "drone"
        self._push_morph_endpoint_to_audio(endpoint, select_tab=False)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_morph_shared_changed belongs to the scrollable Morph
# TUTORIAL: UI, independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_morph_shared_changed(self) -> None:
    if self._suppress:
        return
    self._morph_changed()
    if self._active_tab_name() == "START→END Morph Export":
        self.morph_transport_mode = "drone"
        self._push_morph_endpoint_to_audio(getattr(self, "morph_drone_endpoint", "start"), select_tab=False)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_push_patch_to_audio belongs to the scrollable Morph
# TUTORIAL: UI, independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_push_patch_to_audio(self, patch: PatchSettings, *, volume: float | None = None, wavetable_type: int | None = None) -> None:
    # Endpoint/playground audition uses the live oscillator, not the morph preview buffer.
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
    # TUTORIAL: callback may touch it at the same time.
    # TUTORIAL: ------------------------------------------------------------------------
    with self.state.lock:
        self.state.target_preview_buffer_active = False
        self.state.target_preview_buffer = None
        self.state.target_preview_buffer_pos = 0
    table, notes = _table_for_patch(patch, wavetable_type=wavetable_type)
    freq = clamp_float(_safe_float_from_var(self.frequency_var, self.default_frequency), MIN_FREQUENCY, MAX_FREQUENCY)
    vol = clamp_float(_safe_float_from_var(self.volume_var, 0.20) if volume is None else volume, 0.0, 1.0)
    fm_range = fm_range_label_to_cents(patch.fm_range_label)
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
    # TUTORIAL: callback may touch it at the same time.
    # TUTORIAL: ------------------------------------------------------------------------
    with self.state.lock:
        self.state.target_table = table
        self.state.target_volume = vol
        self.state.target_frequency = freq
        self.state.target_fm = patch.fm
        self.state.target_pwm = patch.pwm
        self.state.target_am = patch.am
        self.state.target_fm_range_cents = fm_range
        self.state.target_before_start = 0.0
        self.state.target_before_length = WINDOW_STEPS
        self.state.target_after_start = 0.0
        self.state.target_after_length = WINDOW_STEPS
        self.state.target_audio_gate = 1.0
        self.state.target_play_remaining_samples = None
        self.state.target_transport_mode = "drone"
        self.state.reset_requested = True
        sanitise_shared_state(self.state)
    suffix = ""
    if notes:
        suffix = "  Notes: " + "; ".join(sorted(set(notes))[:4])
    self.status.config(text=f"DRONE  frequency={_v3_compact_number(freq, max_decimals=6)} Hz volume={vol:.2f}" + suffix)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_push_playground_to_audio belongs to the scrollable
# TUTORIAL: Morph UI, independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_push_playground_to_audio(self) -> None:
    self.playground_transport_mode = "drone"
    self._push_patch_to_audio(self._patch_from_vars(self.single_vars))


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_push_morph_endpoint_to_audio belongs to the
# TUTORIAL: scrollable Morph UI, independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_push_morph_endpoint_to_audio(self, endpoint: str, *, select_tab: bool = True) -> None:
    endpoint = "end" if str(endpoint).lower().startswith("end") else "start"
    self.morph_drone_endpoint = endpoint
    self.morph_transport_mode = "drone"
    if select_tab:
        self.notebook.select(self.morph_tab)
    if endpoint == "end":
        patch = self._patch_from_vars(self.end_vars)
        patch.wavetable_type = self._effective_end_type()
        if not bool(self.different_fm_ranges_var.get()):
            patch.fm_range_label = str(self.shared_fm_range_var.get())
    else:
        patch = self._patch_from_vars(self.start_vars)
        patch.wavetable_type = self._effective_start_type()
        if not bool(self.different_fm_ranges_var.get()):
            patch.fm_range_label = str(self.shared_fm_range_var.get())
    self._push_patch_to_audio(patch, wavetable_type=patch.wavetable_type)
    self.status.config(text=f"DRONE {endpoint.upper()}  live endpoint audition")


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_drone_morph_endpoint belongs to the scrollable Morph
# TUTORIAL: UI, independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_drone_morph_endpoint(self, endpoint: str) -> None:
    self._push_morph_endpoint_to_audio(endpoint, select_tab=True)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_audition_morph_transition belongs to the scrollable
# TUTORIAL: Morph UI, independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_audition_morph_transition(self, reverse: bool = False) -> None:
    self.notebook.select(self.morph_tab)
    self.morph_transport_mode = "preview"
    summary = self._summary_from_ui()
    start = self._patch_from_vars(self.start_vars)
    end = self._patch_from_vars(self.end_vars)
    start.wavetable_type = self._effective_start_type()
    end.wavetable_type = self._effective_end_type()
    if not bool(self.different_fm_ranges_var.get()):
        start.fm_range_label = str(self.shared_fm_range_var.get())
        end.fm_range_label = str(self.shared_fm_range_var.get())
    try:
        audio, notes = _render_morph_audio(
            start,
            end,
            summary=summary,
            shared_type=None if bool(self.different_types_var.get()) else self._effective_start_type(),
            different_types=bool(self.different_types_var.get()),
            different_fm_ranges=bool(self.different_fm_ranges_var.get()),
            curve_amount=clamp_int(_safe_int_from_var(self.curve_amount_var, 127), 1, 255),
            curve_mode=str(self.curve_mode_var.get()),
            cycle_stepped=bool(self.cycle_stepped_var.get()),
            reverse=bool(reverse),
            dc_mode=str(self.dc_mode_var.get()),
            norm_mode=str(self.normalise_mode_var.get()),
            headroom_db=_safe_float_from_var(self.headroom_var, MORPH_HEADROOM_DEFAULT_DB),
        )
        vol = clamp_float(_safe_float_from_var(self.volume_var, 0.20), 0.0, 1.0)
        audio = np.asarray(audio, dtype=np.float32) * vol
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: SharedState is read or written under a lock because the GUI thread and audio
        # TUTORIAL: callback may touch it at the same time.
        # TUTORIAL: ------------------------------------------------------------------------
        with self.state.lock:
            self.state.target_preview_buffer = audio.astype(np.float32)
            self.state.target_preview_buffer_pos = 0
            self.state.target_preview_buffer_active = True
            self.state.target_audio_gate = 1.0
            self.state.target_play_remaining_samples = None
            self.state.target_transport_mode = "preview"
        direction = "END→START" if reverse else "START→END"
        suffix = ("  Notes: " + "; ".join(notes[:4])) if notes else ""
        self.status.config(text=f"AUDITION {direction}  {_format_seconds_compact(summary.duration_seconds)} buffer rendered and playing" + suffix)
    except Exception as exc:
        self.morph_transport_mode = "stopped"
        self.status.config(text=f"AUDITION ERROR  {type(exc).__name__}: {exc}")


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_playground_changed belongs to the scrollable Morph
# TUTORIAL: UI, independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_playground_changed(self) -> None:
    if self._suppress:
        return
    self._update_all_visualisers()
    self._schedule_visual_animation_if_needed()
    if self._active_tab_name() == "Single Engine / Playground":
        self.playground_transport_mode = "drone"
        self._push_playground_to_audio()


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_tab_changed belongs to the scrollable Morph UI,
# TUTORIAL: independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_tab_changed(self, _event=None) -> None:
    # Selecting a tab immediately mutes the other tab. Then restore only the
    # selected tab's remembered transport state.
    key = _v3_active_key(self)
    self._set_audio_stopped()
    if key == "playground":
        if getattr(self, "playground_transport_mode", "drone") == "drone":
            self._push_playground_to_audio()
        else:
            self.status.config(text="PLAYGROUND TAB ACTIVE  transport remains stopped")
    else:
        mode = getattr(self, "morph_transport_mode", "stopped")
        if mode == "drone":
            self._push_morph_endpoint_to_audio(getattr(self, "morph_drone_endpoint", "start"), select_tab=False)
        else:
            self.status.config(text="MORPH TAB ACTIVE  transport stopped; choose Drone START, Drone END, or audition a transition")
    self._schedule_visual_animation_if_needed()


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_reset_single belongs to the scrollable Morph UI,
# TUTORIAL: independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_reset_single(self) -> None:
    self._suppress = True
    try:
        defaults = self._new_patch_vars(PatchSettings())
        for key, var in self.single_vars.items():
            if key in defaults and isinstance(var, tk.Variable):
                var.set(defaults[key].get())
        self.frequency_var.set(self.default_frequency)
        self.volume_var.set(0.20)
    finally:
        self._suppress = False
    self.playground_transport_mode = "drone"
    self._playground_changed()


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_randomise_morph belongs to the scrollable Morph UI,
# TUTORIAL: independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_randomise_morph(self, which: str) -> None:
    if which in {"start", "both"}:
        self._random_patch_into(self.start_vars)
    if which in {"end", "both"}:
        self._random_patch_into(self.end_vars)
    ep = "end" if which == "end" else "start"
    self._morph_patch_changed(ep)
    self.status.config(text=f"RANDOMISE  {which.upper()} patch controls changed")


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_copy_start_to_end belongs to the scrollable Morph UI,
# TUTORIAL: independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_copy_start_to_end(self) -> None:
    self._copy_patch_vars(self.start_vars, self.end_vars)
    self._morph_patch_changed("end")


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_copy_end_to_start belongs to the scrollable Morph UI,
# TUTORIAL: independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_copy_end_to_start(self) -> None:
    self._copy_patch_vars(self.end_vars, self.start_vars)
    self._morph_patch_changed("start")


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_swap_start_end belongs to the scrollable Morph UI,
# TUTORIAL: independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_swap_start_end(self) -> None:
    tmp = {key: self.start_vars[key].get() for key in ("pot1", "pot2", "pot3", "wavetable_type", "fm", "pwm", "am", "before_start", "before_end", "after_start", "after_end", "fm_range_label")}
    self._copy_patch_vars(self.end_vars, self.start_vars)
    for key, value in tmp.items():
        self.end_vars[key].set(value)
    self._morph_patch_changed(getattr(self, "morph_drone_endpoint", "start"))


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_export_paths belongs to the scrollable Morph UI,
# TUTORIAL: independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_export_paths(self, base_path: str, direction: str) -> tuple[str, str]:
    root, ext = _os.path.splitext(base_path)
    if not ext:
        ext = ".wav"
    if direction == "reverse":
        wav_path = f"{root}_REVERSE{ext}"
    else:
        wav_path = f"{root}{ext}"
    json_path = _os.path.splitext(wav_path)[0] + ".json"
    return wav_path, json_path


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-3 override/helper: _v3_create_wav_clicked belongs to the scrollable Morph
# TUTORIAL: UI, independent visualisers, and tab-aware transport layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v3_create_wav_clicked(self) -> None:
    prefix = "fractal_wt_morph"
    try:
        prefix = str(self.filename_prefix_var.get()).strip() or prefix
    except Exception:
        pass
    # Keep the prefix filename-safe but recognisable.
    safe_prefix = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in prefix).strip("_") or "fractal_wt_morph"
    suggested = f"{safe_prefix}_{int(_time.time())}.wav"
    initialdir = getattr(self, "last_export_dir", _os.getcwd()) or _os.getcwd()
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: File dialogs are UI operations, so they happen outside the audio callback and
    # TUTORIAL: before/after offline rendering.
    # TUTORIAL: ------------------------------------------------------------------------
    chosen = _filedialog.asksaveasfilename(
        title="Create WAV + JSON",
        defaultextension=".wav",
        initialdir=initialdir,
        initialfile=suggested,
        filetypes=(("WAV audio", "*.wav"), ("All files", "*.*")),
    )
    if not chosen:
        self.status.config(text="EXPORT CANCELLED")
        return
    self.last_export_dir = _os.path.dirname(chosen) or initialdir
    base_path = chosen
    summary = self._summary_from_ui()
    start = self._patch_from_vars(self.start_vars)
    end = self._patch_from_vars(self.end_vars)
    start.wavetable_type = self._effective_start_type()
    end.wavetable_type = self._effective_end_type()
    if not bool(self.different_fm_ranges_var.get()):
        start.fm_range_label = str(self.shared_fm_range_var.get())
        end.fm_range_label = str(self.shared_fm_range_var.get())

    directions = ["forward", "reverse"] if str(self.direction_var.get()) == "Both" else (["reverse"] if str(self.direction_var.get()) == "END→START" else ["forward"])
    created: list[str] = []
    try:
        for direction in directions:
            reverse = direction == "reverse"
            audio, notes = _render_morph_audio(
                start,
                end,
                summary=summary,
                shared_type=None if bool(self.different_types_var.get()) else self._effective_start_type(),
                different_types=bool(self.different_types_var.get()),
                different_fm_ranges=bool(self.different_fm_ranges_var.get()),
                curve_amount=clamp_int(_safe_int_from_var(self.curve_amount_var, 127), 1, 255),
                curve_mode=str(self.curve_mode_var.get()),
                cycle_stepped=bool(self.cycle_stepped_var.get()),
                reverse=reverse,
                dc_mode=str(self.dc_mode_var.get()),
                norm_mode=str(self.normalise_mode_var.get()),
                headroom_db=_safe_float_from_var(self.headroom_var, MORPH_HEADROOM_DEFAULT_DB),
            )
            wav_path, json_path = self._export_paths(base_path, direction)
            _os.makedirs(_os.path.dirname(wav_path) or ".", exist_ok=True)
            _write_wav_mono(wav_path, audio, summary.sample_rate, summary.bit_depth)
            metadata = {
                "app": "Fractal Wavetable START-END Morph Export",
                "created_epoch": int(_time.time()),
                "direction": "END_TO_START" if reverse else "START_TO_END",
                "filename_prefix": safe_prefix,
                "summary": _asdict(summary),
                "start_patch": _asdict(start),
                "end_patch": _asdict(end),
                "transition": {
                    "curve_mode": str(self.curve_mode_var.get()),
                    "curve_amount": clamp_int(_safe_int_from_var(self.curve_amount_var, 127), 1, 255),
                    "cycle_stepped": bool(self.cycle_stepped_var.get()),
                    "different_wavetable_types": bool(self.different_types_var.get()),
                    "different_fm_ranges": bool(self.different_fm_ranges_var.get()),
                },
                "output_processing": {
                    "dc_removal": str(self.dc_mode_var.get()),
                    "normalise": str(self.normalise_mode_var.get()),
                    "headroom_db": _safe_float_from_var(self.headroom_var, MORPH_HEADROOM_DEFAULT_DB),
                },
                "render_notes": notes,
            }
            if str(summary.bit_depth).startswith("12"):
                metadata["bit_depth_note"] = "12-bit quantisation written in a 16-bit PCM WAV container"
            with open(json_path, "w", encoding="utf-8") as f:
                # TUTORIAL: ------------------------------------------------------------------------
                # TUTORIAL: JSON sidecars are written beside WAV files so settings can be
                # TUTORIAL: inspected, archived, and later imported.
                # TUTORIAL: ------------------------------------------------------------------------
                _json.dump(metadata, f, indent=2, sort_keys=True)
            created.extend([wav_path, json_path])
        self.status.config(text="EXPORT COMPLETE  " + "  ".join(_os.path.basename(x) for x in created))
    except Exception as exc:
        self.status.config(text=f"EXPORT ERROR  {type(exc).__name__}: {exc}")


# Apply v3 overrides.
TabbedFractalApp._tabbed_ensure_common_vars = _v3_ensure_common_vars
TabbedFractalApp._tabbed_update_note_label = _v3_update_note_label
TabbedFractalApp._tabbed_frequency_committed = _v3_frequency_committed
TabbedFractalApp._tabbed_note_changed = _v3_note_changed
TabbedFractalApp._add_frequency_note_row = _v3_add_frequency_note_row
TabbedFractalApp._add_transport_row = _v3_add_transport_row
TabbedFractalApp._set_audio_stopped = _v3_set_audio_stopped
TabbedFractalApp._set_transport_state = _v3_set_transport_state
TabbedFractalApp.drone_audio = _v3_drone_audio
TabbedFractalApp.stop_audio = _v3_stop_audio
TabbedFractalApp._add_visual_mode_controls = _v3_add_visual_mode_controls
TabbedFractalApp._visual_mode_changed = _v3_visual_mode_changed
TabbedFractalApp._build_playground_tab = _v3_build_playground_tab
TabbedFractalApp._make_scrollable_tab = _v3_make_scrollable_tab
TabbedFractalApp._build_morph_tab = _v3_build_morph_tab
TabbedFractalApp._build_endpoint_panel = _v3_build_endpoint_panel
TabbedFractalApp._build_morph_shared_controls = _v3_build_morph_shared_controls
TabbedFractalApp._toggle_playground_visualiser = _v3_toggle_playground_visualiser
TabbedFractalApp._toggle_morph_visualisers = _v3_toggle_morph_visualisers
TabbedFractalApp._toggle_visualisers = _v3_toggle_visualisers
TabbedFractalApp._draw_table = _v3_draw_table
TabbedFractalApp._update_all_visualisers = _v3_update_all_visualisers
TabbedFractalApp._schedule_visual_animation_if_needed = _v3_schedule_visual_animation_if_needed
TabbedFractalApp._visual_animation_tick = _v3_visual_animation_tick
TabbedFractalApp._update_morph_consequence = _v3_update_morph_consequence
TabbedFractalApp._morph_changed = _v3_morph_changed
TabbedFractalApp._morph_patch_changed = _v3_morph_patch_changed
TabbedFractalApp._morph_shared_changed = _v3_morph_shared_changed
TabbedFractalApp._push_patch_to_audio = _v3_push_patch_to_audio
TabbedFractalApp._push_playground_to_audio = _v3_push_playground_to_audio
TabbedFractalApp._push_morph_endpoint_to_audio = _v3_push_morph_endpoint_to_audio
TabbedFractalApp._drone_morph_endpoint = _v3_drone_morph_endpoint
TabbedFractalApp._audition_morph_transition = _v3_audition_morph_transition
TabbedFractalApp._playground_changed = _v3_playground_changed
TabbedFractalApp._tab_changed = _v3_tab_changed
TabbedFractalApp._reset_single = _v3_reset_single
TabbedFractalApp._randomise_morph = _v3_randomise_morph
TabbedFractalApp._copy_start_to_end = _v3_copy_start_to_end
TabbedFractalApp._copy_end_to_start = _v3_copy_end_to_start
TabbedFractalApp._swap_start_end = _v3_swap_start_end
TabbedFractalApp._export_paths = _v3_export_paths
TabbedFractalApp._create_wav_clicked = _v3_create_wav_clicked


# -----------------------------------------------------------------------------
# v4 morph transport / layout corrections
#
# - Morph START/END edits drone only the edited endpoint.
# - Morph shared edits never start audio from a stopped state.
# - Stop is now universal: Stop buttons, End, and Escape stop both tab memories.
# - Morph visualisers reopen in the same top-of-panel position.
# - Morph tab gets a volume slider.
# -----------------------------------------------------------------------------


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-4 override/helper: _v4_universal_stop_audio fixes transport, universal stop,
# TUTORIAL: and Morph-tab volume behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _v4_universal_stop_audio(self, _event=None):
    try:
        self.playground_transport_mode = "stopped"
        self.morph_transport_mode = "stopped"
    except Exception:
        pass
    try:
        self._set_audio_stopped(status="STOP  all drone/audition audio gated off; stream remains alive")
    except Exception:
        pass
    return "break"


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-4 override/helper: _v4_stop_audio fixes transport, universal stop, and
# TUTORIAL: Morph-tab volume behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _v4_stop_audio(self) -> None:
    self._universal_stop_audio()


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-4 override/helper: _v4_toggle_morph_visualisers fixes transport, universal
# TUTORIAL: stop, and Morph-tab volume behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _v4_toggle_morph_visualisers(self) -> None:
    visible = bool(self.morph_visualisers_visible.get())
    for vars_dict in (getattr(self, "start_vars", {}), getattr(self, "end_vars", {})):
        frame = vars_dict.get("_visual_frame") if isinstance(vars_dict, dict) else None
        if frame is not None:
            try:
                if visible:
                    # Reopen at the top of the endpoint panel, ahead of the
                    # controls, rather than re-adding at the bottom.
                    master = frame.master
                    siblings = [w for w in master.winfo_children() if w is not frame]
                    if siblings:
                        frame.pack(fill="x", padx=6, pady=(6, 0), before=siblings[0])
                    else:
                        frame.pack(fill="x", padx=6, pady=(6, 0))
                else:
                    frame.pack_forget()
            except Exception:
                pass
    self._update_all_visualisers()
    self._schedule_visual_animation_if_needed()


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-4 override/helper: _v4_morph_patch_changed fixes transport, universal stop,
# TUTORIAL: and Morph-tab volume behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _v4_morph_patch_changed(self, endpoint: str) -> None:
    if self._suppress:
        return
    endpoint = "end" if str(endpoint).lower().startswith("end") else "start"
    self.morph_drone_endpoint = endpoint
    self._morph_changed()
    if self._active_tab_name() == "START→END Morph Export":
        # Endpoint edit means audition that endpoint now, and only that endpoint.
        self.morph_transport_mode = "drone"
        self._push_morph_endpoint_to_audio(endpoint, select_tab=False)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-4 override/helper: _v4_morph_shared_changed fixes transport, universal stop,
# TUTORIAL: and Morph-tab volume behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _v4_morph_shared_changed(self) -> None:
    if self._suppress:
        return
    self._morph_changed()
    if self._active_tab_name() != "START→END Morph Export":
        return
    # Shared controls should not change transport state. If Morph is already
    # droning, keep the same endpoint droning with updated shared values. If it
    # is stopped or previewing a rendered transition, leave it alone.
    if getattr(self, "morph_transport_mode", "stopped") == "drone":
        self._push_morph_endpoint_to_audio(getattr(self, "morph_drone_endpoint", "start"), select_tab=False)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-4 override/helper: _v4_build_morph_shared_controls fixes transport,
# TUTORIAL: universal stop, and Morph-tab volume behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _v4_build_morph_shared_controls(self, parent) -> None:
    # Build the existing v3 shared controls, then add a Morph-tab volume row.
    _v3_build_morph_shared_controls(self, parent)
    try:
        row = tk.Frame(parent)
        # Put volume just before the consequence/status text when possible.
        children = parent.winfo_children()
        before_widget = children[-1] if children else None
        pack_kwargs = {"fill": "x", "padx": 8, "pady": 3}
        if before_widget is not None:
            pack_kwargs["before"] = before_widget
        row.pack(**pack_kwargs)
        tk.Label(row, text="Volume", width=15, anchor="w").pack(side="left")
        # TUTORIAL: ------------------------------------------------------------------------
        # TUTORIAL: Tk Scale is the slider widget. In this app, slider callbacks push new values
        # TUTORIAL: into patch or audio state.
        # TUTORIAL: ------------------------------------------------------------------------
        slider = tk.Scale(
            row,
            variable=self.volume_var,
            from_=0.0,
            to=1.0,
            resolution=0.01,
            orient="horizontal",
            showvalue=True,
            length=260,
            command=lambda _v: self._morph_shared_changed(),
        )
        slider.pack(side="left", fill="x", expand=True)
    except Exception:
        pass


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-4 override/helper: _v4_tabbed_init fixes transport, universal stop, and
# TUTORIAL: Morph-tab volume behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _v4_tabbed_init(self, *args, **kwargs):
    _v4_previous_tabbed_init(self, *args, **kwargs)
    try:
        self.root.bind_all("<Escape>", self._universal_stop_audio)
        self.root.bind_all("<End>", self._universal_stop_audio)
        self.root.bind_all("<KP_End>", self._universal_stop_audio)
    except Exception:
        pass


_v4_previous_tabbed_init = TabbedFractalApp.__init__
TabbedFractalApp._universal_stop_audio = _v4_universal_stop_audio
TabbedFractalApp.stop_audio = _v4_stop_audio
TabbedFractalApp._toggle_morph_visualisers = _v4_toggle_morph_visualisers
TabbedFractalApp._morph_patch_changed = _v4_morph_patch_changed
TabbedFractalApp._morph_shared_changed = _v4_morph_shared_changed
TabbedFractalApp._build_morph_shared_controls = _v4_build_morph_shared_controls
TabbedFractalApp.__init__ = _v4_tabbed_init


# -----------------------------------------------------------------------------
# v5 export metadata / shared-section layout corrections
#
# - Direction selector is now adjacent to Create WAV + JSON.
# - Morph Volume is rebuilt directly into the shared section so it is visible.
# - Filename prefix remains a prefix only; each save dialog gets a fresh current
#   timestamp. For "Both", two explicit save dialogs are used so file portals /
#   save widgets that grant permission per selected file can still write both.
# - JSON sidecars now include machine-readable patch/export data, including
#   effective wavetable type ids and FM range cents.
# -----------------------------------------------------------------------------


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-5 override/helper: _v5_safe_prefix_from_ui belongs to the richer JSON
# TUTORIAL: metadata and file-dialog export layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v5_safe_prefix_from_ui(self) -> str:
    prefix = "fractal_wt_morph"
    try:
        prefix = str(self.filename_prefix_var.get()).strip() or prefix
    except Exception:
        pass
    return "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in prefix).strip("_") or "fractal_wt_morph"


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-5 override/helper: _v5_direction_code belongs to the richer JSON metadata
# TUTORIAL: and file-dialog export layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v5_direction_code(direction: str) -> str:
    return "END_TO_START" if str(direction).lower() == "reverse" else "START_TO_END"


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-5 override/helper: _v5_direction_suffix belongs to the richer JSON metadata
# TUTORIAL: and file-dialog export layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v5_direction_suffix(direction: str) -> str:
    return "END_TO_START" if str(direction).lower() == "reverse" else "START_TO_END"


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-5 override/helper: _v5_wavetype_info belongs to the richer JSON metadata and
# TUTORIAL: file-dialog export layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v5_wavetype_info(wt_type: int) -> dict:
    wt = clamp_int(int(wt_type), 0, WAVETABLE_TYPE_MAX)
    name, desc = WAVETABLE_MODE_INFO.get(wt, (f"Type {wt}", "")) if "WAVETABLE_MODE_INFO" in globals() else (str(wt), "")
    return {
        "id": wt,
        "name": name,
        "description": desc,
        "option_label": wavetable_type_option(wt) if "wavetable_type_option" in globals() else str(wt),
    }


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-5 override/helper: _v5_fm_range_info belongs to the richer JSON metadata and
# TUTORIAL: file-dialog export layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v5_fm_range_info(label: str) -> dict:
    lab = str(label)
    cents = fm_range_label_to_cents(lab)
    return {
        "label": lab,
        "cents": cents,
        "octaves": cents / 1200.0,
    }


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-5 override/helper: _v5_patch_metadata belongs to the richer JSON metadata
# TUTORIAL: and file-dialog export layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v5_patch_metadata(patch: PatchSettings) -> dict:
    data = _asdict(patch)
    wt = clamp_int(int(data.get("wavetable_type", 0)), 0, WAVETABLE_TYPE_MAX)
    fm_label = str(data.get("fm_range_label", DEFAULT_FM_RANGE_LABEL))
    data["wavetable_type"] = wt
    data["wavetable_type_info"] = _v5_wavetype_info(wt)
    data["fm_range"] = _v5_fm_range_info(fm_label)
    # Keep the legacy label for readability, but add numeric fields for import.
    data["fm_range_cents"] = data["fm_range"]["cents"]
    return data


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-5 override/helper: _v5_current_patch_pair belongs to the richer JSON
# TUTORIAL: metadata and file-dialog export layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v5_current_patch_pair(self) -> tuple[PatchSettings, PatchSettings, PatchSettings, PatchSettings]:
    """Return raw START/END patches plus effective START/END patches."""
    raw_start = self._patch_from_vars(self.start_vars)
    raw_end = self._patch_from_vars(self.end_vars)
    start = self._patch_from_vars(self.start_vars)
    end = self._patch_from_vars(self.end_vars)
    start.wavetable_type = self._effective_start_type()
    end.wavetable_type = self._effective_end_type()
    if not bool(self.different_fm_ranges_var.get()):
        start.fm_range_label = str(self.shared_fm_range_var.get())
        end.fm_range_label = str(self.shared_fm_range_var.get())
    return raw_start, raw_end, start, end


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-5 override/helper: _v5_build_morph_shared_controls belongs to the richer
# TUTORIAL: JSON metadata and file-dialog export layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v5_build_morph_shared_controls(self, parent) -> None:
    self._tabbed_ensure_common_vars()
    self.shared_type_var = tk.IntVar(value=3)
    self.shared_type_option_var = tk.StringVar(value=wavetable_type_option(3))
    self.different_types_var = tk.BooleanVar(value=False)
    self.shared_fm_range_var = tk.StringVar(value=DEFAULT_FM_RANGE_LABEL)
    self.different_fm_ranges_var = tk.BooleanVar(value=False)
    self.duration_mode_var = tk.StringVar(value="Cycles")
    self.duration_value_var = tk.StringVar(value=str(MORPH_DEFAULT_CYCLES))
    self.sample_rate_var = tk.IntVar(value=self.sample_rate if int(self.sample_rate) in EXPORT_SAMPLE_RATES else DEFAULT_SAMPLE_RATE)
    self.bit_depth_var = tk.StringVar(value="24-bit PCM")
    self.curve_mode_var = tk.StringVar(value="Bend")
    self.curve_amount_var = tk.IntVar(value=127)
    self.cycle_stepped_var = tk.BooleanVar(value=True)
    self.direction_var = tk.StringVar(value="START→END")
    self.dc_mode_var = tk.StringVar(value="Per cycle")
    self.normalise_mode_var = tk.StringVar(value="Per cycle peak")
    self.headroom_var = tk.DoubleVar(value=MORPH_HEADROOM_DEFAULT_DB)
    self.filename_prefix_var = tk.StringVar(value="fractal_wt_morph")
    self.consequence_var = tk.StringVar(value="")

    freq_box = tk.Frame(parent)
    freq_box.pack(fill="x", padx=8, pady=3)
    self._add_frequency_note_row(freq_box, command=self._morph_shared_changed)

    line1 = tk.Frame(parent)
    line1.pack(fill="x", padx=8, pady=3)
    tk.Checkbutton(line1, text="Different START/END wavetable types", variable=self.different_types_var, command=self._morph_shared_changed).pack(side="left")
    tk.Label(line1, text="Shared type").pack(side="left", padx=(12, 4))
    shared_combo = _ttk.Combobox(line1, textvariable=self.shared_type_option_var, values=WT_OPTION_LIST, state="readonly", width=58)
    shared_combo.pack(side="left", fill="x", expand=True)

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function shared_type_changed: small named steps make the signal path, UI
    # TUTORIAL: path, and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def shared_type_changed(_event=None):
        self.shared_type_var.set(wavetable_type_from_option(self.shared_type_option_var.get()))
        self._morph_shared_changed()

    shared_combo.bind("<<ComboboxSelected>>", shared_type_changed)
    self.shared_type_combo = shared_combo

    line1b = tk.Frame(parent)
    line1b.pack(fill="x", padx=8, pady=3)
    tk.Label(line1b, text="Shared FM Range").pack(side="left")
    tk.OptionMenu(line1b, self.shared_fm_range_var, *[label for label, _c in FM_RANGE_OPTIONS], command=lambda _v: self._morph_shared_changed()).pack(side="left", padx=(6, 0))
    tk.Checkbutton(line1b, text="Different START/END FM ranges", variable=self.different_fm_ranges_var, command=self._morph_shared_changed).pack(side="left", padx=(12, 0))

    duration_line = tk.Frame(parent)
    duration_line.pack(fill="x", padx=8, pady=3)
    tk.Label(duration_line, text="Duration").pack(side="left")
    tk.OptionMenu(duration_line, self.duration_mode_var, "Seconds", "Cycles", "Samples", command=lambda _v: self._morph_shared_changed()).pack(side="left", padx=(6, 0))
    duration_entry = tk.Entry(duration_line, textvariable=self.duration_value_var, width=10, justify="right")
    duration_entry.pack(side="left", padx=(6, 0))
    duration_entry.bind("<Return>", lambda _e: self._morph_shared_changed())
    duration_entry.bind("<FocusOut>", lambda _e: self._morph_shared_changed())
    tk.Checkbutton(duration_line, text="Cycle-stepped transition", variable=self.cycle_stepped_var, command=self._morph_shared_changed).pack(side="left", padx=(14, 0))
    tk.Label(duration_line, text="Curve").pack(side="left", padx=(14, 4))
    tk.OptionMenu(duration_line, self.curve_mode_var, "Bend", "S-curve", command=lambda _v: self._morph_shared_changed()).pack(side="left")
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Tk Scale is the slider widget. In this app, slider callbacks push new values
    # TUTORIAL: into patch or audio state.
    # TUTORIAL: ------------------------------------------------------------------------
    tk.Scale(duration_line, variable=self.curve_amount_var, from_=1, to=255, resolution=1, orient="horizontal", length=180, command=lambda _v: self._morph_shared_changed()).pack(side="left", padx=(6, 0))

    output_line = tk.Frame(parent)
    output_line.pack(fill="x", padx=8, pady=3)
    tk.Label(output_line, text="Sample rate").pack(side="left")
    tk.OptionMenu(output_line, self.sample_rate_var, *EXPORT_SAMPLE_RATES, command=lambda _v: self._morph_shared_changed()).pack(side="left", padx=(6, 0))
    tk.Label(output_line, text="Bit depth").pack(side="left", padx=(14, 4))
    tk.OptionMenu(output_line, self.bit_depth_var, *EXPORT_BIT_DEPTHS, command=lambda _v: self._morph_shared_changed()).pack(side="left")
    tk.Label(output_line, text="DC removal").pack(side="left", padx=(14, 4))
    tk.OptionMenu(output_line, self.dc_mode_var, "Off", "Whole file", "Per cycle", command=lambda _v: self._morph_shared_changed()).pack(side="left")
    tk.Label(output_line, text="Normalise").pack(side="left", padx=(14, 4))
    tk.OptionMenu(output_line, self.normalise_mode_var, "Off", "Whole file peak", "Per cycle peak", command=lambda _v: self._morph_shared_changed()).pack(side="left")
    tk.Label(output_line, text="Headroom dB").pack(side="left", padx=(14, 4))
    tk.Entry(output_line, textvariable=self.headroom_var, width=7, justify="right").pack(side="left")

    volume_line = tk.Frame(parent)
    volume_line.pack(fill="x", padx=8, pady=3)
    tk.Label(volume_line, text="Volume", width=15, anchor="w").pack(side="left")
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Tk Scale is the slider widget. In this app, slider callbacks push new values
    # TUTORIAL: into patch or audio state.
    # TUTORIAL: ------------------------------------------------------------------------
    tk.Scale(
        volume_line,
        variable=self.volume_var,
        from_=0.0,
        to=1.0,
        resolution=0.01,
        orient="horizontal",
        showvalue=True,
        length=320,
        command=lambda _v: self._morph_shared_changed(),
    ).pack(side="left", fill="x", expand=True)

    filename_line = tk.Frame(parent)
    filename_line.pack(fill="x", padx=8, pady=3)
    tk.Label(filename_line, text="Filename prefix").pack(side="left")
    tk.Entry(filename_line, textvariable=self.filename_prefix_var, width=32).pack(side="left", padx=(6, 0))
    tk.Label(filename_line, text="Save dialog uses a fresh current timestamp each export.").pack(side="left", padx=(10, 0))

    action_line = tk.Frame(parent)
    action_line.pack(fill="x", padx=8, pady=3)
    tk.Button(action_line, text="Drone START", command=lambda: self._drone_morph_endpoint("start")).pack(side="left")
    tk.Button(action_line, text="Drone END", command=lambda: self._drone_morph_endpoint("end")).pack(side="left", padx=(6, 0))
    tk.Button(action_line, text="Stop", command=self.stop_audio).pack(side="left", padx=(6, 0))
    tk.Button(action_line, text="Audition START→END", command=lambda: self._audition_morph_transition(False)).pack(side="left", padx=(18, 0))
    tk.Button(action_line, text="Audition END→START", command=lambda: self._audition_morph_transition(True)).pack(side="left", padx=(6, 0))

    export_group = tk.Frame(action_line)
    export_group.pack(side="right")
    tk.Label(export_group, text="Render").pack(side="left", padx=(0, 4))
    tk.OptionMenu(export_group, self.direction_var, "START→END", "END→START", "Both", command=lambda _v: self._morph_shared_changed()).pack(side="left", padx=(0, 8))
    tk.Button(export_group, text="Create WAV + JSON", command=self._create_wav_clicked).pack(side="left")

    tk.Label(parent, textvariable=self.consequence_var, anchor="w", justify="left", wraplength=1600).pack(fill="x", padx=8, pady=(2, 6))
    self._morph_changed()


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-5 override/helper: _v5_collect_export_targets belongs to the richer JSON
# TUTORIAL: metadata and file-dialog export layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v5_collect_export_targets(self, directions: list[str], safe_prefix: str) -> dict[str, str] | None:
    """Ask for output path(s). Both mode uses two explicit save dialogs."""
    initialdir = getattr(self, "last_export_dir", _os.getcwd()) or _os.getcwd()
    stamp = int(_time.time())
    targets: dict[str, str] = {}

    if directions == ["forward", "reverse"]:
        # Some file-dialog/portal implementations grant write permission only to
        # the exact selected file. Use two explicit file selections for Both.
        defaults = [
            ("forward", f"{safe_prefix}_{stamp}_START_TO_END.wav", "Create START→END WAV + JSON"),
            ("reverse", f"{safe_prefix}_{stamp}_END_TO_START.wav", "Create END→START WAV + JSON"),
        ]
        for direction, initialfile, title in defaults:
            # TUTORIAL: ------------------------------------------------------------------------
            # TUTORIAL: File dialogs are UI operations, so they happen outside the audio
            # TUTORIAL: callback and before/after offline rendering.
            # TUTORIAL: ------------------------------------------------------------------------
            chosen = _filedialog.asksaveasfilename(
                title=title,
                defaultextension=".wav",
                initialdir=initialdir,
                initialfile=initialfile,
                filetypes=(("WAV audio", "*.wav"), ("All files", "*.*")),
            )
            if not chosen:
                self.status.config(text="EXPORT CANCELLED")
                return None
            targets[direction] = chosen
            initialdir = _os.path.dirname(chosen) or initialdir
        self.last_export_dir = initialdir
        return targets

    direction = directions[0]
    suffix = _v5_direction_suffix(direction)
    suggested = f"{safe_prefix}_{stamp}_{suffix}.wav"
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: File dialogs are UI operations, so they happen outside the audio callback and
    # TUTORIAL: before/after offline rendering.
    # TUTORIAL: ------------------------------------------------------------------------
    chosen = _filedialog.asksaveasfilename(
        title="Create WAV + JSON",
        defaultextension=".wav",
        initialdir=initialdir,
        initialfile=suggested,
        filetypes=(("WAV audio", "*.wav"), ("All files", "*.*")),
    )
    if not chosen:
        self.status.config(text="EXPORT CANCELLED")
        return None
    self.last_export_dir = _os.path.dirname(chosen) or initialdir
    targets[direction] = chosen
    return targets


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-5 override/helper: _v5_create_metadata belongs to the richer JSON metadata
# TUTORIAL: and file-dialog export layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v5_create_metadata(
    self,
    *,
    safe_prefix: str,
    direction: str,
    reverse: bool,
    wav_path: str,
    json_path: str,
    summary: RenderSummary,
    raw_start: PatchSettings,
    raw_end: PatchSettings,
    start: PatchSettings,
    end: PatchSettings,
    notes: list[str],
) -> dict:
    shared_type_id = self._effective_start_type() if not bool(self.different_types_var.get()) else None
    metadata = {
        "schema_version": 2,
        "app": "Fractal Wavetable START-END Morph Export",
        "created_epoch": int(_time.time()),
        "created_local_time": _time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "direction": _v5_direction_code(direction),
        "reverse": bool(reverse),
        "files": {
            "wav": _os.path.basename(wav_path),
            "json": _os.path.basename(json_path),
            "wav_path": wav_path,
            "json_path": json_path,
            "filename_prefix": safe_prefix,
        },
        "summary": _asdict(summary),
        "shared_settings": {
            "frequency_hz": float(summary.frequency),
            "volume": clamp_float(_safe_float_from_var(self.volume_var, 0.20), 0.0, 1.0),
            "wavetable_type_is_shared": not bool(self.different_types_var.get()),
            "shared_wavetable_type": _v5_wavetype_info(shared_type_id) if shared_type_id is not None else None,
            "fm_range_is_shared": not bool(self.different_fm_ranges_var.get()),
            "shared_fm_range": _v5_fm_range_info(str(self.shared_fm_range_var.get())),
        },
        "patches": {
            "start_raw": _v5_patch_metadata(raw_start),
            "end_raw": _v5_patch_metadata(raw_end),
            "start_effective": _v5_patch_metadata(start),
            "end_effective": _v5_patch_metadata(end),
        },
        # Keep these names for backward readability/import convenience.
        "start_patch": _v5_patch_metadata(start),
        "end_patch": _v5_patch_metadata(end),
        "transition": {
            "curve_mode": str(self.curve_mode_var.get()),
            "curve_amount": clamp_int(_safe_int_from_var(self.curve_amount_var, 127), 1, 255),
            "cycle_stepped": bool(self.cycle_stepped_var.get()),
            "different_wavetable_types": bool(self.different_types_var.get()),
            "different_fm_ranges": bool(self.different_fm_ranges_var.get()),
            "wavetable_type_transition": "curve_crossfade" if bool(self.different_types_var.get()) else "shared_type",
        },
        "output_processing": {
            "dc_removal": str(self.dc_mode_var.get()),
            "normalise": str(self.normalise_mode_var.get()),
            "headroom_db": _safe_float_from_var(self.headroom_var, MORPH_HEADROOM_DEFAULT_DB),
            "bit_depth": str(summary.bit_depth),
            "sample_rate": int(summary.sample_rate),
        },
        "render_notes": list(notes),
    }
    if str(summary.bit_depth).startswith("12"):
        metadata["bit_depth_note"] = "12-bit quantisation written in a 16-bit PCM WAV container"
    return metadata


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-5 override/helper: _v5_create_wav_clicked belongs to the richer JSON
# TUTORIAL: metadata and file-dialog export layer.
# TUTORIAL: ------------------------------------------------------------------------
def _v5_create_wav_clicked(self) -> None:
    safe_prefix = _v5_safe_prefix_from_ui(self)
    direction_selection = str(self.direction_var.get())
    directions = ["forward", "reverse"] if direction_selection == "Both" else (["reverse"] if direction_selection == "END→START" else ["forward"])
    targets = _v5_collect_export_targets(self, directions, safe_prefix)
    if targets is None:
        return

    summary = self._summary_from_ui()
    raw_start, raw_end, start, end = _v5_current_patch_pair(self)
    created: list[str] = []
    try:
        for direction in directions:
            reverse = direction == "reverse"
            audio, notes = _render_morph_audio(
                start,
                end,
                summary=summary,
                shared_type=None if bool(self.different_types_var.get()) else self._effective_start_type(),
                different_types=bool(self.different_types_var.get()),
                different_fm_ranges=bool(self.different_fm_ranges_var.get()),
                curve_amount=clamp_int(_safe_int_from_var(self.curve_amount_var, 127), 1, 255),
                curve_mode=str(self.curve_mode_var.get()),
                cycle_stepped=bool(self.cycle_stepped_var.get()),
                reverse=reverse,
                dc_mode=str(self.dc_mode_var.get()),
                norm_mode=str(self.normalise_mode_var.get()),
                headroom_db=_safe_float_from_var(self.headroom_var, MORPH_HEADROOM_DEFAULT_DB),
            )
            wav_path = targets[direction]
            if not _os.path.splitext(wav_path)[1]:
                wav_path += ".wav"
            json_path = _os.path.splitext(wav_path)[0] + ".json"
            _os.makedirs(_os.path.dirname(wav_path) or ".", exist_ok=True)
            _write_wav_mono(wav_path, audio, summary.sample_rate, summary.bit_depth)
            metadata = _v5_create_metadata(
                self,
                safe_prefix=safe_prefix,
                direction=direction,
                reverse=reverse,
                wav_path=wav_path,
                json_path=json_path,
                summary=summary,
                raw_start=raw_start,
                raw_end=raw_end,
                start=start,
                end=end,
                notes=notes,
            )
            with open(json_path, "w", encoding="utf-8") as f:
                # TUTORIAL: ------------------------------------------------------------------------
                # TUTORIAL: JSON sidecars are written beside WAV files so settings can be
                # TUTORIAL: inspected, archived, and later imported.
                # TUTORIAL: ------------------------------------------------------------------------
                _json.dump(metadata, f, indent=2, sort_keys=True)
            created.extend([wav_path, json_path])
        self.status.config(text="EXPORT COMPLETE  " + "  ".join(_os.path.basename(x) for x in created))
    except Exception as exc:
        self.status.config(text=f"EXPORT ERROR  {type(exc).__name__}: {exc}")


# Apply v5 overrides.
TabbedFractalApp._build_morph_shared_controls = _v5_build_morph_shared_controls
TabbedFractalApp._create_wav_clicked = _v5_create_wav_clicked


# -----------------------------------------------------------------------------
# v6 schema / export UI / bulk generation pass
#
# - Main JSON patch fields use descriptive source/result names while preserving
#   legacy internal before/after fields in a nested section.
# - Schema version is documented inside every JSON sidecar.
# - Morph export direction selector is replaced by three explicit export buttons.
# - Shared Wavetable Type and Shared FM Pitch Range rows are laid out
#   consistently with their START/END override checkboxes.
# - A third Bulk File Generation tab exports random morphs without hand-editing
#   START/END controls.
# -----------------------------------------------------------------------------

SCHEMA_VERSION = 3
SCHEMA_NAME = "fractal_wavetable_morph_sidecar"
BULK_MAX_EXPORTS = 100000


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-6 override/helper: _v6_title_case_ui_text belongs to the
# TUTORIAL: schema/export/bulk-generation layer.
# TUTORIAL: It often wraps or replaces earlier tabbed export behaviour without deleting the old
# TUTORIAL: code.
# TUTORIAL: ------------------------------------------------------------------------
def _v6_title_case_ui_text(text: str) -> str:
    """Capitalise ordinary UI words while preserving technical tokens."""
    if not isinstance(text, str) or not text:
        return text
    preserve = {
        "START", "END", "FM", "PWM", "AM", "DC", "WAV", "JSON", "PCM",
        "Hz", "dB", "UI", "ESC", "CPU", "START→END", "END→START",
        "START/END", "3-Step", "9-Step", "8-Bit", "12-Bit", "16-Bit",
        "24-Bit", "32-Bit", "96k", "192k", "Both",
    }
    out = []
    for word in text.split(" "):
        if not word:
            out.append(word)
            continue
        stripped = word.strip()
        # Preserve arrow labels and all-uppercase synth/audio abbreviations.
        if stripped in preserve or "→" in stripped or stripped.upper() in preserve:
            out.append(word)
            continue
        if "/" in word:
            parts = word.split("/")
            out.append("/".join(p if p.upper() in preserve else (p[:1].upper() + p[1:] if p else p) for p in parts))
            continue
        # Preserve leading punctuation but title-case the first alphabetic char.
        idx = 0
        while idx < len(word) and not word[idx].isalnum():
            idx += 1
        if idx < len(word):
            out.append(word[:idx] + word[idx:idx+1].upper() + word[idx+1:])
        else:
            out.append(word)
    return " ".join(out)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-6 override/helper: _v6_capitalise_widget_tree belongs to the
# TUTORIAL: schema/export/bulk-generation layer.
# TUTORIAL: It often wraps or replaces earlier tabbed export behaviour without deleting the old
# TUTORIAL: code.
# TUTORIAL: ------------------------------------------------------------------------
def _v6_capitalise_widget_tree(widget) -> None:
    try:
        text = widget.cget("text")
        if isinstance(text, str) and text:
            widget.configure(text=_v6_title_case_ui_text(text))
    except Exception:
        pass
    try:
        for child in widget.winfo_children():
            _v6_capitalise_widget_tree(child)
    except Exception:
        pass


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-6 override/helper: _v6_bit_depth_info belongs to the
# TUTORIAL: schema/export/bulk-generation layer.
# TUTORIAL: It often wraps or replaces earlier tabbed export behaviour without deleting the old
# TUTORIAL: code.
# TUTORIAL: ------------------------------------------------------------------------
def _v6_bit_depth_info(bit_depth: str) -> dict:
    label = str(bit_depth)
    if label.startswith("8"):
        return {"label": label, "bits": 8, "wav_container_bits": 8, "sample_format": "pcm_u8"}
    if label.startswith("12"):
        return {"label": label, "bits": 12, "wav_container_bits": 16, "sample_format": "pcm_s12_quantised_in_s16_container"}
    if label.startswith("16"):
        return {"label": label, "bits": 16, "wav_container_bits": 16, "sample_format": "pcm_s16"}
    if label.startswith("24"):
        return {"label": label, "bits": 24, "wav_container_bits": 24, "sample_format": "pcm_s24"}
    return {"label": label, "bits": 32, "wav_container_bits": 32, "sample_format": "pcm_s32"}


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-6 override/helper: _v6_schema_documentation belongs to the
# TUTORIAL: schema/export/bulk-generation layer.
# TUTORIAL: It often wraps or replaces earlier tabbed export behaviour without deleting the old
# TUTORIAL: code.
# TUTORIAL: ------------------------------------------------------------------------
def _v6_schema_documentation() -> dict:
    return {
        "name": SCHEMA_NAME,
        "version": SCHEMA_VERSION,
        "description": "Sidecar metadata for a Fractal Wavetable START/END morph WAV export.",
        "compatibility": {
            "introduced_in": "fractal_wt_start_end_morph_tabs_v6.py",
            "legacy_internal_fields_preserved": True,
        },
        "patch_field_notes": {
            "core_seed": "User-facing 12-bit Core Seed control, internally pot1.",
            "growth_3_step": "User-facing 3-Step Growth control, internally pot2.",
            "growth_9_step": "User-facing 9-Step Growth control, internally pot3.",
            "wavetable_type": "Integer wavetable mode ID.",
            "fm_range.cents": "Machine-readable FM pitch range in cents.",
            "source_start/source_end": "User-facing Source Window values on the 0..4096 window scale. Export v6 maps these from the internal after_start/after_end fields, per current JSON naming convention.",
            "result_start/result_end": "User-facing Result Window values on the 0..4096 window scale. Export v6 maps these from the internal before_start/before_end fields, per current JSON naming convention.",
            "legacy_internal": "Original internal field names retained to make future importers backwards-compatible with the generator code.",
        },
        "direction_codes": {
            "START_TO_END": "Render morph position from START to END.",
            "END_TO_START": "Render morph position from END to START.",
        },
        "curve_amount": {
            "range": [1, 255],
            "linear": 127,
            "low_values": "Slow start, faster finish.",
            "high_values": "Fast start, slower finish.",
        },
    }


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-6 override/helper: _v6_patch_metadata belongs to the
# TUTORIAL: schema/export/bulk-generation layer.
# TUTORIAL: It often wraps or replaces earlier tabbed export behaviour without deleting the old
# TUTORIAL: code.
# TUTORIAL: ------------------------------------------------------------------------
def _v6_patch_metadata(patch: PatchSettings) -> dict:
    wt = clamp_int(int(patch.wavetable_type), 0, WAVETABLE_TYPE_MAX)
    fm_info = _v5_fm_range_info(str(patch.fm_range_label))
    # Main exported names are descriptive and import-friendly. The legacy block
    # keeps the generator's older before/after names available for round-trips.
    return {
        "core_seed": int(patch.pot1),
        "growth_3_step": int(patch.pot2),
        "growth_9_step": int(patch.pot3),
        "wavetable_type": wt,
        "wavetable_type_info": _v5_wavetype_info(wt),
        "fm": int(patch.fm),
        "pwm": int(patch.pwm),
        "am": int(patch.am),
        "fm_range": fm_info,
        "fm_range_label": fm_info["label"],
        "fm_range_cents": fm_info["cents"],
        "source_start": int(patch.after_start),
        "source_end": int(patch.after_end),
        "result_start": int(patch.before_start),
        "result_end": int(patch.before_end),
        "window_scale": {"min": 0, "max": int(WINDOW_STEPS)},
        "legacy_internal": {
            "pot1": int(patch.pot1),
            "pot2": int(patch.pot2),
            "pot3": int(patch.pot3),
            "before_start": int(patch.before_start),
            "before_end": int(patch.before_end),
            "after_start": int(patch.after_start),
            "after_end": int(patch.after_end),
            "fm_range_label": str(patch.fm_range_label),
        },
    }


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-6 override/helper: _v6_shared_settings_metadata belongs to the
# TUTORIAL: schema/export/bulk-generation layer.
# TUTORIAL: It often wraps or replaces earlier tabbed export behaviour without deleting the old
# TUTORIAL: code.
# TUTORIAL: ------------------------------------------------------------------------
def _v6_shared_settings_metadata(self, summary: RenderSummary) -> dict:
    shared_type_id = self._effective_start_type() if not bool(self.different_types_var.get()) else None
    shared_fm = _v5_fm_range_info(str(self.shared_fm_range_var.get()))
    return {
        "frequency_hz": float(summary.frequency),
        "volume": clamp_float(_safe_float_from_var(self.volume_var, 0.20), 0.0, 1.0),
        "wavetable_type_is_shared": not bool(self.different_types_var.get()),
        "shared_wavetable_type": _v5_wavetype_info(shared_type_id) if shared_type_id is not None else None,
        "fm_pitch_range_is_shared": not bool(self.different_fm_ranges_var.get()),
        "shared_fm_pitch_range": shared_fm,
        "shared_fm_range": shared_fm,
    }


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Builds the JSON sidecar. This is the export provenance record: it should contain
# TUTORIAL: enough machine-readable information to recreate or import the patch later.
# TUTORIAL: ------------------------------------------------------------------------
def _v6_create_metadata(
    self,
    *,
    safe_prefix: str,
    direction: str,
    reverse: bool,
    wav_path: str,
    json_path: str,
    summary: RenderSummary,
    raw_start: PatchSettings,
    raw_end: PatchSettings,
    start: PatchSettings,
    end: PatchSettings,
    notes: list[str],
    bulk: dict | None = None,
) -> dict:
    direction_code = _v5_direction_code(direction)
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "schema": _v6_schema_documentation(),
        "app": "Fractal Wavetable START-END Morph Export",
        "app_file": "fractal_wt_start_end_morph_tabs_v6.py",
        "created_epoch": int(_time.time()),
        "created_local_time": _time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "direction": direction_code,
        "reverse": bool(reverse),
        "files": {
            "wav": _os.path.basename(wav_path),
            "json": _os.path.basename(json_path),
            "wav_path": wav_path,
            "json_path": json_path,
            "filename_prefix": safe_prefix,
        },
        "summary": {
            **_asdict(summary),
            "duration_mode_code": str(summary.requested_mode).lower().replace(" ", "_"),
            "bit_depth_info": _v6_bit_depth_info(str(summary.bit_depth)),
        },
        "shared_settings": _v6_shared_settings_metadata(self, summary),
        "patches": {
            "start_raw": _v6_patch_metadata(raw_start),
            "end_raw": _v6_patch_metadata(raw_end),
            "start_effective": _v6_patch_metadata(start),
            "end_effective": _v6_patch_metadata(end),
        },
        "start_patch": _v6_patch_metadata(start),
        "end_patch": _v6_patch_metadata(end),
        "transition": {
            "curve_mode": str(self.curve_mode_var.get()),
            "curve_amount": clamp_int(_safe_int_from_var(self.curve_amount_var, 127), 1, 255),
            "curve_amount_range": [1, 255],
            "curve_amount_linear": 127,
            "cycle_stepped": bool(self.cycle_stepped_var.get()),
            "different_wavetable_types": bool(self.different_types_var.get()),
            "different_fm_pitch_ranges": bool(self.different_fm_ranges_var.get()),
            "wavetable_type_transition": "curve_crossfade" if bool(self.different_types_var.get()) else "shared_type",
        },
        "output_processing": {
            "dc_removal": str(self.dc_mode_var.get()),
            "normalise": str(self.normalise_mode_var.get()),
            "headroom_db": _safe_float_from_var(self.headroom_var, MORPH_HEADROOM_DEFAULT_DB),
            "bit_depth": str(summary.bit_depth),
            "bit_depth_info": _v6_bit_depth_info(str(summary.bit_depth)),
            "sample_rate": int(summary.sample_rate),
        },
        "render_notes": list(notes),
    }
    if bulk is not None:
        metadata["bulk_generation"] = dict(bulk)
    if str(summary.bit_depth).startswith("12"):
        metadata["bit_depth_note"] = "12-bit quantisation written in a 16-bit PCM WAV container"
    return metadata


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-6 override/helper: _v6_build_morph_shared_controls belongs to the
# TUTORIAL: schema/export/bulk-generation layer.
# TUTORIAL: It often wraps or replaces earlier tabbed export behaviour without deleting the old
# TUTORIAL: code.
# TUTORIAL: ------------------------------------------------------------------------
def _v6_build_morph_shared_controls(self, parent) -> None:
    self._tabbed_ensure_common_vars()
    self.shared_type_var = tk.IntVar(value=3)
    self.shared_type_option_var = tk.StringVar(value=wavetable_type_option(3))
    self.different_types_var = tk.BooleanVar(value=False)
    self.shared_fm_range_var = tk.StringVar(value=DEFAULT_FM_RANGE_LABEL)
    self.different_fm_ranges_var = tk.BooleanVar(value=False)
    self.duration_mode_var = tk.StringVar(value="Cycles")
    self.duration_value_var = tk.StringVar(value=str(MORPH_DEFAULT_CYCLES))
    self.sample_rate_var = tk.IntVar(value=self.sample_rate if int(self.sample_rate) in EXPORT_SAMPLE_RATES else DEFAULT_SAMPLE_RATE)
    self.bit_depth_var = tk.StringVar(value="24-bit PCM")
    self.curve_mode_var = tk.StringVar(value="Bend")
    self.curve_amount_var = tk.IntVar(value=127)
    self.cycle_stepped_var = tk.BooleanVar(value=True)
    self.direction_var = tk.StringVar(value="START→END")
    self.dc_mode_var = tk.StringVar(value="Per cycle")
    self.normalise_mode_var = tk.StringVar(value="Per cycle peak")
    self.headroom_var = tk.DoubleVar(value=MORPH_HEADROOM_DEFAULT_DB)
    self.filename_prefix_var = tk.StringVar(value="fractal_wt_morph")
    self.consequence_var = tk.StringVar(value="")

    freq_box = tk.Frame(parent)
    freq_box.pack(fill="x", padx=8, pady=3)
    self._add_frequency_note_row(freq_box, command=self._morph_shared_changed)

    line1 = tk.Frame(parent)
    line1.pack(fill="x", padx=8, pady=3)
    tk.Checkbutton(line1, text="Different START/END Wavetable Types", variable=self.different_types_var, command=self._morph_shared_changed).pack(side="left")
    tk.Label(line1, text="Shared Wavetable Type").pack(side="left", padx=(18, 6))
    shared_combo = _ttk.Combobox(line1, textvariable=self.shared_type_option_var, values=WT_OPTION_LIST, state="readonly", width=58)
    shared_combo.pack(side="left", fill="x", expand=True)

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function shared_type_changed: small named steps make the signal path, UI
    # TUTORIAL: path, and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def shared_type_changed(_event=None):
        self.shared_type_var.set(wavetable_type_from_option(self.shared_type_option_var.get()))
        self._morph_shared_changed()

    shared_combo.bind("<<ComboboxSelected>>", shared_type_changed)
    self.shared_type_combo = shared_combo

    line2 = tk.Frame(parent)
    line2.pack(fill="x", padx=8, pady=3)
    tk.Checkbutton(line2, text="Different START/END FM Pitch Range", variable=self.different_fm_ranges_var, command=self._morph_shared_changed).pack(side="left")
    tk.Label(line2, text="Shared FM Pitch Range").pack(side="left", padx=(18, 6))
    tk.OptionMenu(line2, self.shared_fm_range_var, *[label for label, _c in FM_RANGE_OPTIONS], command=lambda _v: self._morph_shared_changed()).pack(side="left")

    duration_line = tk.Frame(parent)
    duration_line.pack(fill="x", padx=8, pady=3)
    tk.Label(duration_line, text="Duration").pack(side="left")
    tk.OptionMenu(duration_line, self.duration_mode_var, "Seconds", "Cycles", "Samples", command=lambda _v: self._morph_shared_changed()).pack(side="left", padx=(6, 0))
    duration_entry = tk.Entry(duration_line, textvariable=self.duration_value_var, width=10, justify="right")
    duration_entry.pack(side="left", padx=(6, 0))
    duration_entry.bind("<Return>", lambda _e: self._morph_shared_changed())
    duration_entry.bind("<FocusOut>", lambda _e: self._morph_shared_changed())
    tk.Checkbutton(duration_line, text="Cycle-Stepped Transition", variable=self.cycle_stepped_var, command=self._morph_shared_changed).pack(side="left", padx=(14, 0))
    tk.Label(duration_line, text="Curve").pack(side="left", padx=(14, 4))
    tk.OptionMenu(duration_line, self.curve_mode_var, "Bend", "S-Curve", command=lambda _v: self._morph_shared_changed()).pack(side="left")
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Tk Scale is the slider widget. In this app, slider callbacks push new values
    # TUTORIAL: into patch or audio state.
    # TUTORIAL: ------------------------------------------------------------------------
    tk.Scale(duration_line, variable=self.curve_amount_var, from_=1, to=255, resolution=1, orient="horizontal", length=180, command=lambda _v: self._morph_shared_changed()).pack(side="left", padx=(6, 0))

    output_line = tk.Frame(parent)
    output_line.pack(fill="x", padx=8, pady=3)
    tk.Label(output_line, text="Sample Rate").pack(side="left")
    tk.OptionMenu(output_line, self.sample_rate_var, *EXPORT_SAMPLE_RATES, command=lambda _v: self._morph_shared_changed()).pack(side="left", padx=(6, 0))
    tk.Label(output_line, text="Bit Depth").pack(side="left", padx=(14, 4))
    tk.OptionMenu(output_line, self.bit_depth_var, *EXPORT_BIT_DEPTHS, command=lambda _v: self._morph_shared_changed()).pack(side="left")
    tk.Label(output_line, text="DC Removal").pack(side="left", padx=(14, 4))
    tk.OptionMenu(output_line, self.dc_mode_var, "Off", "Whole File", "Per Cycle", command=lambda _v: self._morph_shared_changed()).pack(side="left")
    tk.Label(output_line, text="Normalise").pack(side="left", padx=(14, 4))
    tk.OptionMenu(output_line, self.normalise_mode_var, "Off", "Whole File Peak", "Per Cycle Peak", command=lambda _v: self._morph_shared_changed()).pack(side="left")
    tk.Label(output_line, text="Headroom dB").pack(side="left", padx=(14, 4))
    tk.Entry(output_line, textvariable=self.headroom_var, width=7, justify="right").pack(side="left")

    volume_line = tk.Frame(parent)
    volume_line.pack(fill="x", padx=8, pady=3)
    tk.Label(volume_line, text="Volume", width=15, anchor="w").pack(side="left")
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Tk Scale is the slider widget. In this app, slider callbacks push new values
    # TUTORIAL: into patch or audio state.
    # TUTORIAL: ------------------------------------------------------------------------
    tk.Scale(volume_line, variable=self.volume_var, from_=0.0, to=1.0, resolution=0.01, orient="horizontal", showvalue=True, length=320, command=lambda _v: self._morph_shared_changed()).pack(side="left", fill="x", expand=True)

    filename_line = tk.Frame(parent)
    filename_line.pack(fill="x", padx=8, pady=3)
    tk.Label(filename_line, text="Filename Prefix").pack(side="left")
    tk.Entry(filename_line, textvariable=self.filename_prefix_var, width=32).pack(side="left", padx=(6, 0))
    tk.Label(filename_line, text="Save Dialog Uses A Fresh Current Timestamp Each Export.").pack(side="left", padx=(10, 0))

    action_line = tk.Frame(parent)
    action_line.pack(fill="x", padx=8, pady=3)
    tk.Button(action_line, text="Drone START", command=lambda: self._drone_morph_endpoint("start")).pack(side="left")
    tk.Button(action_line, text="Drone END", command=lambda: self._drone_morph_endpoint("end")).pack(side="left", padx=(6, 0))
    tk.Button(action_line, text="Stop", command=self.stop_audio).pack(side="left", padx=(6, 0))
    tk.Button(action_line, text="Audition START→END", command=lambda: self._audition_morph_transition(False)).pack(side="left", padx=(18, 0))
    tk.Button(action_line, text="Audition END→START", command=lambda: self._audition_morph_transition(True)).pack(side="left", padx=(6, 0))

    export_line = tk.Frame(parent)
    export_line.pack(fill="x", padx=8, pady=3)
    tk.Button(export_line, text="EXPORT: START→END", command=lambda: self._export_morph_directions(["forward"])).pack(side="left")
    tk.Button(export_line, text="EXPORT: END→START", command=lambda: self._export_morph_directions(["reverse"])).pack(side="left", padx=(8, 0))
    tk.Button(export_line, text="EXPORT: Both", command=lambda: self._export_morph_directions(["forward", "reverse"])).pack(side="left", padx=(8, 0))

    tk.Label(parent, textvariable=self.consequence_var, anchor="w", justify="left", wraplength=1600).pack(fill="x", padx=8, pady=(2, 6))
    self._morph_changed()


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-6 override/helper: _v6_collect_export_targets belongs to the
# TUTORIAL: schema/export/bulk-generation layer.
# TUTORIAL: It often wraps or replaces earlier tabbed export behaviour without deleting the old
# TUTORIAL: code.
# TUTORIAL: ------------------------------------------------------------------------
def _v6_collect_export_targets(self, directions: list[str], safe_prefix: str, *, bulk: bool = False, bulk_epoch: int | None = None, bulk_serial: int | None = None, directory: str | None = None) -> dict[str, str] | None:
    initialdir = getattr(self, "last_export_dir", _os.getcwd()) or _os.getcwd()
    stamp = int(bulk_epoch if bulk_epoch is not None else _time.time())
    targets: dict[str, str] = {}
    if bulk:
        if not directory:
            return None
        serial = int(bulk_serial or 1)
        for direction in directions:
            suffix = _v5_direction_suffix(direction)
            filename = f"{safe_prefix}_{stamp}_{serial:04d}_{suffix}.wav"
            targets[direction] = _os.path.join(directory, filename)
        return targets

    if directions == ["forward", "reverse"]:
        defaults = [
            ("forward", f"{safe_prefix}_{stamp}_START_TO_END.wav", "Create START→END WAV + JSON"),
            ("reverse", f"{safe_prefix}_{stamp}_END_TO_START.wav", "Create END→START WAV + JSON"),
        ]
        for direction, initialfile, title in defaults:
            # TUTORIAL: ------------------------------------------------------------------------
            # TUTORIAL: File dialogs are UI operations, so they happen outside the audio
            # TUTORIAL: callback and before/after offline rendering.
            # TUTORIAL: ------------------------------------------------------------------------
            chosen = _filedialog.asksaveasfilename(title=title, defaultextension=".wav", initialdir=initialdir, initialfile=initialfile, filetypes=(("WAV Audio", "*.wav"), ("All Files", "*.*")))
            if not chosen:
                self.status.config(text="EXPORT CANCELLED")
                return None
            targets[direction] = chosen
            initialdir = _os.path.dirname(chosen) or initialdir
        self.last_export_dir = initialdir
        return targets

    direction = directions[0]
    suffix = _v5_direction_suffix(direction)
    suggested = f"{safe_prefix}_{stamp}_{suffix}.wav"
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: File dialogs are UI operations, so they happen outside the audio callback and
    # TUTORIAL: before/after offline rendering.
    # TUTORIAL: ------------------------------------------------------------------------
    chosen = _filedialog.asksaveasfilename(title="Create WAV + JSON", defaultextension=".wav", initialdir=initialdir, initialfile=suggested, filetypes=(("WAV Audio", "*.wav"), ("All Files", "*.*")))
    if not chosen:
        self.status.config(text="EXPORT CANCELLED")
        return None
    self.last_export_dir = _os.path.dirname(chosen) or initialdir
    targets[direction] = chosen
    return targets


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-6 override/helper: _v6_write_render_targets belongs to the
# TUTORIAL: schema/export/bulk-generation layer.
# TUTORIAL: It often wraps or replaces earlier tabbed export behaviour without deleting the old
# TUTORIAL: code.
# TUTORIAL: ------------------------------------------------------------------------
def _v6_write_render_targets(self, *, directions: list[str], targets: dict[str, str], safe_prefix: str, summary: RenderSummary, raw_start: PatchSettings, raw_end: PatchSettings, start: PatchSettings, end: PatchSettings, bulk: dict | None = None) -> list[str]:
    created: list[str] = []
    for direction in directions:
        reverse = direction == "reverse"
        audio, notes = _render_morph_audio(
            start,
            end,
            summary=summary,
            shared_type=None if bool(self.different_types_var.get()) else self._effective_start_type(),
            different_types=bool(self.different_types_var.get()),
            different_fm_ranges=bool(self.different_fm_ranges_var.get()),
            curve_amount=clamp_int(_safe_int_from_var(self.curve_amount_var, 127), 1, 255),
            curve_mode=str(self.curve_mode_var.get()),
            cycle_stepped=bool(self.cycle_stepped_var.get()),
            reverse=reverse,
            dc_mode=str(self.dc_mode_var.get()),
            norm_mode=str(self.normalise_mode_var.get()),
            headroom_db=_safe_float_from_var(self.headroom_var, MORPH_HEADROOM_DEFAULT_DB),
        )
        wav_path = targets[direction]
        if not _os.path.splitext(wav_path)[1]:
            wav_path += ".wav"
        json_path = _os.path.splitext(wav_path)[0] + ".json"
        _os.makedirs(_os.path.dirname(wav_path) or ".", exist_ok=True)
        _write_wav_mono(wav_path, audio, summary.sample_rate, summary.bit_depth)
        metadata = _v6_create_metadata(
            self,
            safe_prefix=safe_prefix,
            direction=direction,
            reverse=reverse,
            wav_path=wav_path,
            json_path=json_path,
            summary=summary,
            raw_start=raw_start,
            raw_end=raw_end,
            start=start,
            end=end,
            notes=notes,
            bulk=bulk,
        )
        with open(json_path, "w", encoding="utf-8") as f:
            # TUTORIAL: ------------------------------------------------------------------------
            # TUTORIAL: JSON sidecars are written beside WAV files so settings can be inspected,
            # TUTORIAL: archived, and later imported.
            # TUTORIAL: ------------------------------------------------------------------------
            _json.dump(metadata, f, indent=2, sort_keys=True)
        created.extend([wav_path, json_path])
    return created


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-6 override/helper: _v6_export_morph_directions belongs to the
# TUTORIAL: schema/export/bulk-generation layer.
# TUTORIAL: It often wraps or replaces earlier tabbed export behaviour without deleting the old
# TUTORIAL: code.
# TUTORIAL: ------------------------------------------------------------------------
def _v6_export_morph_directions(self, directions: list[str]) -> None:
    safe_prefix = _v5_safe_prefix_from_ui(self)
    targets = _v6_collect_export_targets(self, directions, safe_prefix)
    if targets is None:
        return
    summary = self._summary_from_ui()
    raw_start, raw_end, start, end = _v5_current_patch_pair(self)
    try:
        created = _v6_write_render_targets(self, directions=directions, targets=targets, safe_prefix=safe_prefix, summary=summary, raw_start=raw_start, raw_end=raw_end, start=start, end=end)
        self.status.config(text="EXPORT COMPLETE  " + "  ".join(_os.path.basename(x) for x in created))
    except Exception as exc:
        self.status.config(text=f"EXPORT ERROR  {type(exc).__name__}: {exc}")


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-6 override/helper: _v6_create_wav_clicked belongs to the
# TUTORIAL: schema/export/bulk-generation layer.
# TUTORIAL: It often wraps or replaces earlier tabbed export behaviour without deleting the old
# TUTORIAL: code.
# TUTORIAL: ------------------------------------------------------------------------
def _v6_create_wav_clicked(self) -> None:
    # Backward-compatible path for any older callbacks still pointing here.
    selection = str(getattr(self, "direction_var", tk.StringVar(value="START→END")).get())
    directions = ["forward", "reverse"] if selection == "Both" else (["reverse"] if selection == "END→START" else ["forward"])
    self._export_morph_directions(directions)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-6 override/helper: _v6_random_patch_for_bulk belongs to the
# TUTORIAL: schema/export/bulk-generation layer.
# TUTORIAL: It often wraps or replaces earlier tabbed export behaviour without deleting the old
# TUTORIAL: code.
# TUTORIAL: ------------------------------------------------------------------------
def _v6_random_patch_for_bulk(self) -> PatchSettings:
    before_start, before_end = _random_window_pair()
    after_start, after_end = _random_window_pair()
    return PatchSettings(
        pot1=_random.randint(0, POT_MAX_12BIT),
        pot2=_random.randint(0, POT_MAX_12BIT),
        pot3=_random.randint(0, POT_MAX_12BIT),
        wavetable_type=_random.randint(0, WAVETABLE_TYPE_MAX),
        fm=_random.randint(0, 255),
        pwm=_random.randint(0, 255),
        am=_random.randint(0, 255),
        before_start=before_start,
        before_end=before_end,
        after_start=after_start,
        after_end=after_end,
        fm_range_label=str(self.shared_fm_range_var.get()),
    )


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-6 override/helper: _v6_bulk_pair belongs to the
# TUTORIAL: schema/export/bulk-generation layer.
# TUTORIAL: It often wraps or replaces earlier tabbed export behaviour without deleting the old
# TUTORIAL: code.
# TUTORIAL: ------------------------------------------------------------------------
def _v6_bulk_pair(self) -> tuple[PatchSettings, PatchSettings, PatchSettings, PatchSettings]:
    raw_start = _v6_random_patch_for_bulk(self)
    raw_end = _v6_random_patch_for_bulk(self)
    start = PatchSettings(**_asdict(raw_start))
    end = PatchSettings(**_asdict(raw_end))
    if not bool(self.different_types_var.get()):
        shared_type = _random.randint(0, WAVETABLE_TYPE_MAX)
        start.wavetable_type = shared_type
        end.wavetable_type = shared_type
    if not bool(self.different_fm_ranges_var.get()):
        shared_fm = str(self.shared_fm_range_var.get())
        start.fm_range_label = shared_fm
        end.fm_range_label = shared_fm
    return raw_start, raw_end, start, end


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-6 override/helper: _v6_build_bulk_tab belongs to the
# TUTORIAL: schema/export/bulk-generation layer.
# TUTORIAL: It often wraps or replaces earlier tabbed export behaviour without deleting the old
# TUTORIAL: code.
# TUTORIAL: ------------------------------------------------------------------------
def _v6_build_bulk_tab(self) -> None:
    outer = tk.Frame(self.bulk_tab)
    outer.pack(fill="both", expand=True)
    content = self._make_scrollable_tab(outer)
    tk.Label(content, text="Bulk File Generation", font=("TkDefaultFont", 12, "bold")).pack(anchor="w", padx=8, pady=(8, 4))
    tk.Label(
        content,
        text="Generates Random START/END Patch Pairs Using The Shared Morph/Output Settings. Audio Is Not Auditioned During Bulk Export.",
        anchor="w",
        justify="left",
        wraplength=1500,
    ).pack(fill="x", padx=8, pady=(0, 8))

    # Reuse the same shared variables, but do not duplicate every Morph control.
    settings = tk.LabelFrame(content, text="Shared Bulk Settings")
    settings.pack(fill="x", padx=8, pady=4)

    freq_box = tk.Frame(settings)
    freq_box.pack(fill="x", padx=8, pady=3)
    self._add_frequency_note_row(freq_box, command=self._morph_shared_changed)

    volume_box = tk.Frame(settings)
    volume_box.pack(fill="x", padx=8, pady=3)
    tk.Label(volume_box, text="Volume", width=15, anchor="w").pack(side="left")
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Tk Scale is the slider widget. In this app, slider callbacks push new values
    # TUTORIAL: into patch or audio state.
    # TUTORIAL: ------------------------------------------------------------------------
    tk.Scale(volume_box, variable=self.volume_var, from_=0.0, to=1.0, resolution=0.01, orient="horizontal", showvalue=True, length=320, command=lambda _v: self._morph_shared_changed()).pack(side="left", fill="x", expand=True)

    row1 = tk.Frame(settings); row1.pack(fill="x", padx=8, pady=3)
    tk.Label(row1, text="Duration").pack(side="left")
    tk.OptionMenu(row1, self.duration_mode_var, "Seconds", "Cycles", "Samples", command=lambda _v: self._morph_shared_changed()).pack(side="left", padx=(6, 0))
    tk.Entry(row1, textvariable=self.duration_value_var, width=10, justify="right").pack(side="left", padx=(6, 0))
    tk.Checkbutton(row1, text="Cycle-Stepped Transition", variable=self.cycle_stepped_var, command=self._morph_shared_changed).pack(side="left", padx=(14, 0))
    tk.Label(row1, text="Curve").pack(side="left", padx=(14, 4))
    tk.OptionMenu(row1, self.curve_mode_var, "Bend", "S-Curve", command=lambda _v: self._morph_shared_changed()).pack(side="left")
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Tk Scale is the slider widget. In this app, slider callbacks push new values
    # TUTORIAL: into patch or audio state.
    # TUTORIAL: ------------------------------------------------------------------------
    tk.Scale(row1, variable=self.curve_amount_var, from_=1, to=255, resolution=1, orient="horizontal", length=180, command=lambda _v: self._morph_shared_changed()).pack(side="left", padx=(6, 0))

    row2 = tk.Frame(settings); row2.pack(fill="x", padx=8, pady=3)
    tk.Label(row2, text="Sample Rate").pack(side="left")
    tk.OptionMenu(row2, self.sample_rate_var, *EXPORT_SAMPLE_RATES, command=lambda _v: self._morph_shared_changed()).pack(side="left", padx=(6, 0))
    tk.Label(row2, text="Bit Depth").pack(side="left", padx=(14, 4))
    tk.OptionMenu(row2, self.bit_depth_var, *EXPORT_BIT_DEPTHS, command=lambda _v: self._morph_shared_changed()).pack(side="left")
    tk.Label(row2, text="DC Removal").pack(side="left", padx=(14, 4))
    tk.OptionMenu(row2, self.dc_mode_var, "Off", "Whole File", "Per Cycle", command=lambda _v: self._morph_shared_changed()).pack(side="left")
    tk.Label(row2, text="Normalise").pack(side="left", padx=(14, 4))
    tk.OptionMenu(row2, self.normalise_mode_var, "Off", "Whole File Peak", "Per Cycle Peak", command=lambda _v: self._morph_shared_changed()).pack(side="left")
    tk.Label(row2, text="Headroom dB").pack(side="left", padx=(14, 4))
    tk.Entry(row2, textvariable=self.headroom_var, width=7, justify="right").pack(side="left")

    row3 = tk.Frame(settings); row3.pack(fill="x", padx=8, pady=3)
    tk.Checkbutton(row3, text="Different START/END Wavetable Types", variable=self.different_types_var, command=self._morph_shared_changed).pack(side="left")
    tk.Checkbutton(row3, text="Different START/END FM Pitch Range", variable=self.different_fm_ranges_var, command=self._morph_shared_changed).pack(side="left", padx=(18, 0))
    tk.Label(row3, text="Shared FM Pitch Range").pack(side="left", padx=(18, 6))
    tk.OptionMenu(row3, self.shared_fm_range_var, *[label for label, _c in FM_RANGE_OPTIONS], command=lambda _v: self._morph_shared_changed()).pack(side="left")

    export = tk.LabelFrame(content, text="Bulk Export")
    export.pack(fill="x", padx=8, pady=8)
    self.bulk_count_var = tk.StringVar(value="12")
    self.bulk_prefix_var = tk.StringVar(value="fractal_wt_morph")

    row4 = tk.Frame(export); row4.pack(fill="x", padx=8, pady=3)
    tk.Label(row4, text="Random Iterations").pack(side="left")
    tk.Entry(row4, textvariable=self.bulk_count_var, width=10, justify="right").pack(side="left", padx=(6, 0))
    tk.Label(row4, text="Filename Prefix").pack(side="left", padx=(18, 6))
    tk.Entry(row4, textvariable=self.bulk_prefix_var, width=28).pack(side="left")
    tk.Label(row4, text="Files Use Epoch + Serial Number + Direction.").pack(side="left", padx=(10, 0))

    row5 = tk.Frame(export); row5.pack(fill="x", padx=8, pady=3)
    tk.Button(row5, text="EXPORT BULK: START→END", command=lambda: self._bulk_export(["forward"])).pack(side="left")
    tk.Button(row5, text="EXPORT BULK: END→START", command=lambda: self._bulk_export(["reverse"])).pack(side="left", padx=(8, 0))
    tk.Button(row5, text="EXPORT BULK: Both", command=lambda: self._bulk_export(["forward", "reverse"])).pack(side="left", padx=(8, 0))

    tk.Label(content, textvariable=self.consequence_var, anchor="w", justify="left", wraplength=1600).pack(fill="x", padx=8, pady=(2, 6))


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Bulk exporter: repeatedly randomises patch pairs and writes WAV+JSON files. This
# TUTORIAL: turns the instrument into a sample-material generator rather than only a
# TUTORIAL: hand-tweaked UI.
# TUTORIAL: ------------------------------------------------------------------------
def _v6_bulk_export(self, directions: list[str]) -> None:
    try:
        count = clamp_int(_safe_int_from_var(self.bulk_count_var, 12), 1, BULK_MAX_EXPORTS)
    except Exception:
        count = 12
    prefix = "fractal_wt_morph"
    try:
        prefix = str(self.bulk_prefix_var.get()).strip() or prefix
    except Exception:
        pass
    safe_prefix = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in prefix).strip("_") or "fractal_wt_morph"
    initialdir = getattr(self, "last_export_dir", _os.getcwd()) or _os.getcwd()
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: File dialogs are UI operations, so they happen outside the audio callback and
    # TUTORIAL: before/after offline rendering.
    # TUTORIAL: ------------------------------------------------------------------------
    directory = _filedialog.askdirectory(title="Choose Bulk Export Folder", initialdir=initialdir, mustexist=True)
    if not directory:
        self.status.config(text="BULK EXPORT CANCELLED")
        return
    self.last_export_dir = directory
    epoch = int(_time.time())
    summary = self._summary_from_ui()
    created_count = 0
    try:
        for serial in range(1, count + 1):
            raw_start, raw_end, start, end = _v6_bulk_pair(self)
            targets = _v6_collect_export_targets(self, directions, safe_prefix, bulk=True, bulk_epoch=epoch, bulk_serial=serial, directory=directory)
            bulk_info = {"enabled": True, "epoch": epoch, "serial_number": serial, "requested_iterations": count, "directions": [_v5_direction_code(d) for d in directions]}
            created = _v6_write_render_targets(self, directions=directions, targets=targets or {}, safe_prefix=safe_prefix, summary=summary, raw_start=raw_start, raw_end=raw_end, start=start, end=end, bulk=bulk_info)
            created_count += len(created)
            if serial == 1 or serial % 10 == 0 or serial == count:
                self.status.config(text=f"BULK EXPORT  {serial}/{count} random iterations complete")
                try:
                    self.root.update_idletasks()
                except Exception:
                    pass
        self.status.config(text=f"BULK EXPORT COMPLETE  {created_count} files written to {_os.path.basename(directory) or directory}")
    except Exception as exc:
        self.status.config(text=f"BULK EXPORT ERROR  {type(exc).__name__}: {exc}")


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-6 override/helper: _v6_tab_changed belongs to the
# TUTORIAL: schema/export/bulk-generation layer.
# TUTORIAL: It often wraps or replaces earlier tabbed export behaviour without deleting the old
# TUTORIAL: code.
# TUTORIAL: ------------------------------------------------------------------------
def _v6_tab_changed(self, _event=None) -> None:
    name = self._active_tab_name()
    if name == "Bulk File Generation":
        self._set_audio_stopped(status="BULK FILE GENERATION TAB ACTIVE  audio stopped")
        return
    return _v3_tab_changed(self, _event)


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-6 override/helper: _v6_tabbed_init belongs to the
# TUTORIAL: schema/export/bulk-generation layer.
# TUTORIAL: It often wraps or replaces earlier tabbed export behaviour without deleting the old
# TUTORIAL: code.
# TUTORIAL: ------------------------------------------------------------------------
def _v6_tabbed_init(self, *args, **kwargs):
    _v6_previous_tabbed_init(self, *args, **kwargs)
    try:
        self.bulk_tab = tk.Frame(self.notebook)
        self.notebook.add(self.bulk_tab, text="Bulk File Generation")
        self._build_bulk_tab()
        _v6_capitalise_widget_tree(self.root)
    except Exception as exc:
        try:
            self.status.config(text=f"BULK TAB BUILD ERROR  {type(exc).__name__}: {exc}")
        except Exception:
            pass


# Case-tolerant export post-processing for title-cased UI option labels.
_previous_apply_cycle_dc_and_normalise_v6 = _apply_cycle_dc_and_normalise

# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Per-cycle cleanup stage for exported audio. This supports the sound-design goal:
# TUTORIAL: every cycle can become a flat, centred oscillator/wavetable frame.
# TUTORIAL: ------------------------------------------------------------------------
def _apply_cycle_dc_and_normalise(
    audio: np.ndarray,
    *,
    frequency: float,
    sample_rate: int,
    dc_mode: str,
    norm_mode: str,
    headroom_db: float,
) -> np.ndarray:  # type: ignore[override]
    dc = str(dc_mode or "").strip().lower().replace("_", " ")
    norm = str(norm_mode or "").strip().lower().replace("_", " ")
    dc_canonical = "Off"
    if dc == "whole file":
        dc_canonical = "Whole file"
    elif dc == "per cycle":
        dc_canonical = "Per cycle"
    norm_canonical = "Off"
    if norm == "whole file peak":
        norm_canonical = "Whole file peak"
    elif norm == "per cycle peak":
        norm_canonical = "Per cycle peak"
    return _previous_apply_cycle_dc_and_normalise_v6(
        audio,
        frequency=frequency,
        sample_rate=sample_rate,
        dc_mode=dc_canonical,
        norm_mode=norm_canonical,
        headroom_db=headroom_db,
    )


# Apply v6 overrides.
TabbedFractalApp._build_morph_shared_controls = _v6_build_morph_shared_controls
TabbedFractalApp._export_morph_directions = _v6_export_morph_directions
TabbedFractalApp._create_wav_clicked = _v6_create_wav_clicked
TabbedFractalApp._build_bulk_tab = _v6_build_bulk_tab
TabbedFractalApp._bulk_export = _v6_bulk_export
TabbedFractalApp._tab_changed = _v6_tab_changed
_v6_previous_tabbed_init = TabbedFractalApp.__init__
TabbedFractalApp.__init__ = _v6_tabbed_init



# -----------------------------------------------------------------------------
# v7 scroll/progress/bulk-layout pass
#
# - Scroll-wheel dispatch is focus/hover aware instead of being overwritten by
#   the last scrollable tab built. This restores Morph scrolling while keeping
#   Bulk scrolling working.
# - Bulk tab removes the audition-only Volume control.
# - Bulk shared rows mirror the Morph shared layout where the controls overlap.
# - Bulk export gets a determinate progress bar and progress label.
# -----------------------------------------------------------------------------


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-7 override/helper: _v7_bind_scroll_dispatcher is part of the latest patch
# TUTORIAL: layer. These functions usually replace older methods by assignment near the end of
# TUTORIAL: the section.
# TUTORIAL: Reading tip: in this file, the last assignment to a method name is the one that wins
# TUTORIAL: at runtime.
# TUTORIAL: ------------------------------------------------------------------------
def _v7_bind_scroll_dispatcher(self) -> None:
    if getattr(self, "_v7_scroll_dispatch_bound", False):
        return

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function on_mousewheel: small named steps make the signal path, UI path,
    # TUTORIAL: and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def on_mousewheel(event):
        canvas = getattr(self, "_active_scroll_canvas", None)
        if canvas is None:
            return
        try:
            if not bool(canvas.winfo_exists()):
                return
        except Exception:
            return
        try:
            if getattr(event, "num", None) == 4:
                canvas.yview_scroll(-3, "units")
            elif getattr(event, "num", None) == 5:
                canvas.yview_scroll(3, "units")
            else:
                delta = int(getattr(event, "delta", 0))
                if delta:
                    steps = int(-delta / 120)
                    if steps == 0:
                        steps = -1 if delta > 0 else 1
                    canvas.yview_scroll(steps, "units")
        except Exception:
            pass

    try:
        self.root.bind_all("<MouseWheel>", on_mousewheel)
        self.root.bind_all("<Button-4>", on_mousewheel)
        self.root.bind_all("<Button-5>", on_mousewheel)
        self._v7_scroll_dispatch_bound = True
    except Exception:
        pass


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-7 override/helper: _v7_make_scrollable_tab is part of the latest patch
# TUTORIAL: layer. These functions usually replace older methods by assignment near the end of
# TUTORIAL: the section.
# TUTORIAL: Reading tip: in this file, the last assignment to a method name is the one that wins
# TUTORIAL: at runtime.
# TUTORIAL: ------------------------------------------------------------------------
def _v7_make_scrollable_tab(self, parent: tk.Frame) -> tk.Frame:
    canvas = tk.Canvas(parent, highlightthickness=0)
    scrollbar = tk.Scrollbar(parent, orient="vertical", command=canvas.yview)
    inner = tk.Frame(canvas)
    win_id = canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function on_inner_config: small named steps make the signal path, UI
    # TUTORIAL: path, and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def on_inner_config(_event=None):
        try:
            canvas.configure(scrollregion=canvas.bbox("all"))
        except Exception:
            pass

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function on_canvas_config: small named steps make the signal path, UI
    # TUTORIAL: path, and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def on_canvas_config(event):
        try:
            canvas.itemconfigure(win_id, width=event.width)
        except Exception:
            pass

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function activate: small named steps make the signal path, UI path, and
    # TUTORIAL: export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def activate(_event=None):
        self._active_scroll_canvas = canvas

    inner.bind("<Configure>", on_inner_config)
    canvas.bind("<Configure>", on_canvas_config)
    canvas.bind("<Enter>", activate)
    inner.bind("<Enter>", activate)
    canvas.bind("<FocusIn>", activate)
    inner.bind("<FocusIn>", activate)
    try:
        # Make clicks inside the area enough to redirect wheel events back here.
        canvas.bind("<Button-1>", lambda _e: (activate(), canvas.focus_set()))
        inner.bind("<Button-1>", lambda _e: (activate(), canvas.focus_set()))
    except Exception:
        pass

    self._v7_bind_scroll_dispatcher()
    self._active_scroll_canvas = canvas
    return inner


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-7 override/helper: _v7_shared_bulk_settings_rows is part of the latest patch
# TUTORIAL: layer. These functions usually replace older methods by assignment near the end of
# TUTORIAL: the section.
# TUTORIAL: Reading tip: in this file, the last assignment to a method name is the one that wins
# TUTORIAL: at runtime.
# TUTORIAL: ------------------------------------------------------------------------
def _v7_shared_bulk_settings_rows(self, parent: tk.Frame) -> None:
    """Build the overlapping Morph/Bulk shared controls with Morph-like layout.

    Bulk intentionally omits Volume because there is no audition path on this
    tab. Export level is controlled by DC removal, normalisation, and headroom.
    """
    freq_box = tk.Frame(parent)
    freq_box.pack(fill="x", padx=8, pady=3)
    self._add_frequency_note_row(freq_box, command=self._morph_shared_changed)

    line1 = tk.Frame(parent)
    line1.pack(fill="x", padx=8, pady=3)
    tk.Checkbutton(
        line1,
        text="Different START/END Wavetable Types",
        variable=self.different_types_var,
        command=self._morph_shared_changed,
    ).pack(side="left")
    tk.Label(line1, text="Shared Wavetable Type").pack(side="left", padx=(18, 6))
    shared_combo = _ttk.Combobox(
        line1,
        textvariable=self.shared_type_option_var,
        values=WT_OPTION_LIST,
        state="readonly",
        width=58,
    )
    shared_combo.pack(side="left", fill="x", expand=True)

    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Helper function shared_type_changed: small named steps make the signal path, UI
    # TUTORIAL: path, and export path easier to inspect and test.
    # TUTORIAL: ------------------------------------------------------------------------
    def shared_type_changed(_event=None):
        self.shared_type_var.set(wavetable_type_from_option(self.shared_type_option_var.get()))
        self._morph_shared_changed()

    shared_combo.bind("<<ComboboxSelected>>", shared_type_changed)
    self.bulk_shared_type_combo = shared_combo

    line2 = tk.Frame(parent)
    line2.pack(fill="x", padx=8, pady=3)
    tk.Checkbutton(
        line2,
        text="Different START/END FM Pitch Range",
        variable=self.different_fm_ranges_var,
        command=self._morph_shared_changed,
    ).pack(side="left")
    tk.Label(line2, text="Shared FM Pitch Range").pack(side="left", padx=(18, 6))
    tk.OptionMenu(
        line2,
        self.shared_fm_range_var,
        *[label for label, _c in FM_RANGE_OPTIONS],
        command=lambda _v: self._morph_shared_changed(),
    ).pack(side="left")

    duration_line = tk.Frame(parent)
    duration_line.pack(fill="x", padx=8, pady=3)
    tk.Label(duration_line, text="Duration").pack(side="left")
    tk.OptionMenu(
        duration_line,
        self.duration_mode_var,
        "Seconds",
        "Cycles",
        "Samples",
        command=lambda _v: self._morph_shared_changed(),
    ).pack(side="left", padx=(6, 0))
    duration_entry = tk.Entry(duration_line, textvariable=self.duration_value_var, width=10, justify="right")
    duration_entry.pack(side="left", padx=(6, 0))
    duration_entry.bind("<Return>", lambda _e: self._morph_shared_changed())
    duration_entry.bind("<FocusOut>", lambda _e: self._morph_shared_changed())
    tk.Checkbutton(
        duration_line,
        text="Cycle-Stepped Transition",
        variable=self.cycle_stepped_var,
        command=self._morph_shared_changed,
    ).pack(side="left", padx=(14, 0))
    tk.Label(duration_line, text="Curve").pack(side="left", padx=(14, 4))
    tk.OptionMenu(
        duration_line,
        self.curve_mode_var,
        "Bend",
        "S-Curve",
        command=lambda _v: self._morph_shared_changed(),
    ).pack(side="left")
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: Tk Scale is the slider widget. In this app, slider callbacks push new values
    # TUTORIAL: into patch or audio state.
    # TUTORIAL: ------------------------------------------------------------------------
    tk.Scale(
        duration_line,
        variable=self.curve_amount_var,
        from_=1,
        to=255,
        resolution=1,
        orient="horizontal",
        length=180,
        command=lambda _v: self._morph_shared_changed(),
    ).pack(side="left", padx=(6, 0))

    output_line = tk.Frame(parent)
    output_line.pack(fill="x", padx=8, pady=3)
    tk.Label(output_line, text="Sample Rate").pack(side="left")
    tk.OptionMenu(
        output_line,
        self.sample_rate_var,
        *EXPORT_SAMPLE_RATES,
        command=lambda _v: self._morph_shared_changed(),
    ).pack(side="left", padx=(6, 0))
    tk.Label(output_line, text="Bit Depth").pack(side="left", padx=(14, 4))
    tk.OptionMenu(
        output_line,
        self.bit_depth_var,
        *EXPORT_BIT_DEPTHS,
        command=lambda _v: self._morph_shared_changed(),
    ).pack(side="left")
    tk.Label(output_line, text="DC Removal").pack(side="left", padx=(14, 4))
    tk.OptionMenu(
        output_line,
        self.dc_mode_var,
        "Off",
        "Whole File",
        "Per Cycle",
        command=lambda _v: self._morph_shared_changed(),
    ).pack(side="left")
    tk.Label(output_line, text="Normalise").pack(side="left", padx=(14, 4))
    tk.OptionMenu(
        output_line,
        self.normalise_mode_var,
        "Off",
        "Whole File Peak",
        "Per Cycle Peak",
        command=lambda _v: self._morph_shared_changed(),
    ).pack(side="left")
    tk.Label(output_line, text="Headroom dB").pack(side="left", padx=(14, 4))
    tk.Entry(output_line, textvariable=self.headroom_var, width=7, justify="right").pack(side="left")


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-7 override/helper: _v7_build_bulk_tab is part of the latest patch layer.
# TUTORIAL: These functions usually replace older methods by assignment near the end of the
# TUTORIAL: section.
# TUTORIAL: Reading tip: in this file, the last assignment to a method name is the one that wins
# TUTORIAL: at runtime.
# TUTORIAL: ------------------------------------------------------------------------
def _v7_build_bulk_tab(self) -> None:
    outer = tk.Frame(self.bulk_tab)
    outer.pack(fill="both", expand=True)
    content = self._make_scrollable_tab(outer)

    tk.Label(content, text="Bulk File Generation", font=("TkDefaultFont", 12, "bold")).pack(anchor="w", padx=8, pady=(8, 4))
    tk.Label(
        content,
        text="Generates Random START/END Patch Pairs Using The Shared Morph/Output Settings. Audio Is Not Auditioned During Bulk Export.",
        anchor="w",
        justify="left",
        wraplength=1500,
    ).pack(fill="x", padx=8, pady=(0, 8))

    settings = tk.LabelFrame(content, text="Shared Bulk Settings")
    settings.pack(fill="x", padx=8, pady=4)
    self._v7_shared_bulk_settings_rows(settings)

    export = tk.LabelFrame(content, text="Bulk Export")
    export.pack(fill="x", padx=8, pady=8)
    self.bulk_count_var = tk.StringVar(value="12")
    self.bulk_prefix_var = tk.StringVar(value="fractal_wt_morph")
    self.bulk_progress_var = tk.DoubleVar(value=0.0)
    self.bulk_progress_label_var = tk.StringVar(value="Progress: 0/0")

    row1 = tk.Frame(export)
    row1.pack(fill="x", padx=8, pady=3)
    tk.Label(row1, text="Random Iterations").pack(side="left")
    tk.Entry(row1, textvariable=self.bulk_count_var, width=10, justify="right").pack(side="left", padx=(6, 0))
    tk.Label(row1, text="Filename Prefix").pack(side="left", padx=(18, 6))
    tk.Entry(row1, textvariable=self.bulk_prefix_var, width=28).pack(side="left")
    tk.Label(row1, text="Files Use Epoch + Serial Number + Direction.").pack(side="left", padx=(10, 0))

    row2 = tk.Frame(export)
    row2.pack(fill="x", padx=8, pady=3)
    tk.Button(row2, text="EXPORT BULK: START→END", command=lambda: self._bulk_export(["forward"])).pack(side="left")
    tk.Button(row2, text="EXPORT BULK: END→START", command=lambda: self._bulk_export(["reverse"])).pack(side="left", padx=(8, 0))
    tk.Button(row2, text="EXPORT BULK: Both", command=lambda: self._bulk_export(["forward", "reverse"])).pack(side="left", padx=(8, 0))

    row3 = tk.Frame(export)
    row3.pack(fill="x", padx=8, pady=(6, 3))
    tk.Label(row3, textvariable=self.bulk_progress_label_var, width=22, anchor="w").pack(side="left")
    self.bulk_progress_bar = _ttk.Progressbar(
        row3,
        variable=self.bulk_progress_var,
        maximum=100.0,
        mode="determinate",
    )
    self.bulk_progress_bar.pack(side="left", fill="x", expand=True)

    tk.Label(content, textvariable=self.consequence_var, anchor="w", justify="left", wraplength=1600).pack(fill="x", padx=8, pady=(2, 6))


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Version-7 override/helper: _v7_set_bulk_progress is part of the latest patch layer.
# TUTORIAL: These functions usually replace older methods by assignment near the end of the
# TUTORIAL: section.
# TUTORIAL: Reading tip: in this file, the last assignment to a method name is the one that wins
# TUTORIAL: at runtime.
# TUTORIAL: ------------------------------------------------------------------------
def _v7_set_bulk_progress(self, current: int, total: int, *, prefix: str = "Progress") -> None:
    total = max(0, int(total))
    current = clamp_int(int(current), 0, max(total, 1)) if total else 0
    try:
        if hasattr(self, "bulk_progress_bar"):
            self.bulk_progress_bar.configure(maximum=max(total, 1))
        if hasattr(self, "bulk_progress_var"):
            self.bulk_progress_var.set(float(current))
        if hasattr(self, "bulk_progress_label_var"):
            self.bulk_progress_label_var.set(f"{prefix}: {current}/{total}")
        self.root.update_idletasks()
    except Exception:
        pass


# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Latest bulk exporter override. It adds progress-bar updates while preserving the v6
# TUTORIAL: render/write behaviour.
# TUTORIAL: ------------------------------------------------------------------------
def _v7_bulk_export(self, directions: list[str]) -> None:
    try:
        count = clamp_int(_safe_int_from_var(self.bulk_count_var, 12), 1, BULK_MAX_EXPORTS)
    except Exception:
        count = 12
    prefix = "fractal_wt_morph"
    try:
        prefix = str(self.bulk_prefix_var.get()).strip() or prefix
    except Exception:
        pass
    safe_prefix = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in prefix).strip("_") or "fractal_wt_morph"
    initialdir = getattr(self, "last_export_dir", _os.getcwd()) or _os.getcwd()
    # TUTORIAL: ------------------------------------------------------------------------
    # TUTORIAL: File dialogs are UI operations, so they happen outside the audio callback and
    # TUTORIAL: before/after offline rendering.
    # TUTORIAL: ------------------------------------------------------------------------
    directory = _filedialog.askdirectory(title="Choose Bulk Export Folder", initialdir=initialdir, mustexist=True)
    if not directory:
        self.status.config(text="BULK EXPORT CANCELLED")
        self._v7_set_bulk_progress(0, count, prefix="Cancelled")
        return

    self.last_export_dir = directory
    epoch = int(_time.time())
    summary = self._summary_from_ui()
    created_count = 0
    self._v7_set_bulk_progress(0, count, prefix="Progress")
    try:
        for serial in range(1, count + 1):
            raw_start, raw_end, start, end = _v6_bulk_pair(self)
            targets = _v6_collect_export_targets(
                self,
                directions,
                safe_prefix,
                bulk=True,
                bulk_epoch=epoch,
                bulk_serial=serial,
                directory=directory,
            )
            bulk_info = {
                "enabled": True,
                "epoch": epoch,
                "serial_number": serial,
                "requested_iterations": count,
                "directions": [_v5_direction_code(d) for d in directions],
            }
            created = _v6_write_render_targets(
                self,
                directions=directions,
                targets=targets or {},
                safe_prefix=safe_prefix,
                summary=summary,
                raw_start=raw_start,
                raw_end=raw_end,
                start=start,
                end=end,
                bulk=bulk_info,
            )
            created_count += len(created)
            self._v7_set_bulk_progress(serial, count, prefix="Progress")
            if serial == 1 or serial % 10 == 0 or serial == count:
                self.status.config(text=f"BULK EXPORT  {serial}/{count} random iterations complete")
                try:
                    self.root.update_idletasks()
                except Exception:
                    pass
        self._v7_set_bulk_progress(count, count, prefix="Complete")
        self.status.config(text=f"BULK EXPORT COMPLETE  {created_count} files written to {_os.path.basename(directory) or directory}")
    except Exception as exc:
        self.status.config(text=f"BULK EXPORT ERROR  {type(exc).__name__}: {exc}")
        self._v7_set_bulk_progress(created_count, count, prefix="Error")


TabbedFractalApp._v7_bind_scroll_dispatcher = _v7_bind_scroll_dispatcher
TabbedFractalApp._make_scrollable_tab = _v7_make_scrollable_tab
TabbedFractalApp._v7_shared_bulk_settings_rows = _v7_shared_bulk_settings_rows
TabbedFractalApp._build_bulk_tab = _v7_build_bulk_tab
TabbedFractalApp._v7_set_bulk_progress = _v7_set_bulk_progress
TabbedFractalApp._bulk_export = _v7_bulk_export

# TUTORIAL: ------------------------------------------------------------------------
# TUTORIAL: Program entry point. It parses command-line options, builds the shared state and UI,
# TUTORIAL: starts audio, and enters the Tk event loop.
# TUTORIAL: ------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Live fractal wavetable oscillator and START/END WAV exporter")
    parser.add_argument("--samplerate", type=int, default=DEFAULT_SAMPLE_RATE)
    parser.add_argument("--freq", type=float, default=DEFAULT_FREQUENCY, help="Oscillator frequency in Hz")
    parser.add_argument("--device", default=None, help="Optional sounddevice output device name or number")
    parser.add_argument("--classic", action="store_true", help="Use the earlier single-screen UI")
    args = parser.parse_args()

    initial_table = generate_wavetable(DEFAULT_POT1, DEFAULT_POT2, DEFAULT_POT3, 3)
    initial_frequency, _freq_notes = clamp_frequency(args.freq)

    state = SharedState(
        target_table=initial_table,
        target_volume=0.20,
        target_frequency=initial_frequency,
        target_fm=0.0,
        target_pwm=0.0,
        target_am=0.0,
        target_before_start=0.0,
        target_before_length=WINDOW_STEPS,
        target_after_start=0.0,
        target_after_length=WINDOW_STEPS,
        reset_requested=True,
        lock=threading.Lock(),
    )
    state.target_fm_range_cents = DEFAULT_FM_RANGE_CENTS
    state.audio_watchdog_message = ""

    root = tk.Tk()
    if args.classic:
        app = App(root, state, initial_frequency=initial_frequency, sample_rate=args.samplerate)
    else:
        app = TabbedFractalApp(root, state, initial_frequency=initial_frequency, sample_rate=args.samplerate)
    engine = AudioEngine(state, samplerate=args.samplerate, device=args.device)
    app.audio_engine = engine

    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        engine.close()


if __name__ == "__main__":
    main()
