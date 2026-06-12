# Fractal Wavetable

**A tool for sound designers, sound explorers, and synthesizer programmers.**

Live online GPL v2 build is available here: https://atom-smasher.github.io/Fractal-Wavetable-Generator/

<img width="416" height="263" alt="image" src="https://github.com/user-attachments/assets/c7ae4db5-d0d6-4e71-9669-983b323d027f" />

---

## Outputs are yours

Any output from this tool is yours to use however you want.

The program’s GPL notice applies to the program itself; it does **not**, by itself, impose GPL licensing on generated audio, WAV files, JSON sidecars, samples, loops, drones, presets made from exported audio, or finished music.

---

## Intended use cases

Fractal Wavetable can be used in several different ways.

### Wavetable synthesis

A one-cycle or short-cycle export can become oscillator material for wavetable synths, samplers, keygroups, or resynthesis workflows. Longer multi-cycle exports can become scanning material or evolving wavetable-like sources.

Possible uses:

- single-cycle oscillator waves
- harsh digital wavetable frames
- mellow or blurred wavetable sources
- bass, lead, stab, drone, and pad oscillator material
- morphing START/END tables for custom wavetable construction

### Vector and crossfade instruments

Several exported WAVs can become corners or layers in a vector-style instrument. One export might be bright, another hollow, another noisy, another mellow. These can be crossfaded with an XY pad, mod wheel, velocity, envelope, LFO, or DAW automation.

Possible uses:

- four WAVs as XY vector corners
- START and END exports as macro endpoints
- related waves as velocity layers
- stable/unstable or smooth/noisy sound pairs

### Subtractive synthesis source material

Instead of starting a subtractive patch with a saw, square, or triangle, Fractal Wavetable can provide a more complex oscillator source before the filter even starts working.

Possible uses:

- bass oscillators with unusual harmonics
- aggressive leads and stabs
- resonant filter sweeps over complex sources
- noisy but pitched percussion material
- mellow source waves for pads and blurred textures

### Sampler-based synthesis

Short files can become one-shots. Medium files can become keygroup sources. Long files can become drones, atmospheres, or resampling beds.

Possible uses:

- metallic hits and clangs
- growls and bass shots
- loopable drone tones
- reverse swells and transitions
- sampler instruments for MPC, Octatrack, Deluge, Kontakt, Renoise, Ableton, Bitwig, or hardware phrase samplers

### Drones and long-form sound

The same generator can produce long, sustained files for drone, ambient, installation, noise, and resampling work. In this workflow, the WAV file may be the finished sound rather than a source for another synthesizer.

Possible uses:

- dark ambient drones
- noise beds
- shoegaze layers
- long intros and outros
- slowly shifting electronic textures
- resampling sessions where the interesting moment may happen later

### Growls, clangs, and hostile digital sounds

Fractal Wavetable is good at sounds that feel like they are misbehaving: folded, bitten, unstable, almost broken, or harmonically overcrowded.

Possible uses:

- bass growls
- metallic clangs
- FM-like snarls
- digital scrape tones
- synthetic impacts
- glitch percussion
- abrasive techno and industrial source material

### Shoegaze, ambient, and blurred textures

The project is not only for harsh sounds. Some modes and settings soften, blur, smear, hollow out, or round the waveform. These outputs can be layered behind guitars, pads, reverbs, chorus, shimmer, tape-style effects, or granular clouds.

Possible uses:

- blurred pad sources
- chorus-friendly single-cycle waves
- noisy shimmer layers
- unstable organ-like tones
- faint digital movement under guitars
- texture beds for intros, bridges, and transitions

### Learning sound design

The tool exposes waveform generation, modulation, filtering, visualisation, playback, and export in one place. That makes it useful for learning relationships that are often taught separately:

- what a single-cycle waveform is
- how waveform shape relates to tone
- why sharp edges create brighter harmonic content
- how FM, AM, and phase-width modulation affect sound
- why filtering changes both the sound and the spectrum
- why a WAV file can be a finished sound or oscillator material
- why cycle count matters when exporting sound-design source files

### Learning to code

The project is also a code-learning object. It was developed through fast, conversational, AI-assisted iteration. The current files are heavily commented so a reader can follow the path from controls to state, generation, audio, visualisation, export, WAV writing, ZIP packaging, and JSON metadata.

The code should not be treated as ideal architecture. It is a prototype, a lab notebook, and a tutorial. That is part of its value.

---

## Design principles

### Playability over mathematical purity

The generator is allowed to be strange, but the audio path should stay alive. Extreme settings are clipped, bounded, normalised, widened, cancelled, or replaced when necessary.

### Visible signal flow

The visualiser should help explain what the tool is doing. Base-table, phase-locked, time-domain, animated, and spectrum views answer different questions.

### Meaningful labels

The project has moved away from anonymous “pots” toward labels such as Primary Phase, Shape 1, Shape 2, Source Window, Result Window, and Wavetable Type.

### Randomisation with restraint

Randomisation should help find new sounds without destroying the whole context. Bulk randomisation targets sound-design parameters but leaves render/export format choices under user control. Filters are reset to Off for safer random batches.

