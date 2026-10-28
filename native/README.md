# Native Runtime

The add-on uses Gradium's `ptts-ffi` C API from
[`xn-ptts` v0.5.0](https://github.com/gradium-ai/xn-ptts/tree/e851445158f07061f063e3edff0080f9426c0da2).
The interface is declared in that project's `ptts-ffi/include/ptts.h`.
Apply `ptts-windows.patch` to that exact source revision before building. The
patch adds compute unit 3 for Vulkan and initializes this DLL's own CPU pool
with at most three participants. It does not set process-wide environment
variables. The CPU ABI and model generation algorithm are unchanged.

Unpack the `xn` 0.2.11 crate into `xn` inside that source tree, then apply
`xn-vulkan.patch` inside it. The workspace patch selects that copy. It fixes
native cleanup after failed Vulkan initialization, buffer allocation and
pipeline creation. The accompanying native tests use an isolated mock driver.
Both native patches are provided under MIT OR Apache-2.0, matching upstream.

Build `ptts-ffi` in release mode with a Rust Windows toolchain. This candidate
uses Rust 1.98.0's `x86_64-pc-windows-gnullvm` toolchain and LLVM-MinGW, with
`-C target-cpu=x86-64-v3` and stripped symbols. Remap build paths when compiling
redistributable binaries. Do not use the upstream native-CPU default for a
portable distribution.

Enable `ptts/vulkan` when building `ptts-ffi`. The build requires `glslc` on
PATH to compile xn's shaders. They are embedded in the DLL; end users need
only a compatible installed Vulkan graphics driver, not a compiler or SDK.

Put the resulting `ptts_ffi.dll` and its matching LLVM `libunwind.dll` in
`addon/synthDrivers/phononLocal/bin`. Never replace DLLs while NVDA is running.
The DLL, not a helper executable, performs synthesis inside NVDA's speech
worker; callbacks deliver float PCM for NVDA's Sonic and WavePlayer pipeline.

The multilingual checkpoint belongs in `addon/data/phonon`, with `config.json`,
`tokenizer.json`, `model.safetensors` and `voices/*.safetensors`. Voice prompt
tensors use their original `speaker_wavs` identity.
Obtain the original model and voices under the licences linked in the main
README. Binary/model folders are excluded from Git; release packages include
them. Test fixtures do not need model files.

The C handle is owned by one speech worker. It must not be freed from the UI
thread or while generation is running. Cancellation stops NVDA playback and
rejects stale callbacks immediately, then lets the native operation return
before reusing or freeing the handle.
