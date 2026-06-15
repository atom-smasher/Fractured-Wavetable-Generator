# Fractured Wavetable

**A tool for sound designers, sound explorers, and synthesizer programmers.**

Live online GPL v2 build is available here: https://atom-smasher.github.io/Fractured-Wavetable-Generator/

<img width="416" height="263" alt="image" src="https://github.com/user-attachments/assets/c7ae4db5-d0d6-4e71-9669-983b323d027f" />

Fractured Wavetable Generator is for making oscillator waves, morphing START→END sounds, drones, growls, clangs, transitions, texture beds, sampler sources, and raw material for further synthesis or resampling.

---

Press **ESC** to stop audio and cancel active rendering. Leaving a tab also mutes the current audition.

---

## Outputs are yours

Any output from this tool is yours to use however you want.

The program’s GPL notice applies to the program itself; it does **not**, by itself, impose GPL licensing on generated audio, WAV files, JSON sidecars, samples, loops, drones, presets made from exported audio, or finished music.

---

## What it does

- Playground — build, randomise, and audition one generated wavetable patch.
- START→END Morph Export — design two endpoint patches, audition the transition, and export forward or reverse WAV+JSON files.
- Bulk Random Export — generate batches of random START/END pairs for later auditioning and pruning.
- Calculator — plan sample rate, samples per cycle, frequency, duration, tempo length, file size, and simple multisample maps.
- Visualisation — inspect the generated table, modulation behaviour, time-domain shape, and spectrum.
- Export — render mono WAV files with JSON metadata, using user-selected length, sample rate, bit depth, DC removal, normalisation, and headroom settings.

The browser version is a single-file HTML/JavaScript tool. No build step is required.

---

## Design approach

Fractured Wavetable favours artistic weirdness over mathematical or musical purity. It is meant to produce sounds that feel discovered rather than selected: harsh, folded, glitching, metallic, unstable, mellowed, blurred, bitten down, or oddly alive.

The project also tries to keep the signal path visible, use meaningful controls, randomise with restraint, and make exported WAVs useful as sound-design objects.

### Running it

Use the live online version, or download the current HTML file and open it in a modern browser.

Web Audio is required for auditioning. Web MIDI is optional and browser-dependent. Chromium-based browsers are usually the safest choice for Web MIDI.

Start with the volume low. Some settings can produce sharp, bright, or abrupt digital sounds.

---

## License and provenance

The current browser implementation is released under the GNU General Public License version 2 only:
`SPDX-License-Identifier: GPL-2.0-only`

Fractured Wavetable began as an exploration inspired by the Fractal Wavetable Generator concept attributed to Carl Hudson / tonysnail. Carl Hudson’s original C implementation is separately copyrighted and is not relicensed by the browser version’s GPL notice.

Older Python/Tkinter and original/upstream files in this repository may have different provenance. Do not assume that the browser GPL notice automatically relicenses older or inherited code.

This README is a practical provenance note, not legal advice.

---

## Credits

Fractured Wavetable Generator was directed and developed by Atom Smasher with heavy AI-assisted programming (vibe coding), iterative listening, testing, renaming, fixing, and expansion.

---

## Use cases

Fractured Wavetable can be used in several different ways.

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

The tool exposes waveform generation, modulation, filtering, visualisation, playback, export, and sample-planning maths in one place. That makes it useful for learning relationships that are often taught separately:

### Learning to code

The project is also a code-learning object. It was developed through fast, conversational, AI-assisted iteration. The current files are heavily commented so a reader can follow the path from controls to state, generation, audio, visualisation, calculator maths, export, WAV writing, ZIP packaging, persistent settings, and JSON metadata.

The code should not be treated as ideal architecture. It is a prototype, a lab notebook, and a tutorial. That is part of its value.

---

## Browser v2.4 feature summary

### Generator families and variants

Current generator families:

