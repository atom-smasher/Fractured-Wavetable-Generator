# Fractured Wavetable Generator — Example MPC Wavetables

This directory contains example wavetables generated with
[Fractured Wavetable Generator](https://github.com/atom-smasher/Fractured-Wavetable-Generator/).

They are packaged for the **User Wavetables** feature in **MPC 3.9 or later**. Each `.wav` file is also an ordinary mono WAV file, so it can be used as a normal sample in an MPC, another sampler, a DAW, or an audio editor.

## Install as MPC User Wavetables

Copy the supplied *morph folders into the `Oscillators/Wavetables/` folder, in the root of a drive that your MPC can browse, merging it with any existing `Oscillators` folder.

The resulting layout should look like this:

```text
<Drive>/
└── Oscillators/
    └── Wavetables/
        └── <LibraryFolder>/
            ├── <wavetable-001>.wav
            ├── <wavetable-002>.wav
            ├── ...
            └── format.json
```

Alternatively, copy each supplied `<LibraryFolder>` directly into:

```text
<Drive>/Oscillators/Wavetables/
```

Keep each `format.json` beside the WAV files it describes. Do not rename, edit, or remove it unless you also understand and preserve the wavetable geometry.

An MPC wavetable library folder should contain only its WAV files and `format.json`. Do not put this README, the licence file, Fractured Wavetable Generator sidecars, or additional nested folders inside it.

## Load a wavetable on the MPC

1. Connect or mount the drive containing the files.
2. Create or select a **Drum** or **Keygroup** track.
3. Open **Track Edit**.
4. Open the **Samples/Oscs** tab.
5. Set the required layer to **OSC** rather than **Sample**.
6. Open the oscillator source menu.
7. Browse to **User Wavetables → `<LibraryFolder>`**.
8. Select a WAV file.

After loading it:

- **Position** selects a waveform/frame within the table.
- **XFade** smooths transitions between adjacent frames.
- Modulating **Osc Position** with an LFO, envelope, controller, velocity, aftertouch, or another modulation source turns a static waveform into an evolving wavetable sound.

A simple starting patch is a Keygroup with the wavetable on Layer 1, moderate XFade, and an LFO routed to **Layer 1 → Osc Position**.

If the Wavetable oscillator or User Wavetables category is unavailable, make sure the MPC is running version 3.9 or later and that the required MPC oscillator content has been installed from **Preferences → Activations**.

## What `format.json` does

A wavetable WAV is one continuous audio file containing a fixed number of consecutive single-cycle waveforms. `format.json` tells the MPC how to divide that file:

```json
{
  "formatInfo": {
    "numSamplesPerSingleCycle": 2048,
    "numSingleCycles": 256
  }
}
```

In that example, the WAV must contain exactly:

```text
2048 samples per cycle × 256 cycles = 524288 samples
```

Folder names may include geometry such as `256x2048`, meaning **256 cycles of 2048 samples each**.

The supplied WAV files and manifests are already matched. You do not need to calculate or edit these values merely to use the files.

## Use the WAV files as normal samples

Nothing about the audio is locked to the MPC Wavetable oscillator. The WAV files can also be:

- loaded onto a Drum or Keygroup sample layer;
- auditioned or edited in Sample Edit;
- chopped, reversed, looped, pitched, stretched, layered, or resampled;
- placed on an audio track;
- opened in a DAW, software sampler, granular instrument, or audio editor.

For normal sample use, `format.json` is not needed.

Because each file contains a sequence of short waveform cycles, ordinary sample playback may produce a brief pitched tone, buzz, sweep, or transition rather than a conventional drum hit or instrument recording. Looping, stretching, pitching down, filtering, and resampling can make the material useful for drones, pads, one-shots, percussion, textures, and sound effects.

When a WAV is used as an ordinary sample, the sampler does not use the wavetable frame layout. Set its root note, tuning, loop points, and playback behaviour as you would for any other sample.

## Fractured Wavetable Generator JSON sidecars

Some downloads may include a separate sibling folder ending in `_json`. Those JSON files are **not required by the MPC** and should not be placed inside an MPC wavetable library folder.

They record the Fractured Wavetable Generator settings and provenance used to make each WAV. A sidecar can be imported back into the generator as a template, allowing the START and END patches, morph settings, or render settings to be adjusted and re-rendered.

In summary:

- `format.json` — required by the MPC; keep it with the wavetable WAV files.
- `_json/*.json` — optional Fractured Wavetable Generator templates and metadata; keep them elsewhere.

## Troubleshooting

### The library or file does not appear

Check that:

- the MPC is running version 3.9 or later;
- the path is exactly `Oscillators/Wavetables/<LibraryFolder>/`;
- `format.json` is present in the same folder as the WAV files;
- there are no nested folders inside `<LibraryFolder>`;
- the WAV has not been edited, truncated, or converted since it was packaged.

The MPC requires user wavetable sources to be mono WAV files whose sample count matches the geometry declared in `format.json`.

### The sound seems static

Move **Position**. A wavetable oscillator held at one position plays only one frame, just like a conventional oscillator. Route modulation to **Osc Position** to scan through the table.

### Scanning sounds stepped or rough

Increase **XFade**, reduce the depth or speed of Position modulation, or use a smoother wavetable. Some of these examples are deliberately edged, noisy, or unstable.

## Licence

To the extent possible under law, the example WAV files, their `format.json` manifests, included Fractured Wavetable Generator sidecars, and this README are dedicated to the public domain under
[CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/).

You may copy, modify, redistribute, sample, resell, perform, and use them commercially without permission or attribution.

Attribution is **not** required. An optional credit is:

> Generated with Fractured Wavetable Generator  
> https://github.com/atom-smasher/Fractured-Wavetable-Generator/

The Fractured Wavetable Generator program itself is separate software released under **GPL-2.0-only**. CC0 applies to the example content in this collection, not to the generator’s source code.

## Disclaimer

These files are independent user-created content. They are not produced, sponsored, or endorsed by Akai Professional or inMusic Brands. MPC is a trademark of its respective owner.
