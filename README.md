# Fractal Wavetable

**A tool for sound designers, sound explorers, and synthesizer programmers.**

**A real-time fractal wavetable instrument for strange waveforms, performable noise, and learnable code.**

Fractal Wavetable is an experimental Python/Tkinter synthesizer and wavetable laboratory. It began as a live, Linux-friendly adaptation of the Carl Hudson / tonysnail Fractal Wavetable Generator idea, then grew into a hands-on sound-design tool for exploring recursive waveform generation in real time.

This project is also openly **vibe-coded**. It was developed through conversational, iterative AI-assisted programming: try an idea, run it, listen, find the weird failure, patch it, rename things, add safety rails, and keep going. That process is part of the project’s character. The current code should be understood as an exploratory prototype and annotated development history, not a clean-room rewrite or a polished software-engineering artifact.

The project is deliberately somewhere between instrument, laboratory, and tutorial. It is not trying to be another polished subtractive synth. It is for making waveforms that feel discovered rather than selected: harsh, folded, glitching, metallic, unstable, mellowed, blurred, bitten down, or oddly alive.

## Important provenance and license note

This project is based on, adapted from, or inspired by earlier Fractal Wavetable Generator code attributed to Carl Hudson / tonysnail. The original code appears to have been published as **“all rights reserved”** rather than under an open-source license.

That matters.

This repository should not pretend that the whole project is cleanly open source unless and until the provenance is resolved. I do not claim exclusive rights over the original work, the original algorithmic expression, or any copied/adapted portions of the original implementation. The AI-assisted additions, UI work, safety systems, comments, export logic, and restructuring may contain new contributions, but those contributions do not erase the rights status of the original material.

For now, treat this repository as **source-available for study, discussion, and personal experimentation**, not as a formally licensed open-source project. Do not assume that you have permission to redistribute, relicense, package, sell, or commercially reuse the code.

Before any formal release, package, plugin, or permissive license, the responsible next step is to resolve licensing. That may mean contacting the original author, obtaining permission, removing/replacing restricted material, or doing a proper clean-room rewrite of the affected parts.

This README is not legal advice. It is a practical disclosure so that the project does not overclaim ownership or mislead contributors/users about reuse rights.

## Why this exists

Most wavetable instruments start from a library of finished waveforms. Fractal Wavetable starts from a small recursive process and lets the performer push that process around.

The original idea was simple: take a few seed values, feed them through short recurrence patterns, and turn the result into a single-cycle wavetable. The interesting part is that small changes of input values can produce disproportionate changes in the output waveform. That makes the instrument useful for techno, noise, drones, digital percussion, sample-source creation, and any situation where “too clean” is the wrong answer.

The project has also become a way to learn. The code is meant to be readable enough that someone can trace the path from GUI control, to wavetable generation, to safety checks, to audio callback, to visualisation, to export. The comments should increasingly explain not just what the code does, but why it does it.

## What it is

Fractal Wavetable is currently a standalone real-time Python instrument with:

* recursive wavetable generation from a small set of seed/growth controls;
* multiple wavetable-combination modes, including harsh, folded, bitwise, blended, and mellowing modes;
* live self-modulation through FM, PWM-like phase warping, and AM;
* source/result window controls for re-framing the generated cycle;
* pitch controls by frequency or note/octave;
* bounded FM ranges from subtle cents to octave-wide movement;
* single-cycle and modulation-aware waveform visualisation;
* output bit-depth and sample-rate controls;
* Drone, Stop, and Play (Timer) transport modes;
* randomisation aimed at sound discovery rather than total chaos;
* START/END morphing for moving between generated states;
* bulk-export workflows for creating folders of source material;
* JSON sidecar metadata for exported morphs;
* safety systems to keep extreme settings from poisoning or stalling the audio engine.

The interface is intentionally direct. The point is to move controls and hear the recurrence respond.

## How the project got here

The project started as a small live version of a fractal wavetable generator: one oscillator, a few controls, and a real-time table display. From there it grew by following practical sound-design questions:

* Can the controls be made more meaningful than “Pot 1”, “Pot 2”, and “Pot 3”?
* Can the dangerous recurrence settings be made playable instead of crash-prone?
* Can the visualiser show the actual rendered modulation rather than a misleading static table?
* Can FM be bounded musically so it does not detune the oscillator into unusable sub-octave mush?
* Can single-cycle playback, timed playback, drone playback, and silence all coexist without killing the audio stream?
* Can randomisation support exploration without overwriting important performance settings?
* Can morphing and bulk export turn live discoveries into reusable sample material?
* Can the export JSON describe the patch clearly enough to be useful later?
* Can the code itself become a tutorial for how a real-time audio tool is built?