| Family | Smooth / Crunchy role |
| --- | --- |
| **Clean Chaos** | Rounded or grittier source-wave chaos. |
| **Phase Lattice** | Phase-warp/lattice harmonic motion, from root-stable to sharper folds. |
| **Phase Distortion** | Clean-room non-linear phase remapping, from rounded knees to harder edge motion. |
| **FM** | FM-style phase modulation baked into the generated table. |
| **Pulse Warp** | PWM-style duty-cycle, edge, notch, and offset shaping baked into the table. |
| **Sync** | Hard-sync-style reset shaping with constrained ratios and A/B waveshape controls. |
| **Formant** | Vowel-ish/formant-inspired harmonic ridges and nasal/growl movement. |
| **Fold** | Wavefolder-style drive, symmetry, and bias. |
| **Bitcrush** | Table-generation quantisation, sample-hold, dither/error bias, and foldback. |
| **Harmonic Cluster** | Additive harmonic density, rolloff, odd/even bias, and inharmonic clustering. |
| **Modal** | Bell, glass, metal, and resonator-style modal spacing. |
| **Feedback Sine** | Self-phase sine bending, feedback, saturation, and folding. |

Smooth variants generally favour rounded movement and stronger fundamental stability. Crunchy variants push harder phase warp, folding, quantisation, reset edges, feedback, or digital grit.

### Family-specific generator controls

The three main generator controls keep their internal names as `pot1`, `pot2`, and `pot3`, but the UI relabels them by generator family and updates those labels when the family changes.

| Family | Knob 1 | Knob 2 | Knob 3 |
| --- | --- | --- | --- |
| Clean Chaos | Seed / Phase | Source A Shape | Source B Shape |
| Phase Lattice | Phase Warp | Lattice A | Lattice B |
| Phase Distortion | Distortion Depth | Breakpoint / Knee | Wave Colour / Warp |
| FM | FM Index | Modulator Ratio | Carrier Colour |
| Pulse Warp | Duty Warp | Edge Slope | Notch / Offset |
| Sync | Sync Intensity | Source A Shape | Source B Shape |
| Formant | Formant Spacing | Vowel Position | Bandwidth / Grit |
| Fold | Fold Drive | Fold Symmetry | Source / Bias |
| Bitcrush | Bit Depth Loss | Sample Hold | Dither / Error Bias |
| Harmonic Cluster | Harmonic Density | Rolloff / Brightness | Odd / Inharmonic Bias |
| Modal | Modal Spacing | Brightness Weight | Metalness / Inharmonicity |
| Feedback Sine | Feedback Depth | Feedback Phase | Saturation / Folding |

The Sync family uses bounded bidirectional A↔B cross-coupling. Source A can shape Source B, and Source B can shape Source A, without using unstable recursive feedback.

### Wavetable modes (wave interpolation)

v2.4 exposes 55 visible wavetable type modes. Additional legacy modes remain loadable for older JSON files, but are hidden from the normal dropdown and randomisation pool when they overlap too strongly with clearer current modes.

Wavetable Type is the combine/collision stage for the two internal source waves. Visible groups include:

- Sources / Splices — Source A, Source B, A→B and B→A splice modes from single half-cycle splices up to ×16 block splices.
- Blend / Difference — Sum, Difference, Abs Difference, Smooth Average, Smooth Difference, Mostly A, Mostly B.
- Dynamic A/B Interaction — level, polarity, and threshold modes where one source opens, selects, or shapes the other.
- Compare / Edges — Min, Max, Comparator, and Edge.
- Multiply / Divide / Polarity — Multiply, A/B divide modes, A/B gated modes, and polarity-flip modes.
- Low-Bit Logic — Low-Bit OR, AND, and XOR.
- Folding / Shaping — Fold Sum, Fold Difference, Sine Shaper, Soft Clip Sum, Hard Clip Sum, Root Shape, and Power Shape.
- Symmetry / Harmonics — Odd Soft, Even Soft, Odd Hard, and Even Hard.
- Window / Interference — Zero-Cross Blend, Hann Windowed, and Cyclic Comb.

### Modulation

Each patch includes self-modulation controls:

