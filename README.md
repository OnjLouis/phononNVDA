# Phonon for NVDA

Offline speech using Gradium's multilingual Phonon engine. Includes 102 voices
for English, French, German, Spanish and Portuguese. The model and voices are
included, with no first-use download required.

## Changelog

### 1.0.2

- Added a read-only Synthesis backend field to the Phonon settings panel, showing actual GPU operation, CPU operation or CPU fallback after initialization.

### 1.0.1

- Fixed the Voice list retaining voices from the previous Language filter in NVDA Speech settings. The choices and selected voice now update together, including after reopening settings. Cancel still restores the previous settings.

### 1.0

- Bundled multilingual model and 102 voices, ready to use after installation.
- Automatic GPU acceleration through Vulkan, with quiet CPU fallback.
- NVDA audio output, Language filter, Voice, Rate, Rate boost, Pitch and Volume.
- Playback-timed indexing, spelling, Say All, pause and interruption handling.
- Settings panel with Help and signed-update controls.

## Requirements and Setup

Requires 64-bit NVDA 2026.2 or later, Windows 10 or later and an AVX2-capable
processor. Older processors and 32-bit NVDA cannot load this runtime.

Install the add-on and restart NVDA. Open the synthesizer selection dialog with
NVDA+Control+S and choose Phonon. The model and voices are included in the package.

Alternative: [Ollama Speech](https://github.com/OnjLouis/ollamaSpeech).

## Speech Controls

Choose Language filter and Voice in Speech settings or the synth settings ring.
English Character Voices groups the catalogue's character voices separately
from British and American English. The filter limits the voice list; it does not
translate text. The model supports the five languages listed above.

Rate, Rate boost, Pitch and Volume use NVDA's streaming Sonic processing.
Audio plays through NVDA's selected output device. Shift pauses or resumes;
Control stops speech. The engine finishes its current calculation before
starting replacement speech, so responsiveness depends on the processor.

If the native engine fails, the active driver switches to eSpeak NG to preserve
access. Keep a familiar alternative synthesizer available.

## Settings and Updates

The Phonon settings panel contains GPU acceleration, Synthesis backend, Check for updates, Check
for updates now and Help. Update intervals are Never, Hourly or Daily; Daily is the
default. Help opens this manual.

**Use GPU acceleration when available** is checked by default. Phonon uses a
compatible NVIDIA, AMD or Intel graphics device through its installed Vulkan
driver. No CUDA toolkit is required. Performance depends on the device; disable
it if CPU speech works better. After changing this option, switch to another
synthesizer and back to Phonon, or restart NVDA.

Phonon tests GPU synthesis before using it. If initialization fails, it quietly
uses the CPU, without a popup or spoken warning. The reason is recorded once in
the NVDA log, and GPU initialization is not retried until NVDA restarts. No
graphics driver is downloaded or installed. CPU speech remains available without a GPU.

**Synthesis backend** reports the running engine, rather than just whether a
GPU is present. It distinguishes GPU (Vulkan), CPU with GPU disabled, and CPU
fallback after unsuccessful GPU initialization. If Phonon is not selected, no
backend is assumed. The field updates only while this settings panel is open.

Updates require a valid Phonon signature and matching package identity, version,
size and hash. Downloads are staged outside the add-ons folder and installed by
NVDA after your approval. Restart NVDA when prompted. Saved speech settings are
kept.

## Privacy

Speech is generated on this computer. Spoken text and audio are not saved or
sent over the network. Only update checks and approved update downloads use the
network. Automatic update checks can be disabled.

## Downloads And Support

- [Download Phonon for NVDA](https://github.com/OnjLouis/phononNVDA/releases/latest)
- [Project, source code and issue tracker](https://github.com/OnjLouis/phononNVDA)
- [Phonon SAPI for other Windows speech applications](https://github.com/OnjLouis/phononSAPI/releases/latest)
- [Phonon for Mac (Apple Silicon)](https://github.com/OnjLouis/phononMac/releases/latest)
- [Andre Louis software catalogue](https://onj.me/software)
- [Contact Andre Louis](https://onj.me/contact)
- [Donate to support development](https://onj.me/donate)

Donations are optional and do not unlock features.

## Credits and Licences

Add-on by Andre Louis, with development assistance from OpenAI Codex.
[Gradium's Phonon runtime](https://github.com/gradium-ai/xn-ptts) builds on
Kyutai's Pocket TTS and Mimi. Voice metadata and recordings come from
[Gradium's voice catalogue](https://huggingface.co/gradium/phonon-voices).
Audio controls use NVDA's bundled Sonic by Bill Cox.

Add-on code is GPL-2.0-or-later. The runtime is MIT OR Apache-2.0, the voice
catalogue is Apache-2.0, and the
[40M multilingual model](https://huggingface.co/gradium/phonon-40m-multilingual)
is CC BY-NC 4.0. The original multilingual tensors were repackaged for the native
runtime, not retrained. Original licence texts and native dependency notices are
included in the installed manual's Licenses folder. This is an independent
project, not an official or endorsed Gradium product.
