# Fractured Wavetable

**A tool for sound designers, sound explorers, and synthesizer programmers.**

Live online GPL v2 build: <https://atom-smasher.github.io/Fractured-Wavetable-Generator/>

<img width="416" height="263" alt="Fractured Wavetable Generator interface" src="https://github.com/user-attachments/assets/11c11f14-0969-4a1c-b18d-b283d4b32e90" />

Fractured Wavetable Generator is for making oscillator waves, morphing START→END sounds, drones, growls, clangs, transitions, texture beds, sampler sources, and raw material for further synthesis or resampling.

The current public release is **v2.9c**. The previous public release was **v2.8q**.

---

Press **ESC** to stop audio and cancel ordinary active rendering. During a Bulk render, ESC is context-sensitive: on the **Bulk** tab it cancels the batch; on another tab it stops audition or drone audio without interrupting the batch. Leaving a tab also mutes the current audition.

---

## Outputs are yours

Any output from this tool is yours to use however you want.

The program's GPL notice applies to the program itself; it does **not**, by itself, impose GPL licensing on generated audio, WAV files, JSON sidecars, samples, loops, drones, presets made from exported audio, or finished music.

---

## What it does

- **Playground** — build, randomise, and audition one generated wavetable patch, including its Drawn Waveform source.
- **START→END Morph Export** — design two endpoint patches, audition the transition, and export forward, reverse, or both directions as WAV + JSON.
- **Bulk Random Export** — create batches from one or more random presets, with per-file morph selection, wavetable-oriented guardrails, live status, and reproducible JSON sidecars.
- **Calculator** — plan sample rate, samples per cycle, frequency, duration, tempo length, file size, and simple multisample maps.
- **Visualisation** — inspect the generated table, modulation behaviour, time-domain shape, and spectrum.
- **Export** — render mono WAV files with JSON metadata, using user-selected length, sample rate, bit depth, DC removal, normalisation, headroom, and optional MPC wavetable-folder settings.
- **Import and retuning** — reload exported JSON as patches or templates, adjust the result by hand, and render a revised version.

The browser version is a single-file HTML/JavaScript tool. No build step is required.

---

## What's new in v2.9c

v2.9c promotes the experimental Bulk-tuning work after v2.8q into a release-facing workflow and tightens the behaviour of long browser renders.

- **Six Bulk random presets** — the visible preset ladder is now **Unrestricted**, **Cycle-coherent random**, **Classic Waveforms**, **Smooth**, **Edge**, and **Glitch & Noise**. One or more presets can be enabled; each iteration chooses from the checked set.
- **Curated sound identities** — Classic Waveforms, Smooth, and Edge provide increasingly coloured but frame-coherent wavetable material. Glitch & Noise merges the strongest glitch, noise, and damaged-material recipes into one character-first preset. Unrestricted remains the broad discovery mode.
- **Current-file status** — Bulk export now shows the resolved preset, morph mode, endpoints, and other per-file choices while the batch is running.
- **Locked render plans** — Bulk settings are snapshotted at the start of a batch. Shared render controls in Bulk, Morph, Playground, and Calculator are locked until the batch ends, so edits cannot leak into the file currently rendering. Morph forward/reverse audition and export are also locked; START and END endpoint drones remain available.
- **Context-sensitive ESC** — ESC cancels an active Bulk batch from the Bulk tab, but only silences audition audio when pressed from another tab during that batch.
- **Richer JSON import and provenance** — sidecars can be loaded as endpoint patches, complete Morph templates, Morph settings only, render settings only, or Morph + render settings only. Copy, swap, import, hand-tuning, and RAM-slot lineage are recorded more clearly.
- **MPC export refinements** — MPC auto-naming and export matching are more reliable, sidecars use a separate `_json` folder, and Bulk MPC export keeps its cycle-coherent frame requirements.

---

## Design approach

Fractured Wavetable favours artistic weirdness over mathematical or musical purity. It is meant to produce sounds that feel discovered rather than selected: harsh, folded, glitching, metallic, unstable, mellowed, blurred, bitten down, or oddly alive.

The project also tries to keep the signal path visible, use meaningful controls, randomise with restraint, and make exported WAVs useful as sound-design objects.

### Running it

Use the live online version, or download the current `index.html` file and open it in a modern browser.

Web Audio is required for auditioning. Web MIDI is optional and browser-dependent. Chromium-based browsers are usually the safest choice for Web MIDI.

Start with the volume low. Some settings can produce sharp, bright, or abrupt digital sounds.

---

## Current feature summary — v2.9c

### Generator families and variants

Each generated patch uses a Family × Variant combination. Playground has one patch; Morph has independently editable START and END patches. Bulk presets may deliberately constrain or correlate endpoint choices.

| Family | Smooth | Crunchy |
| --- | --- | --- |
| **Clean Chaos** | Rounded phase motion, softer drive, stable fundamentals | Stronger phase warp, harder shaping, richer digital grit |
| **Classic Waveforms** | Predictable sine, triangle, saw, trapezoid, square, and pulse structures | Harder edges and brighter variants of the same topology controls |
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