That history matters because the current design is not arbitrary. Most controls exist because some earlier version exposed a real musical, technical, or usability problem.

## Intended use cases

Fractal Wavetable can be used in two very different ways.

For some musicians, the exported WAV file is the finished sound: a drone, stab, growl, clang, loop, texture, riser, impact, noisy oscillator tone, or strange digital artefact that goes straight into a track.

For other musicians and sound designers, the exported WAV file is only the beginning. It becomes raw material for another instrument, sampler, granular processor, subtractive synth, wavetable synth, vector synth, DAW, hardware groovebox, or modular-style resampling chain.

The export controls are there because those two workflows need different kinds of files. A one-cycle or few-cycle WAV is useful as oscillator material. A short burst is useful as a transient, stab, or sampler hit. A longer morph is useful as an evolving texture. A minutes-long or hours-long file can become a drone bed, installation source, ambient layer, noise floor, or resampling archive.

### 1. Wavetable synthesis

One of the most direct uses is to export single-cycle or short-cycle WAV files for use in wavetable synths.

A one-cycle export can become a raw oscillator shape. A multi-cycle export can become a small wavetable-like sequence, especially when the START/END morph slowly moves between two related fractal states. Depending on the destination instrument, these files may be imported as single-cycle waves, chopped into frames, resynthesised, scanned, or simply used as source material for a custom wavetable.

This is where cycle-count export matters. If the output length is defined in cycles rather than only seconds, the exported file can be made musically and technically intentional: one exact cycle for oscillator use, 16 cycles for a small evolving tone, 256 cycles for a longer scan, and so on.

Possible uses:

* single-cycle oscillator waves;
* harsh digital wavetable frames;
* morphing START/END wavetable material;
* bit-crushed, folded, or aliased source waves;
* mellowed/blurred waves for softer wavetable patches;
* bass, lead, stab, drone, and pad oscillator sources.

### 2. Vector synthesis and crossfade instruments

The exported files can also be used as corners or layers in a vector-style instrument.

Instead of thinking of one exported WAV as “the sound”, a sound designer can export several related waves: one bright, one hollow, one noisy, one mellow, one metallic, one unstable. These can then be loaded into a sampler, synth, or DAW rack and crossfaded with an XY pad, velocity, modulation wheel, LFO, envelope, or automation lane.

This makes Fractal Wavetable useful for pseudo-vector synthesis even if it is not itself a vector synth. It becomes a source generator for the vector instrument downstream.

Possible uses:

* four WAVs as XY vector corners;
* several related waves as velocity layers;
* START and END exports as opposite ends of a macro control;
* bright/dark, stable/unstable, smooth/noisy, or clean/damaged sound pairs;
* evolving performance racks in Ableton, Bitwig, MPC, Deluge, Renoise, or hardware samplers.

### 3. Subtractive synthesis source material

Traditional subtractive synthesis usually starts with simple oscillator shapes: saw, square, pulse, triangle, noise. Fractal Wavetable can provide much stranger oscillator sources before the filter even starts working.

A fractal WAV can be loaded into a sampler or wavetable oscillator, then treated like the raw oscillator in a subtractive patch. Filter it, envelope it, saturate it, chorus it, distort it, or run it through a resonant low-pass, band-pass, comb filter, phaser, or formant filter.

This can be especially useful when a normal saw wave is too familiar. The fractal source can already contain asymmetry, grit, internal motion, partial instability, digital edges, or harmonic clusters. The subtractive patch then shapes that material rather than creating all the interest from scratch.

Possible uses:

* bass oscillators with unusual harmonic structure;
* aggressive leads and stabs;
* formant-like filter sweeps over complex sources;
* unstable pulse-like waves for PWM-style patches;
* noisy but pitched material for industrial or techno percussion;
* mellow source waves for pads, shoegaze layers, and blurred electronic textures.

### 4. Sampler-based synthesis

Fractal Wavetable is also a sample-source generator.

Short files can become one-shots. Medium files can become keygroup sources. Long files can become drones, atmospheres, or resampling beds. Multi-cycle files can be looped, sliced, keytracked, reversed, layered, or mapped across velocity zones.

