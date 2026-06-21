# Fractured Wavetable

**A tool for sound designers, sound explorers, and synthesizer programmers.**

Live online GPL v2 build: <https://atom-smasher.github.io/Fractured-Wavetable-Generator/>

<img width="416" height="263" alt="image" src="https://github.com/user-attachments/assets/11c11f14-0969-4a1c-b18d-b283d4b32e90" />

Fractured Wavetable Generator is for making oscillator waves, morphing START→END sounds, drones, growls, clangs, transitions, texture beds, sampler sources, and raw material for further synthesis or resampling.

---

Press **ESC** to stop audio and cancel active rendering. Leaving a tab also mutes the current audition.

---

## Outputs are yours

Any output from this tool is yours to use however you want.

The program's GPL notice applies to the program itself; it does **not**, by itself, impose GPL licensing on generated audio, WAV files, JSON sidecars, samples, loops, drones, presets made from exported audio, or finished music.

---

## What's new in v2.7

v2.7 is a significant update over v2.6za (the previous public release). The main additions include:

- **Drawn Waveform source** — each patch now has a hand-drawable 256-sample waveform. It can be routed as a source tap into the normal wavetable-type stage, used alongside or instead of the generator engine, and it morphs smoothly between START and END. Seam and smoothing modes are applied non-destructively at render/audition time.
- **Audio-to-Drawn import** — any browser-decodable audio file (WAV, AIFF, FLAC, OGG, MP3, and others where the browser decoder allows) can be loaded into the current patch's Drawn Waveform table. Long files are compressed across all 256 samples; short files are stretched. The result is DC-removed and peak-normalised.
- **JSON patch import** — exported JSON sidecars (schema version 7 or later) can be loaded back. A dialog lets you choose whether to load the START patch, END patch, or both into Playground or Morph.
- **Four RAM-only save slots per patch context** — quick save/load/overwrite for Playground, START, and END patches, individually. Slots are session memory and are not persisted to localStorage.

---

## What it does

- **Playground** — build, randomise, and audition one generated wavetable patch, including its Drawn Waveform source.
- **START→END Morph Export** — design two endpoint patches, audition the transition, and export forward or reverse WAV + JSON files.
- **Bulk Random Export** — generate batches of random START/END pairs for later auditioning and pruning, with morph curve controls.
- **Calculator** — plan sample rate, samples per cycle, frequency, duration, tempo length, file size, and simple multisample maps.
- **Visualisation** — inspect the generated table, modulation behaviour, time-domain shape, and spectrum.
- **Export** — render mono WAV files with JSON metadata, using user-selected length, sample rate, bit depth, DC removal, normalisation, and headroom settings.

The browser version is a single-file HTML/JavaScript tool. No build step is required.

---

## Design approach

Fractured Wavetable favours artistic weirdness over mathematical or musical purity. It is meant to produce sounds that feel discovered rather than selected: harsh, folded, glitching, metallic, unstable, mellowed, blurred, bitten down, or oddly alive.

The project also tries to keep the signal path visible, use meaningful controls, randomise with restraint, and make exported WAVs useful as sound-design objects.

### Running it

Use the live online version, or download the current `index.html` file and open it in a modern browser.

Web Audio is required for auditioning. Web MIDI is optional and browser-dependent. Chromium-based browsers are usually the safest choice for Web MIDI.

Start with the volume low. Some settings can produce sharp, bright, or abrupt digital sounds.

Notes:

- Web Audio is required for auditioning.
- Web MIDI is optional and browser-dependent.
- Chromium-based browsers are usually the safest choice for Web MIDI.
- Start with the volume low. Some settings can produce sharp, bright, or abrupt digital sounds.

---

## v2.7 feature summary

### Generator families and variants

Each generator is built from a Family × Variant combination. Both halves ("START" and "END") are randomisable independently.