### Outputs as sound-design objects

The WAV file can be the endpoint or the starting point. It can become a finished drone, a one-shot, a sampler source, a wavetable frame, a vector corner, or raw material for later mangling.

### Honesty about AI assistance

This project is openly vibe-coded. AI assistance was used heavily, under human direction, through iterative listening, testing, renaming, fixing, and expansion. That does not remove the need for review, testing, cleanup, and licensing discipline.

---

**A browser-native wavetable generator, morphing lab, and sound-design source tool.**

Fractal Wavetable began as an exploration of the Fractal Wavetable Generator idea attributed to Carl Hudson / tonysnail. It now contains two related but legally and technically distinct paths:

1. **The older Python/Tkinter version** — a live desktop prototype derived from, adapted around, or closely based on the earlier Fractal Wavetable Generator lineage.
2. **The newer HTML/JavaScript browser version** — a browser-native, single-file wavetable generator inspired by that lineage, but rebuilt as clean-room DSP experiments using ordinary waveforms, phase warping, folding, smoothing, saturation, bitwise/quantised collision, filtering, morphing, and spectral table reconstruction.

A local/offline version using Carl Hudson's code is:
https://github.com/atom-smasher/Fractal-Wavetable-Generator/blob/master/fractal_wt_start_end_morph_tabs_v9_streaming_export.py

A live online GPL v2 build is available here:
https://atom-smasher.github.io/Fractal-Wavetable-Generator/

The browser version can run from the live page or locally from the downloaded HTML file. No build step is required.

---

## Repository contents

This repository may contain files with different provenance and different reuse status.

| File / area | Status |
| --- | --- |
| `fractal_wavetable_clean_chaos_lab_v14.03.html` | Current browser version. Copyright © 2026 Atom Smasher. Released under GPL-2.0-only. |
| `fractal_wt_start_end_morph_tabs_v9_streaming_export.py` | Older Python/Tkinter desktop prototype. Useful historically and technically, but treat as provenance-sensitive because it descends from the earlier Fractal Wavetable Generator line. |
| `main.c` or other original/upstream files | Historical/original Fractal Wavetable Generator material attributed to Carl Hudson / tonysnail. Not relicensed by the browser GPL notice. |
| `example_WAV_files/` | Example outputs and sound-design material. |
| GitHub Pages live tool | Online browser build: `https://atom-smasher.github.io/Fractal-Wavetable-Generator/`. |

The browser version and the older Python version should not be treated as the same licensing object.

---

## License and provenance

### Browser version

The browser implementation is free software under the **GNU General Public License version 2 only**:

```text
SPDX-License-Identifier: GPL-2.0-only
```

If this project is redistributed as a repository or release package, include the GPL v2 license text in a `COPYING` file.

The browser implementation is inspired by Carl Hudson’s Fractal Wavetable Generator concept. Carl Hudson’s original C implementation is separately copyrighted and is **not** relicensed by the browser version’s GPL notice.

### Python and original-code lineage

The older Python/Tkinter version was developed as a live, Linux-friendly exploration of the Carl Hudson / tonysnail Fractal Wavetable Generator idea. Because it is closer to the older implementation and algorithmic lineage, treat that file as **source-available for study and personal experimentation unless the original rights status is resolved**.

Do not assume that the GPL notice attached to the browser implementation automatically relicenses the older Python file, the original C file, or any code copied or adapted from the original project.

This README is not legal advice. It is a practical disclosure so the project does not overclaim ownership or mislead users about reuse rights.

---

## What the browser version is

Fractal Wavetable v14.03 is a single-file browser instrument and export tool with four main tabs:

| Tab | Purpose |
| --- | --- |
| **Single Engine / Playground** | Build and audition one generated wavetable patch. |
| **START→END Morph Export** | Design two endpoint patches, audition the morph, and export forward/reverse WAV+JSON files. |
| **Bulk Random Export** | Generate batches of random START/END pairs for later auditioning and pruning. |
| **Notes** | Built-in usage, license, provenance, and export notes. |

The browser version is meant to sit somewhere between instrument, laboratory, sample generator, and tutorial. It is not trying to be a conventional subtractive synth or a polished commercial plugin. It is for making waveforms that feel discovered rather than selected: harsh, folded, glitching, metallic, unstable, mellowed, blurred, bitten down, or oddly alive.

---

## Browser v14.03 feature summary

### Generator engines

The current browser version uses clean-room DSP engines rather than directly porting the original recurrence:

- **Clean Chaos Smooth**
- **Clean Chaos Crunchy**
- **Phase Lattice Smooth**
- **Phase Lattice Crunchy**

The controls are intentionally learnable:

| Control | Meaning |
| --- | --- |
| **Primary Phase** | Main phase/seed position. |
| **Shape 1** | First source-wave shape and warp control. |
| **Shape 2** | Second source-wave shape and warp control. |
| **Wavetable Type** | Collision/combine stage for the two internal source waves. |
| **Source Window Start/End** | Reframes source material before wavetable-type math. |
| **Result Window Start/End** | Reframes the result after wavetable-type math. |