For sampler-based synthesis, the exported WAV does not need to be beautiful by itself. It only needs to be interesting enough to survive further processing.

Possible uses:

* one-shot digital percussion;
* metallic hits and clangs;
* growls and bass shots;
* loopable drone tones;
* keygroup oscillator sources;
* velocity-layered noise/texture patches;
* reverse swells and transitions;
* resampled material for MPC, Octatrack, Deluge, Kontakt, Renoise, Simpler/Sampler, or hardware phrase samplers.

### 5. Drones and long-form sound

The same generator can also be used for long, sustained material.

A short cycle count is useful when the goal is oscillator design. A long duration is useful when the goal is time: drone, decay, fatigue, slow morph, background instability, or evolving texture. The export length can be set in seconds, minutes, or potentially much longer durations when the sound designer wants an extended file rather than a compact oscillator sample.

This makes the tool useful for:

* dark ambient drones;
* noise beds;
* installation audio;
* shoegaze layers;
* long intros and outros;
* slowly shifting electronic textures;
* resampling sessions where the interesting moment may happen later.

For this use, the WAV file may be the finished sound. It can be placed directly on a DAW timeline, faded in/out, filtered, reverbed, or layered under guitars, synths, field recordings, or percussion.

### 6. Growls, clangs, and hostile digital sounds

Fractal recurrence is good at sounds that feel like they are misbehaving: folded, bitten, unstable, almost broken, or harmonically overcrowded.

That makes it useful for sound designers looking for:

* bass growls;
* metallic clangs;
* FM-like snarls;
* digital scrape tones;
* alarm-like harmonics;
* synthetic impacts;
* glitch percussion;
* harsh oscillator sync-like edges;
* abrasive techno and industrial source material.

These sounds may be exported as short files and then treated with distortion, filtering, compression, convolution, pitch envelopes, granular processing, or sampler modulation.

### 7. Shoegaze, ambient, and blurred textures

The project is not only for harsh sounds. Some modes and settings are useful because they soften, blur, smear, or hollow out the waveform. These outputs can be layered behind guitars, pads, reverbs, tape-style effects, chorus, shimmer, or granular clouds.

For shoegaze and ambient use, the value may be in the texture rather than the pitch. A slightly unstable fractal tone through reverb, chorus, filtering, and saturation can become a pad-like bed or a ghost layer behind more conventional instruments.

Possible uses:

* blurred pad sources;
* chorus-friendly single-cycle waves;
* noisy shimmer layers;
* unstable organ-like tones;
* faint digital movement under guitars;
* long reverb sends;
* granular clouds;
* texture beds for intros, bridges, and transitions.

### 8. Electronic music source material

For electronic music more broadly, Fractal Wavetable is a way to make source material that does not sound like a stock preset.

It can be used for:

* techno stabs;
* electro and industrial percussion;
* IDM glitches;
* bass source waves;
* drone layers;
* transition effects;
* risers and falls;
* one-note hooks;
* noisy pads;
* sample-pack building;
* custom oscillator libraries.

The key idea is that the WAV output can sit at any point in the production chain. It can be the finished audio event, the oscillator inside another synth, the sample inside a sampler, the layer inside a vector patch, or the raw ingredient for later mangling.

### 9. Cycle-count exports as a sound-design tool

The cycle-count output option is not just a convenience. It is there for sound designers who think in waveform periods.

A file that is exactly 1 cycle long is a different object from a file that is 1 second long. A file that is exactly 64 cycles long is different again. Cycle-count export makes the file length relative to the oscillator itself, not just the wall-clock.

That matters for:

* clean single-cycle oscillator exports;
* short loopable waveform studies;
* controlled multi-cycle morphs;
* sample-accurate bursts;
* matching exported material to pitch/frequency;
* creating files that can be sliced into equal waveform periods;
* making consistent batches of related source material.

Seconds, minutes, and hours are useful when the exported file is meant to be listened to as audio. Cycles are useful when the exported file is meant to become material for another synthesis process.

### 10. WAV files as endpoints vs starting points

Fractal Wavetable does not assume one “correct” workflow.

For some users, the exported WAV is the endpoint:

* export a drone;
* export a hit;
* export a morph;
* drop it into a DAW;
* use it in the track.

For others, the exported WAV is a starting point:

