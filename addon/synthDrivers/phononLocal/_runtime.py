# SPDX-License-Identifier: GPL-2.0-or-later
"""Serialized access to Phonon's C API; no network or audio-device access."""
import ctypes as C
import os
from pathlib import Path
import sys
import threading

from logHandler import log

SAMPLE_RATE = 24000
MAX_INPUT = 4096
UNIT_CPU = 0
UNIT_VULKAN = 3
LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x00000800
LANGUAGES = frozenset(("en", "fr", "de", "es", "pt"))
MODEL = Path(__file__).resolve().parents[2] / "data" / "phonon"
BIN = Path(__file__).resolve().parent / "bin"
FRAME = C.CFUNCTYPE(C.c_bool, C.POINTER(C.c_float), C.c_size_t, C.c_void_p)
_DLL = None
_DLL_LOCK = threading.Lock()
_VULKAN = None
_GPU_INIT_LOCK = threading.Lock()
_GPU_UNAVAILABLE = False


def _vulkan_loader():
    global _VULKAN
    if _VULKAN is None:
        loader = Path(os.environ['SystemRoot']) / 'System32' / 'vulkan-1.dll'
        _VULKAN = C.WinDLL(str(loader), winmode=LOAD_LIBRARY_SEARCH_SYSTEM32)
    return _VULKAN


class NativeError(RuntimeError):
    pass


class Result(C.Structure):
    _fields_ = [("frames", C.c_uint32), ("samples", C.c_size_t),
                ("ttfa_ms", C.c_double), ("total_ms", C.c_double)]


def supported():
    return (sys.platform == "win32" and C.sizeof(C.c_void_p) == 8
            and bool(C.windll.kernel32.IsProcessorFeaturePresent(40)))


def available():
    return supported() and all(path.is_file() for path in (
        BIN / "ptts_ffi.dll", BIN / "libunwind.dll", MODEL / "config.json",
        MODEL / "tokenizer.json", MODEL / "model.safetensors"))


def language(code):
    value = (code or "en").replace("-", "_").split("_", 1)[0].lower()
    return value if value in LANGUAGES else "en"


def _library():
    global _DLL
    with _DLL_LOCK:
        if _DLL is not None:
            return _DLL
        with os.add_dll_directory(str(BIN)):
            library = C.CDLL(str(BIN / "ptts_ffi.dll"))
        for name, result, arguments in (
            ("ptts_new", C.c_void_p, [C.c_char_p, C.c_uint32, C.c_char_p]),
            ("ptts_speak", C.c_bool, [C.c_void_p, C.c_char_p, FRAME, C.c_void_p, C.POINTER(Result)]),
            ("ptts_set_voice", C.c_bool, [C.c_void_p, C.c_char_p]),
            ("ptts_set_language", C.c_bool, [C.c_void_p, C.c_char_p]),
            ("ptts_last_error", C.c_char_p, [C.c_void_p]),
            ("ptts_free", None, [C.c_void_p]),
        ):
            function = getattr(library, name)
            function.restype, function.argtypes = result, arguments
        # The DLL's shared worker pool outlives individual model handles.
        _DLL = library
        return library


class Engine:
    def __init__(self, model=MODEL, use_gpu=False):
        global _GPU_UNAVAILABLE
        self._owner = threading.get_ident()
        self._handle = None
        self.backend = 'CPU'
        if not supported():
            raise NativeError("Phonon requires 64-bit NVDA and an AVX2-capable processor.")
        try:
            self._dll = _library()
            if use_gpu:
                with _GPU_INIT_LOCK:
                    if not _GPU_UNAVAILABLE:
                        try:
                            _vulkan_loader()
                            self._open(model, UNIT_VULKAN)
                            self._prime_gpu()
                            self.backend = 'GPU (Vulkan)'
                        except (OSError, NativeError) as error:
                            self.close()
                            _GPU_UNAVAILABLE = True
                            log.info('Phonon GPU unavailable; using CPU for this session: %s', error)
            if not self._handle:
                self._open(model, UNIT_CPU)
        except BaseException:
            self.close()
            raise

    def _error(self):
        detail = self._dll.ptts_last_error(self._handle)
        return detail.decode('utf-8', errors='replace')[:512] if detail else 'Unknown native engine error'

    def _open(self, model, unit):
        self._handle = self._dll.ptts_new(str(model).encode('utf-8'), unit, b'en')
        if not self._handle:
            raise NativeError('Phonon could not load its bundled model: ' + self._error())

    def _prime_gpu(self):
        # Validate real synthesis on the worker before choosing a GPU for speech.
        samples = 0
        @FRAME
        def discard(pcm, count, user):
            nonlocal samples
            samples += count
            return True
        result = Result()
        if not self._dll.ptts_speak(self._handle, b'A', discard, None, C.byref(result)):
            raise NativeError('GPU synthesis is unavailable: ' + self._error())
        if not samples:
            raise NativeError('GPU initialization produced no audio.')

    def _check_owner(self):
        if threading.get_ident() != self._owner:
            raise RuntimeError("Phonon's engine must be used only by its speech worker.")

    def speak(self, text, voice, locale, cancel, sink):
        self._check_owner()
        if cancel.is_set():
            return None
        if not self._handle or not text or len(text) > MAX_INPUT or "\0" in text:
            raise NativeError("The Phonon speech request is invalid.")
        if not self._dll.ptts_set_voice(self._handle, voice.encode("utf-8")):
            raise NativeError("Phonon could not select the requested voice.")
        if not self._dll.ptts_set_language(self._handle, language(locale).encode("ascii")):
            raise NativeError("Phonon could not select the requested language.")
        failure = []

        @FRAME
        def callback(pcm, count, user):
            if cancel.is_set():
                return False
            try:
                return bool(sink(pcm, count)) and not cancel.is_set()
            except Exception as error:
                failure.append(error)
                return False

        result = Result()
        success = self._dll.ptts_speak(self._handle, text.encode("utf-8"), callback, None, C.byref(result))
        if failure:
            raise failure[0]
        if not success and not cancel.is_set():
            raise NativeError("Phonon's native engine could not generate this utterance.")
        return result

    def close(self):
        self._check_owner()
        if self._handle:
            self._dll.ptts_free(self._handle)
            self._handle = None