| Family | Smooth | Crunchy |
| --- | --- | --- |
| **Clean Chaos** | Rounded phase motion, softer drive, stable fundamentals | Stronger phase warp, harder shaping, richer digital grit |
| **Phase Lattice** | Root-stable harmonic motion with softened folds | Sharper lattice folds and glitchier harmonic movement |
| **Phase Distortion** | Rounded phase knees, stable fundamentals, bright-but-controlled sweeps | Sharper phase knees, stronger folding, controlled non-linear movement |
| **FM** | Continuous ratios and rounded carrier-colour changes | More awkward ratios, feedback-like phase push, folding, and edge bite |
| **Pulse Warp** | Rounded moving edges and soft notches | Harder edges, chopped notches, and sync-like pulse collisions |
| **Sync** | Constrained reset ratios, rounded reset overshoot | Constrained reset ratios, sharper resets, foldback |
| **Formant** | Broad vowel-ish peaks and a stronger Source A ridge | Narrower formant peaks, driven ridges, and growlier vocal-ish edges |
| **Fold** | Rounded Buchla-ish folds and stable symmetry changes | Higher fold drive, asymmetry, hard foldback, and digital edge emphasis |
| **Bitcrush** | Softened stair-steps, dither-like offsets, sample-hold texture | Harsher bit-depth loss, decimation, and error-feedback-like bite |
| **Harmonic Cluster** | Controlled harmonic density, rolloff, and odd/even bias | Denser partial clusters, inharmonic offsets, and stronger saturation |
| **Modal** | Glassy, bell-like, and struck-object harmonic spacing | More inharmonic spacing, metallic drive, and uneven modal emphasis |
| **Feedback Sine** | Rounded feedback bends and stable fundamental anchoring | Stronger feedback, folding into itself, and sharper saturation |

Each engine exposes three knobs (Primary Phase, Shape 1, Shape 2) whose effect varies by family — the Notes tab documents what each knob does.

### Source Taps and Wavetable Type

Each generator produces four related taps: **A**, **B**, **C**, and **D**. Input 1 and Input 2 choose which two taps feed the Wavetable Type stage.

A fifth tap — **Drawn** — is the per-patch hand-drawn or audio-imported 256-sample table. It can be assigned to Input 1 or Input 2 like any other tap, or excluded from random tap assignment.

The Wavetable Type stage combines the two inputs into the final table. Available types include splices, sums, XOR modes, level-gated mixing, spectral blending, phase-based folding, and others.

### Drawn Waveform source

Each patch (Playground, START, END) contains a 256-sample Drawn Waveform:

- **Hand-drawn** by clicking or dragging on the canvas.
- **Audio-imported** from any browser-decodable audio file — WAV, AIFF, FLAC, OGG, MP3, and others. Long files are compressed; short files are stretched. Flat or silent imports are rejected.
- **Seam mode** (Free Edge / Smooth Wrap) and **Smoothing** (Off / Light / Medium / Heavy) are applied non-destructively at render time, so the saved drawing is never permanently altered by preview changes.
- Untouched sine or flat tables are excluded from random tap assignment. Only meaningful Drawn tables are eligible.

### Modulation (FM, PWM, Ring Mod)

The oscillator applies three modulation stages at playback/render time, on top of whatever table the generator produced:

- **FM** — frequency modulation by the table itself. Pitch range is set in musical cents and can be shared or endpoint-specific across START and END.
- **PWM** — pulse-width modulation shifts the phase lookup to skew the waveform asymmetrically without altering the table.
- **Ring Mod** — a multi-partial ring modulation and soft-distortion stage applied after FM and PWM.

All three are smoothly interpolated during START→END morphing.

### Filter

A resonant biquad filter (Low-pass, High-pass, or Band-pass) is applied in the signal path after oscillator modulation. Filters are reset to Off during Bulk Random Export so random batches are less likely to disappear into silence.

### Pitch and MIDI

Pitch is shared across Playground, Morph, and Bulk:

- logarithmic frequency slider
- exact Hz numeric input
- note and octave selectors
- selected-note reference readout
- Web MIDI note input where the browser supports it

MIDI note input is gated: note-on starts or retunes the current drone, note-off releases it, and the most recently held note has priority.