* load it into a wavetable synth;
* map it across a sampler;
* layer it with other exports;
* scan it with an LFO;
* crossfade it in a vector patch;
* filter it subtractively;
* granularise it;
* stretch it;
* reverse it;
* distort it;
* resample it again.

Both workflows are valid. The point of the project is to generate strange, useful audio objects — some finished, some unfinished, all available for further abuse.

### 11. Understanding sound, waveforms, and synthesis

Fractal Wavetable is also useful for people who are trying to understand sound itself.

Because the program exposes waveform generation, modulation, visualisation, playback, and export in one place, it can help connect concepts that are often taught separately:

* what a single-cycle waveform is;
* how waveform shape relates to perceived tone;
* why sharp edges tend to create brighter harmonic content;
* how small changes in an oscillator source can produce large sonic changes;
* how FM, AM, and phase/pulse-width style modulation affect a sound;
* why a waveform can look simple but sound complex, or look complex but sound dull;
* why filtering, clipping, bit depth, sample rate, and interpolation matter;
* how a sample can function as both audio and oscillator material.

For someone learning synthesis, the tool gives a concrete way to see and hear the relationship between waveform, harmonic character, modulation, pitch, duration, and export format. It is not a replacement for a spectrum analyser, oscilloscope, textbook, or full synthesizer, but it can make those ideas less abstract.

A user can move one control, watch the waveform change, hear the result, export it, load it somewhere else, and compare what survives through another instrument or effect chain. That loop is useful for learning.

### 12. Learning sound design

The project can also be used as a sound-design exercise machine.

Instead of starting from named presets such as “bass”, “pad”, “lead”, or “pluck”, the user starts from raw behaviour: recurrence, growth, windowing, modulation, pitch range, bit depth, and duration. That encourages a different kind of listening.

A sound designer can ask:

* What makes this sound harsh?
* What makes this sound hollow?
* What makes this sound feel metallic?
* What makes this sound stable or unstable?
* What makes this waveform loop cleanly?
* What happens if this is filtered?
* What happens if this is stretched?
* What happens if this becomes a wavetable frame?
* What happens if this becomes a sampler source?
* What happens if this ugly sound is used quietly, behind something pretty?

That makes the project useful not only for producing sounds, but for practising how to listen to sounds.

### 13. Learning to code

Fractal Wavetable is also intended as a learning-code project.

The current script is not presented as ideal architecture. It is a visible, heavily commented development history. That is part of its educational value. A reader can see how a small idea grows into a larger tool: first a generator, then a live oscillator, then safety systems, then visualisation, then bounded modulation, then transport, then morphing, then export.

For someone learning to code, the project touches practical topics that are often hard to understand in isolation:

* Python functions and data flow;
* GUI controls and event callbacks;
* shared state between a GUI thread and an audio thread;
* real-time audio callback constraints;
* table lookup and interpolation;
* waveform normalisation;
* clipping and numerical safety;
* NaN/Inf protection;
* file export;
* JSON metadata;
* incremental refactoring;
* why prototypes become messy;
* why later cleanup matters.

The comments are intended to make the file readable as a tutorial, not just executable as a program. A learner should be able to follow a control from the GUI, through patch state, through wavetable generation, through the audio callback, and out to a WAV file.

This is also a useful cautionary example: vibe-coded prototypes can move quickly, but they need review, testing, cleanup, and licensing discipline before they become dependable tools.

## Design principles

### Playability over mathematical purity

The recurrence is allowed to be strange, but the audio engine should stay alive. Extreme settings are clipped, sanitised, widened, replaced, or push-started when necessary. The goal is not to preserve every mathematically possible failure mode; the goal is to keep the instrument playable.

### Visible signal flow

The visualiser is not just decoration. It should help explain what the instrument is doing: base table, rendered FM/PWM/AM, phase-locked modulation views, and time-domain previews each answer a different question.

### Meaningful labels

The project has moved away from anonymous “pots” toward labels such as Core Seed, 3-Step Growth, 9-Step Growth, Source Window Start/End, and Result Window Start/End. The aim is not to hide the weirdness, but to make the controls learnable.

### Randomisation with restraint

Randomisation should help find new sounds without destroying the whole performance context. Some controls are good randomisation targets; others are anchors. Frequency, FM pitch range, volume, and play timer are treated as anchors.

### Tutorial value over architectural cleanliness — for now

The current script has grown experimentally and is intentionally layered. Later functions override earlier functions; later sections patch class methods; old decisions remain visible instead of being fully erased. That is not ideal architecture, but it does show the development path.