- **FM** — waveform self-modulates pitch.
- **FM Pitch Range** — bounded musical FM range, from cents to multi-octave movement.
- **PWM** — phase-width warping for arbitrary wavetables.
- **Ring Mod** — a multi-ratio self-ring-mod stage that adds sidebands, clang, and phase-related grit.

The FM range is bounded so the oscillator stays musically controllable instead of collapsing into runaway pitch shifts. The old internal patch field for AM remains for JSON compatibility, but the current UI and render path use Ring Mod.

### Filter

The browser version includes a simple subtractive output filter stage.

Randomisation resets the filter to **Off** so random batches are less likely to disappear into silence.

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

### Visualisation

The visualiser can show:

- Base table — post wave interpolator; pre modulation.
- Phase-locked — post modulation.
- Slow animation — post modulation.
- Time-domain — post modulation.
- Spectrum analyser — post modulation.

Visual Mode and Animation FPS are universal visualiser preferences. Changing them in Playground or Morph updates the same shared setting and stores one persistent value.

The Slow animation view is a slow-motion visualisation of audio-frequency FM/PWM/Ring Mod effects. It is not real-time oscillator speed or playback speed. If FM, PWM, and Ring Mod are all zero, the Slow animation view is static and should not keep repainting.

### Persistent global settings

The hosted browser version saves common global preferences in a browser cookie, with `localStorage` as a fallback for local-file testing or stricter cookie contexts.

Saved settings include:

- frequency
- volume
- MIDI channel
- render unit and render length
- visual mode
- animation FPS
- sample rate
- bit depth
- Morph Render Mode
- FFT anchor count
- ZIP/separate download mode
- DC removal
- normalisation
- headroom dB
- Different START/END Wavetable Types
- Different START/END FM Pitch Range
- Cycle-stepped transition
- Calculator tab values while open
- last open tab

Patch-specific sound-design parameters are not treated as global cookie settings.

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

### Loop-safe bulk export

Bulk includes an optional **Phase-accurate loop-safe** mode aimed mainly at wavetable developers. It neutralises settings that can make a file fail simple phase-closure tests, then plans output around exact samples-per-cycle targets.

Current loop-safe targets are:

- 256 samples/cycle — native internal table match
- 512 samples/cycle — 2× oversampling
- 1024 samples/cycle — 4× oversampling

For one-shots, drones, risers, texture beds, and general sound design, it is usually fine to leave loop-safe mode off, render as needed or slightly longer than needed, then trim to a zero crossing later.

### Export

The browser version exports mono WAV files and JSON sidecars.

Export controls include render length by cycles, seconds, or samples; sample rate; bit depth; morph render mode; FFT anchor count; morph curve mode and amount; cycle-stepped transition; shared or separate START/END wavetable types; shared or separate START/END FM pitch ranges; forward, reverse, or both directions; separate WAV+JSON files or one ZIP; DC removal; normalisation; headroom in dB; and JSON metadata.

Very long renders are allowed, but browser export is still memory-bound. WAV files are generated in browser memory before download. Large renders can stress RAM, CPU time, browser Blob/download limits, storage writes, filesystem limits, and classic RIFF/WAV size limits.

---

## Running the browser version

### Use the live online tool

Open the GitHub Pages version in a modern browser - https://atom-smasher.github.io/Fractured-Wavetable-Generator/

This is the easiest way to try the browser version without downloading anything.

### Run it locally

Download or clone the repository, then open the current HTML file in a modern browser:

```text
fractured_wavetable_generator_v2_4.html
```

No build step is required. The browser version is a single-file HTML/JavaScript tool, so the local file and the hosted page should behave the same when the same build is deployed, except that cookie behaviour may differ for local `file://` testing. Local testing can use the `localStorage` fallback.

Notes:

- Web Audio is required for auditioning.
- Web MIDI is optional and browser-dependent.
- Chromium-based browsers are usually the safest choice for Web MIDI.
- Start with the volume low. Some settings can produce sharp, bright, or abrupt digital sounds.

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