### Calculator

The Calculator tab is a planning helper for wavetable and sampler work. It does not generate sound by itself; it explains and applies the arithmetic behind the render settings.

It can help plan:

- sample rate and samples-per-cycle targets
- recommended frequency for 128, 256, 512, or 1024 samples per cycle
- exact duration and total sample count from cycle count
- nearest equal-tempered note and pitch offset
- mono WAV data-size estimates by bit depth and file count
- tempo/bar-length renders for risers, transitions, drones, and morphs
- rough multisample maps by MIDI range, zone spacing, and seconds per root note

The native internal table is 256 samples. Higher samples-per-cycle targets oversample the rendered/interpolated result and create larger files; lower values create smaller files but preserve less cycle detail.

### Source and Result Windows

Each patch has a Before Window (applied before the Wavetable Type stage) and an After Window (applied after). Both are set on a 0–256 scale matching the internal 256-sample table. Narrowing a window reduces how much of the source contributes; this interacts with the Wavetable Type to create truncated, gated, or edge-emphasised textures.

### Morphing

The Morph tab supports START→END sound design and export. Morph render modes include:

- Full Interpolation — favours digital noise and fracture.
- Crossfade — warmer but can beat or phase.
- Phase-Aligned Crossfade — reduces cancellation.
- Phase-Offset Crossfade — encourages phaser-like motion.
- Dual-Path Interpolation — avoids midpoint type/filter/engine switches.
- Hybrid Morph — moves values but fades structural changes.
- Wavetable Morph — blends tables before audio.
- Warm Wavetable Morph — equal-power table blend plus smoothing.
- Softened Morph — more rounded middle.
- Saturated Morph — gentle compression/glue.
- Comb Wavetable Morph — hollow cyclic interference.
- FFT Spectral Morph — rebuilds tables from harmonic data.
- Spectral Envelope Morph — smoother spectral envelope movement.
- Harmonic Level Morph — stable phase with changing harmonic levels.

### Loop safety

The Loop Safety panel (Morph and Bulk tabs) checks whether the current render settings will produce a file that can loop cleanly:

- Reports whether samples per cycle is an integer.
- Warns about continuous morphing, phase-offset modes, and different Wavetable Types or engines at the endpoints.
- Suggests loop-safe render frequencies and sample-count substitutions.
- In Bulk, offers a loop-safe export mode targeting 256, 512, or 1024 samples per cycle.

### Morph curve

The morph curve shapes the timing of START→END transitions before parameter interpolation:

- **Bend** — eases in or out, placing more of the transition earlier or later. Signed −127..+127 with 0 as linear.
- **S-curve** — accelerates through the middle and eases at both ends (positive) or the reverse (negative).

The curve is shared across all morph render modes and Bulk export. It is now persisted to the global settings cookie.

### Import and export

- **WAV export** — mono, with user-selected sample rate (44.1, 48, 88.2, 96, 176.4, or 192 kHz), bit depth (8, 12-in-16, 16, 24, or 32-bit PCM), DC removal, normalisation mode, headroom, and a choice of export length in cycles, seconds, or samples.
- **JSON sidecar** — accompanies each WAV with patch parameters, export settings, file provenance, and a shared export ID that links the WAV and JSON pair. Schema v11 as of v2.7.
- **JSON patch import** — any exported JSON (schema v7 or later) can be loaded back into a Playground, Morph START, Morph END, or cross-loaded endpoint.
- **Audio-to-Drawn import** — imports any browser-decodable audio file into the current patch's Drawn Waveform source.

Very long renders are allowed, but browser export is still memory-bound. WAV files are generated in browser memory before download. Large renders can stress RAM, CPU time, browser Blob/download limits, storage writes, filesystem limits, and classic RIFF/WAV size limits.

### Save slots

Each of the three patch contexts (Playground, START, END) has four RAM-only save slots. Slots hold a snapshot of the full patch state. They survive in-session control rebuilds because the data lives in the application state object, not in the DOM. Slots are **not** persisted to localStorage; they are scratch memory only.