Longer term, the generator, audio engine, GUI, preset/morph logic, and export tools should be separated into clearer modules.

### Honesty about AI assistance

The project does not hide that AI assistance was used heavily. That means the code should be reviewed carefully, especially around licensing, correctness, safety, and portability. It also means the comments should not pretend that every design decision came from a traditional solo development process.

## Current controls, conceptually

| Area                        | Purpose                                                                         |
| --------------------------- | ------------------------------------------------------------------------------- |
| Core Seed                   | Sets the central value that drives the recurrence.                              |
| 3-Step Growth               | Shapes the shorter recurrence path and many sharper/edgier behaviours.          |
| 9-Step Growth               | Shapes the longer recurrence path and many broader/secondary structures.        |
| Wavetable Type              | Chooses how the recurrence paths are used or combined.                          |
| Frequency / Note            | Sets the base pitch.                                                            |
| FM Self + Range             | Uses the waveform to modulate its own pitch within a bounded musical range.     |
| PWM Self                    | Warps the phase width of the waveform.                                          |
| AM Self                     | Uses the waveform to modulate its own amplitude.                                |
| Source Window               | Re-frames the source material before wavetable-type math.                       |
| Result Window               | Re-frames the result after wavetable-type math.                                 |
| Output Bit Depth            | Quantises the output for cleaner or grittier digital character.                 |
| Sample Rate                 | Selects the audio stream sample rate where supported.                           |
| Drone / Stop / Play (Timer) | Supports continuous sound, silence, or sample-counted timed playback.           |
| Randomise                   | Jumps selected sound-design parameters without overwriting performance anchors. |
| Morph                       | Moves between START and END states.                                             |
| Bulk Export                 | Generates/export batches for later auditioning and pruning.                     |

## What this is not

Fractal Wavetable is not yet a finished plugin, a commercial instrument, or a stable preset library. It is not designed to replace a conventional wavetable synth such as Vital, Serum, Pigments, or a hardware sampler.

It is also not currently a cleanly licensed open-source project. It is more like a small digital organism under glass: playable, inspectable, sometimes hostile, legally/provenance-aware, and useful because it does not always behave politely.

## Running it

The current prototype is a Python script using Tkinter for the interface and PortAudio/sounddevice for audio output.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install numpy sounddevice
python fractal_wt_start_end_morph_tabs_v7_tutorial_commented.py
```

On some Linux systems you may also need Tkinter and PortAudio packages from your distribution, for example:

```bash
sudo apt install python3-tk portaudio19-dev
```

Start with the volume low. Some settings can produce sharp, bright, or abrupt digital sounds.

## Project status

This is an active prototype. The important work right now is not only adding features, but making the project easier to understand, package, test, and extend.

Near-term priorities include:

* resolving provenance and licensing before any formal release;
* cleaning up the code structure into clearer modules;
* documenting the wavetable modes and parameter ranges;
* improving morph and bulk-export workflows;
* adding a clearer preset/schema format;
* making comments and examples more tutorial-like;
* packaging the program for easier non-developer use, only if licensing allows it;
* adding screenshots, audio examples, and short demo videos.

## Possible future directions

* Contact the original author or otherwise resolve reuse permission.
* Replace restricted inherited code with a clean-room implementation if needed.
* MIDI controller mapping for live hardware control.
* Export formats for common sampler and wavetable workflows.
* A more formal preset browser.
* Safer loudness management and metering.
* A cleaner headless/export mode.
* Cross-platform packaging.
* Plugin experiments, if the engine and licensing both become clean enough to justify it.

## Credits

Fractal Wavetable began as a live Python exploration inspired by the Carl Hudson / tonysnail Fractal Wavetable Generator idea. This project is an experimental continuation focused on real-time performance, sound design, learning, and export workflows.

Development of this prototype was heavily AI-assisted and conversationally directed. The resulting code reflects human intent, iterative listening/testing, and machine-assisted implementation.

## License

**No open-source license is currently granted.**

The original Fractal Wavetable Generator code appears to be “all rights reserved”. Because this project is derived from or adapted around that earlier work, I am not currently attaching a permissive license to the repository.

Until this is resolved, treat the repository as source-available for inspection, learning, and personal experimentation only. Reuse, redistribution, packaging, commercial use, or relicensing may require permission from the original rights holder and/or a clean replacement of the affected code.
