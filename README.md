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

- Playground — build and audition one generated wavetable patch.
- START→END Morph Export — design two endpoint patches, audition the transition, and export forward or reverse WAV+JSON files.
- Bulk Random Export — generate batches of random START/END pairs for later auditioning and pruning.
- Visualisation — inspect the generated table, modulation behaviour, and spectrum.
- Export — render mono WAV files with JSON metadata, using user-selected length, sample rate, bit depth, DC removal, normalisation, and headroom settings.

The browser version is a single-file HTML/JavaScript tool. No build step is required.

---

## Design approach

Fractured Wavetable favours weirdness over mathematical or musical purity. It is meant to produce sounds that feel discovered rather than selected: harsh, folded, glitching, metallic, unstable, mellowed, blurred, bitten down, or oddly alive.

The project also tries to keep the signal path visible, use meaningful controls, randomise with restraint, and make exported WAVs useful as sound-design objects rather than temporary previews.

---

### Running it

Use the live online version, or download the current HTML file and open it in a modern browser.

Web Audio is required for auditioning. Web MIDI is optional and browser-dependent. Chromium-based browsers are usually the safest choice for Web MIDI.

Start with the volume low. Some settings can produce sharp, bright, or abrupt digital sounds.

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

Instead of starting a subtractive patch with a saw, square, or triangle, Fractured Wavetable can provide a more complex oscillator source before the filter even starts working.

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

Fractured Wavetable is good at sounds that feel like they are misbehaving: folded, bitten, unstable, almost broken, or harmonically overcrowded.

Possible uses:

- bass growls
- metallic clangs
- FM-like snarls
- sync-like rips
- formant-ish growls
- bitcrushed and folded source tones
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
- how phase distortion, sync, folding, pulse-width shaping, and formant-style shaping can be baked into a wavetable
- why filtering changes both the sound and the spectrum
- why a WAV file can be a finished sound or oscillator material
- why cycle count matters when exporting sound-design source files

### Learning to code

The project is also a code-learning object. It was developed through fast, conversational, AI-assisted iteration. The current files are heavily commented so a reader can follow the path from controls to state, generation, audio, visualisation, export, WAV writing, ZIP packaging, persistent settings, and JSON metadata.

The code should not be treated as ideal architecture. It is a prototype, a lab notebook, and a tutorial. That is part of its value.

---

## Browser v2 feature summary

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

v2.0a includes 43 wavetable type modes. Wavetable Type is the combine/collision stage for the two internal source waves.

Modes include:
- Source A, Source B
- B→A Splice, A→B Splice
- Sum, Difference
- Multiply, Divide
- Quantised OR, Quantised XOR, Quantised AND, Quantised XNOR
- Min, Max
- Abs Difference
- Fold Sum, Fold Difference
- Comparator, Interleave
- Edge
- Average, Mostly A, Mostly B
- Primary Phase Crossfade
- Smooth Average, Smooth Difference
- Sine Shaper
- Soft Saturate
- Triangle Fold Soft
- Root Shape, Power Shape
- Zero-Cross Blend
- Hann Windowed
- Phase Blur
- Odd Soft, Even Soft, Odd Hard, Even Hard
- Gated B
- Sign Multiply
- Soft Clip Sum, Hard Clip Sum
- Cyclic Comb.

### Modulation

Each patch includes self-modulation controls:

- **FM** — waveform self-modulates pitch.
- **FM Pitch Range** — bounded musical FM range, from cents to multi-octave movement.
- **PWM** — phase-width warping for arbitrary wavetables.
- **AM** — waveform self-modulates amplitude.

The FM range is bounded so the oscillator stays musically controllable instead of collapsing into runaway pitch shifts.

### Filter

The browser version includes a simple subtractive output filter stage:

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

Visual Mode and Animation FPS are universal visualiser preferences. Changing them in Playground or Morph updates the same shared setting and stores one persistent value.

FM/PWM/AM views and the spectrum analyser show a **post-filter preview**. The base table shows the generated table before oscillator modulation and filtering. The spectrum analyser is a cyclic single-cycle analyser, not a room or microphone analyser.

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

### Export

The browser version exports mono WAV files and JSON sidecars.

Export controls include render length by cycles, seconds, or samples; sample rate; bit depth; morph render mode; FFT anchor count; morph curve mode and amount; cycle-stepped transition; shared or separate START/END wavetable types; shared or separate START/END FM pitch ranges; forward, reverse, or both directions; separate WAV+JSON files or one ZIP; DC removal; normalisation; headroom in dB; and schema version 4 JSON metadata.

Very long renders are allowed, but browser export is still memory-bound. WAV files are generated in browser memory before download. Large renders can stress RAM, CPU time, browser Blob/download limits, storage writes, filesystem limits, and classic RIFF/WAV size limits.

---

## Running the browser version

### Use the live online tool

Open the GitHub Pages version in a modern browser:

```text
https://atom-smasher.github.io/Fractured-Wavetable-Generator/
```

This is the easiest way to try the browser version without downloading anything.

### Run it locally

Download or clone the repository, then open the HTML file in a modern browser:

```text
fractal_wavetable_clean_chaos_lab_v14.15.html
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