### Visualisers

Five visualiser views are available from both Playground and Morph:

- **Base table** — the raw 256-sample generated wavetable.
- **Phase-locked FM/PWM/Ring Mod** — the modulated waveform at a fixed phase.
- **Animated stable FM/PWM/Ring Mod** — the modulated waveform animated over time.
- **Time-domain FM/PWM/Ring Mod** — the rendered time-domain signal.
- **Spectrum analyser** — FFT magnitude spectrum of the rendered output.

---

## License and provenance

The current browser implementation is released under the GNU General Public License version 2 only: `SPDX-License-Identifier: GPL-2.0-only`

Fractured Wavetable began as an exploration inspired by the Fractal Wavetable Generator concept attributed to Carl Hudson / tonysnail. Carl Hudson's original C implementation is separately copyrighted and is not relicensed by the browser version's GPL notice.

Older Python/Tkinter and original/upstream files in this repository may have different provenance. Do not assume that the browser GPL notice automatically relicenses older or inherited code.

This README is a practical provenance note, not legal advice.

---

## Credits

Fractured Wavetable Generator was directed and developed by Atom Smasher with heavy AI-assisted programming (vibe coding), iterative listening, testing, renaming, fixing, and expansion.

---

## Use cases

### Wavetable synthesis

A one-cycle or short-cycle export can become oscillator material for wavetable synths, samplers, keygroups, or resynthesis workflows. Longer multi-cycle exports can become scanning material or evolving wavetable-like sources.

### Vector and crossfade instruments

Several exported WAVs can become corners or layers in a vector-style instrument. One export might be bright, another hollow, another noisy, another mellow. These can be crossfaded with an XY pad, mod wheel, velocity, envelope, LFO, or DAW automation.

### Subtractive synthesis source material

Instead of starting a subtractive patch with a saw, square, or triangle, Fractured Wavetable can provide a more complex oscillator source before the filter even starts working.

### Sampler-based synthesis

Short files can become one-shots. Medium files can become keygroup sources. Long files can become drones, atmospheres, or resampling beds.

### Drones and long-form sound

The same generator can produce long, sustained files for drone, ambient, installation, noise, and resampling work. In this workflow, the WAV file may be the finished sound rather than a source for another synthesizer.

### Growls, clangs, and hostile digital sounds

Fractured Wavetable is good at sounds that feel like they are misbehaving: folded, bitten, unstable, almost broken, or harmonically overcrowded.

### Shoegaze, ambient, and blurred textures

The project is not only for harsh sounds. Some modes and settings soften, blur, smear, hollow out, or round the waveform. These outputs can be layered with guitars, pads, reverbs, chorus, shimmer, tape-style effects, or granular clouds.

### Learning sound design

The tool exposes waveform generation, modulation, filtering, visualisation, playback, export, and sample-planning maths in one place. That makes it useful for learning relationships that are often taught separately.

### Learning to code

The project is also a code-learning object. It was developed through fast, conversational, AI-assisted iteration. The current file is heavily commented so a reader can follow the path from controls to state, generation, audio, visualisation, calculator maths, export, WAV writing, ZIP packaging, persistent settings, JSON metadata, patch import, and the AudioWorklet engine.

Comments marked `TUTORIAL` explain intent and data flow without changing executable behaviour. The code should not be treated as ideal architecture — it is a prototype, a lab notebook, and a tutorial. That is part of its value.

### Persistent global settings

The hosted browser version saves common global preferences in a browser cookie, with `localStorage` as a fallback for local-file testing or stricter cookie contexts.

## Running the older Python version

The Python version is a desktop prototype using Tkinter and PortAudio/sounddevice.

Example setup on Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install numpy sounddevice
python fractal_wt_start_end_morph_tabs_v9_streaming_export.py
```

On some Linux systems you may also need Tkinter and PortAudio development packages:

```bash
sudo apt install python3-tk portaudio19-dev
```

The Python version remains useful as an annotated development history and for understanding the evolution of the project, but the browser version is the cleaner current direction.
