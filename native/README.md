# Native Runtime

The add-on uses Gradium's unmodified `ptts-ffi` C API from
[`xn-ptts` v0.5.0](https://github.com/gradium-ai/xn-ptts/tree/e851445158f07061f063e3edff0080f9426c0da2).
The interface is declared in that project's `ptts-ffi/include/ptts.h`.

Build `ptts-ffi` in release mode with a Rust Windows toolchain. This candidate
uses Rust 1.98.0's `x86_64-pc-windows-gnullvm` toolchain and LLVM-MinGW, with
`-C target-cpu=x86-64-v3` and stripped symbols. Remap build paths when compiling
redistributable binaries. Do not use the upstream native-CPU default for a
portable distribution.

Put the resulting `ptts_ffi.dll` and its matching LLVM `libunwind.dll` in
`addon/synthDrivers/phononLocal/bin`. Never replace DLLs while NVDA is running.
The DLL, not a helper executable, performs synthesis inside NVDA's speech
worker; callbacks deliver float PCM for NVDA's Sonic and WavePlayer pipeline.

The multilingual checkpoint belongs in `addon/data/phonon`, with `config.json`,
`tokenizer.json`, `model.safetensors` and `voices/*.safetensors`. Voice prompt
tensors use their original `speaker_wavs` identity, not an Ollama layer name.
Obtain the original model and voices under the licences linked in the main
README. Binary/model folders are excluded from Git; release packages include
them. Test fixtures do not need model files.

The C handle is owned by one speech worker. It must not be freed from the UI
thread or while generation is running. Cancellation stops NVDA playback and
rejects stale callbacks immediately, then lets the native operation return
before reusing or freeing the handle.