Each engine exposes three family-specific controls. The interface and Notes tab show their current names and purpose.

### Classic Waveforms presets

Classic Waveforms is a stable, deliberately understandable source family. Eight patch buttons provide clean landmarks:

- Sine
- Triangle
- Saw down
- Saw up
- Trapezoid
- Square 50%
- Pulse 25%
- Pulse 12.5%

The underlying Topology, Symmetry / Width, and Character controls can move continuously between those landmarks.

### Source Taps and Wavetable Type

Each generator produces four related taps: **A**, **B**, **C**, and **D**. Input 1 and Input 2 choose which two taps feed the Wavetable Type stage.

A fifth tap — **Drawn** — is the per-patch hand-drawn or audio-imported 256-sample table. It can be assigned to Input 1 or Input 2 like any other tap, or excluded from random tap assignment.

The Wavetable Type stage combines the two inputs into the final table. Available types include source selection, repeated splices, sums and differences, dynamic A/B interactions, multiply/divide/polarity operations, logic modes, folding and shaping, windows, and interference structures.

### Drawn Waveform source

Each patch — Playground, START, and END — contains a 256-sample Drawn Waveform:

- **Hand-drawn** by clicking or dragging on the canvas.
- **Audio-imported** from any audio format the browser can decode. Long files are compressed across the table; short files are stretched. Flat or silent imports are rejected.
- **Seam mode** and **Smoothing** are applied non-destructively at audition/render time, so preview changes do not overwrite the stored drawing.
- Untouched sine or flat Drawn tables are excluded from ordinary random tap assignment. Only meaningful Drawn tables are eligible.

### Modulation — FM, PWM, and Ring Mod

The oscillator can apply three modulation stages on top of the generated table:

- **FM** — self-frequency modulation with a musical pitch range in cents. START and END can share one range or use different ranges.
- **PWM** — phase-width warping that skews the waveform asymmetrically without rewriting the underlying table.
- **Ring Mod** — a multi-partial ring-modulation and soft-distortion stage after FM and PWM.

Standard morph paths can move these controls between START and END. Dedicated START Phase-Modulates END modes instead use START as a phase-modulation source and END as the audible carrier.

### Filter

A resonant biquad filter — Low-pass, High-pass, or Band-pass — is available after oscillator modulation.

Bulk behaviour depends on the selected preset. Frame-coherent presets may neutralise filters and other destabilising controls; Glitch & Noise may preserve recipe-required modulation because tearing, discontinuity, and unstable phase motion are intentional material.

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
- recommended frequency for common samples-per-cycle values
- exact duration and total sample count from cycle count
- nearest equal-tempered note and pitch offset
- mono WAV data-size estimates by bit depth and file count
- tempo/bar-length renders for risers, transitions, drones, and morphs
- rough multisample maps by MIDI range, zone spacing, and seconds per root note

The native internal table is 256 samples. Higher samples-per-cycle targets oversample the rendered/interpolated result and create larger files; lower values create smaller files but preserve less cycle detail.

### Source and Result Windows

Each patch has a Source Window before the Wavetable Type stage and a Result Window after it. Both use a 0–256 scale matching the internal table. Narrowing a window changes which part of the source contributes and can create truncated, gated, repeated, or edge-emphasised textures.

A truly flat or unusable table is not amplified into a false full-scale waveform. During rendering, the safety system first tries to hold a previous valid table from the same local context; if none exists, it substitutes a deterministic safety table. Corrections are summarised in the interface and JSON rather than repeated for every frame.

### Morphing

The Morph tab supports START→END design, audition, and export. Render modes are grouped by strategy:

- **Direct / Parameter** — Full Interpolation, Dual-Path Interpolation, and Hybrid Morph.
- **Crossfade / Phase** — Crossfade, Phase-Aligned Crossfade, and Phase-Offset Crossfade.
- **Wavetable / One-Oscillator** — Wavetable, Warm Wavetable, Softened, Saturated, and Comb Wavetable Morph.
- **START Phase-Modulates END — Moving Values** — START and END continue moving through the morph while START warps END phase; rate/depth variants include 1:1, ±180°, ±360°, ×1/5, ×1/3, ×1/2, ×2, ×3, and ×5.
- **START Phase-Modulates END — Fixed Endpoints** — START remains the fixed modulation source and END the fixed audible carrier. Forward and reverse renders are identical because the endpoints do not interpolate.
- **Spectral / FFT** — FFT Spectral Morph, Spectral Envelope Morph, and Harmonic Level Morph.

FFT Spectral Morph supports 2, 3, 5, or 9 analysed table states. The Bulk presets can choose different anchor pools according to their intended texture.

### Morph curve

The morph curve shapes transition timing before parameter interpolation:

- **Bend** — eases in or out, placing more of the transition earlier or later. Signed −127..+127 with 0 as linear.
- **S-curve** — accelerates through the middle and eases at both ends, or applies the inverse response for negative values.