### Wavetable modes

v14.03 includes 40 wavetable type modes, including:

- source A / source B
- A/B splices
- sum, difference, multiply, divide
- quantised OR, XOR, AND, XNOR
- min, max, absolute difference
- fold sum and fold difference
- comparator and interleave
- average, mostly-A, mostly-B
- smooth average and smooth difference
- sine shaping, soft saturation, clipping
- triangle folding, root/power shaping
- zero-cross blending
- Hann-windowed shaping
- phase blur
- odd/even soft modes
- gated and sign-multiply modes

The goal is not one perfect oscillator. The goal is a wide field of sound-design source material, from mellow and rounded to harsh and damaged.

### Modulation

Each patch includes self-modulation controls:

- **FM** — waveform self-modulates pitch.
- **FM Pitch Range** — bounded musical FM range, from cents to multi-octave movement.
- **PWM** — phase-width warping for arbitrary wavetables.
- **AM** — waveform self-modulates amplitude.

The FM range is bounded so the oscillator stays musically controllable instead of collapsing into runaway pitch shifts or zero-frequency stalls.

### Filter

v14.03 adds a simple subtractive output filter stage:

- Off
- Low-pass
- High-pass
- Band-pass

Randomisation resets the filter to **Off** so random batches are less likely to disappear into silence.

### Pitch and MIDI

Pitch is shared across Playground, Morph, and Bulk:

- logarithmic frequency slider
- exact Hz numeric input
- note and octave selectors
- selected-note reference readout
- Web MIDI note input where the browser supports it

MIDI note input is gated: note-on starts or retunes the current drone, note-off releases it, and the most recently held note has priority.

### Visualisation

The visualiser can show:

- base generated table
- phase-locked FM/PWM/AM
- animated stable FM/PWM/AM
- time-domain FM/PWM/AM
- spectrum analyser

FM/PWM/AM views and the spectrum analyser show a **post-filter preview**. The base table shows the generated table before oscillator modulation and filtering. The spectrum analyser is a cyclic single-cycle analyser, not a room or microphone analyser.

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
- FFT Spectral Morph — rebuilds tables from harmonic data.
- Spectral Envelope Morph — smoother spectral envelope movement.
- Harmonic Level Morph — stable phase with changing harmonic levels.

### Export

The browser version exports mono WAV files and JSON sidecars.

Export controls include:

- render length by cycles, seconds, or samples
- sample rate
- bit depth:
  - 8-bit PCM
  - 12-bit PCM in 16-bit WAV
  - 16-bit PCM
  - 24-bit PCM
  - 32-bit PCM
- forward, reverse, or both directions
- separate WAV+JSON files or one ZIP
- DC removal:
  - Off
  - Whole File
  - Per Cycle
- normalisation:
  - Off
  - Whole File Peak
  - Per Cycle Peak
- headroom in dB
- JSON sidecar metadata
- schema version 3 metadata

Very long renders are allowed, but browser export is still memory-bound. WAV files are generated in browser memory before download. Large renders can stress RAM, CPU time, browser Blob/download limits, storage writes, filesystem limits, and classic RIFF/WAV size limits.

Press **ESC** to stop audio and cancel active rendering. Leaving a tab also mutes the current audition.

---

## Running the browser version

### Use the live online tool

Open the GitHub Pages version in a modern browser:

```text
https://atom-smasher.github.io/Fractal-Wavetable-Generator/
```

This is the easiest way to try the browser version without downloading anything.

### Run it locally

Download or clone the repository, then open the HTML file in a modern browser:

```text
fractal_wavetable_clean_chaos_lab_v14.03.html
```

No build step is required. The browser version is a single-file HTML/JavaScript tool, so the local file and the hosted page should behave the same when the same build is deployed.

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

---

## Project status

The browser version is the current focus.

Near-term priorities:

- keep the browser version GPL-2.0-only with clear notices
- include `COPYING` with the GPL v2 license text
- keep provenance notes visible
- avoid applying the browser GPL notice to older/inherited code
- add screenshots and short demo audio
- document the wavetable modes more completely
- improve examples and export recipes
- keep the live online tool current with the recommended browser build
- add browser compatibility notes
- split historical/provenance-sensitive code from current browser code more clearly
- consider a cleaner directory layout, such as `browser/`, `legacy-python/`, `original/`, and `example_WAV_files/`

Possible future directions:

- AudioWorklet live engine for lower-latency browser audio
- preset import/export
- more formal patch schema documentation
- sampler/wavetable export recipes
- additional filter types
- safer loudness metering
- better long-render warnings
- screenshots, GIFs, and demo videos
- curated example WAV packs

---

## Credits

Fractal Wavetable began as an exploration inspired by the Fractal Wavetable Generator attributed to Carl Hudson / tonysnail.

The browser implementation was directed and developed by Atom Smasher with heavy AI-assisted programming. The current browser version is a clean-room inspired wavetable generator and morphing lab, not a relicensing of Carl Hudson’s original C implementation.

The project exists for sound designers, sound explorers, synthesizer programmers, musicians, people learning synthesis, and people learning to code.