The curve is shared by Morph and Bulk and is persisted with the other global settings.

### Loop safety, cycle coherence, and MPC mode

The Loop Safety panel checks whether the current render settings are structurally suitable for clean looping. It reports sample/cycle relationships, identifies settings likely to prevent loop closure, and suggests compatible frequencies or sample counts.

Bulk adds preset-level structural intent:

- **Cycle-coherent random**, **Classic Waveforms**, **Smooth**, and **Edge** are designed for frame-coherent wavetable material.
- **Unrestricted** begins with broad random material, subject to table safety and any export-mode guardrails.
- **Glitch & Noise** is character-first and may intentionally break frame coherence.

**MPC Wavetable Mode** creates an MPC-style folder with WAV files and a matching `format.json`. Bulk MPC export enforces cycle-coherent wavetable structure and writes FWG sidecars to a separate `_json` folder. Exact display, sorting, and edge-case naming behaviour can still vary by MPC firmware and should be checked on the target device.

### Bulk Random Export

Bulk can draw from any checked combination of six release-facing presets:

- **Unrestricted** — full-range general random generation for discovery, pads, one-shots, drones, transitions, and resampling.
- **Cycle-coherent random** — broad but stable source material with wavetable-oriented neutralisation and a conservative random morph pool.
- **Classic Waveforms** — stable A-only Classic recipes, Tap A→A, one guaranteed large primary-pot movement, and a curated morph pool that includes integer Moving START Phase-Modulates END variants.
- **Smooth** — mostly Classic-based material with clean crossfade and FFT movement, plus a low-weight two-source branch.
- **Edge** — brighter and more animated frame-coherent material, controlled splices, stronger colour, and a wider morph pool.
- **Glitch & Noise** — broken, unstable, noisy, and digitally damaged material. JSON records whether FM was active on both endpoints, neither endpoint, START only, or END only.

At batch start, the selected pools, naming, pitch, render, morph, FFT, normalisation, and MPC settings are snapshotted. Controls that write those shared settings remain locked until the batch ends. The Current file panel shows the actual choices used by the file being rendered.

### Import and export

- **WAV export** — mono, with sample rates of 44.1, 48, 88.2, 96, 176.4, or 192 kHz; 8, 12-in-16, 16, 24, or 32-bit PCM; DC removal; normalisation; headroom; and length in cycles, seconds, or samples.
- **JSON sidecar** — accompanies each WAV with patch parameters, morph settings, render settings, safety notes, provenance, and an export UUID linking the pair. The current export schema is **12**.
- **JSON patch import** — exported sidecars can load START or END into Playground or Morph, cross-load an endpoint, or load both endpoints together.
- **Morph template import** — loads START, END, Morph settings, and render/export settings for reconstruction, hand-tuning, and re-rendering.
- **Settings-only import** — imports Morph settings, render settings, or both while leaving the current endpoint patches in place.
- **Audio-to-Drawn import** — imports browser-decodable audio into the current patch's Drawn Waveform source.
- **MPC Wavetable export** — writes an MPC-style WAV folder with `format.json` and optional FWG sidecars in a sibling `_json` folder.

Very long renders are allowed, but browser export is still memory-bound. WAV files and ZIP packages are built in browser memory before download. Large renders can stress RAM, CPU time, Blob/download limits, storage writes, filesystem limits, and classic RIFF/WAV size limits.

### Save slots

Each patch context — Playground, START, and END — has four RAM-only save slots. Slots hold a snapshot of the full patch state and its provenance. They survive in-session control rebuilds because the data lives in application state, not in the DOM. Slots are **not** persisted to localStorage; they are scratch memory only.

### Visualisers

Five visualiser views are available from Playground and Morph:

- **Base table** — the raw 256-sample generated wavetable.
- **Phase-locked FM/PWM/Ring Mod** — the modulated waveform at a fixed phase.
- **Animated stable FM/PWM/Ring Mod** — the modulated waveform animated over time.
- **Time-domain FM/PWM/Ring Mod** — the rendered time-domain signal.
- **Spectrum analyser** — FFT magnitude spectrum of the rendered output.

### Persistent global settings

The hosted browser version saves common global preferences in a browser cookie, with `localStorage` as a fallback for local-file testing or stricter cookie contexts. The last open tab is also remembered locally.

---

## License and provenance

The current browser implementation is released under the GNU General Public License version 2 only: `SPDX-License-Identifier: GPL-2.0-only`

Fractured Wavetable began as an exploration inspired by the Fractal Wavetable Generator concept attributed to Carl Hudson / tonysnail. Carl Hudson's original C implementation is separately copyrighted and is not relicensed by the browser version's GPL notice.

Older Python/Tkinter and original/upstream files in this repository may have different provenance. Do not assume that the browser GPL notice automatically relicenses older or inherited code.

This README is a practical provenance note, not legal advice.

---

## Credits

Fractured Wavetable Generator was directed and developed by Atom Smasher with heavy AI-assisted programming, iterative listening, testing, renaming, fixing, and expansion.

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

---

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
