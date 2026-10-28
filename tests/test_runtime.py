import ctypes as C
import threading
import unittest
from unittest.mock import patch

from test_driver import driver_module
from synthDrivers.phononLocal import _runtime as runtime


class DLL:
    def __init__(self): self.delivered = 0
    def ptts_set_voice(self, *args): return True
    def ptts_set_language(self, *args): return True
    def ptts_speak(self, handle, text, callback, user, result):
        pcm = (C.c_float * 4)(0.1, -0.1, 0.1, -0.1)
        self.delivered += bool(callback(pcm, 4, None))
        return True


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.engine = runtime.Engine.__new__(runtime.Engine)
        self.engine._owner = threading.get_ident()
        self.engine._handle = 1
        self.engine._dll = DLL()

    def test_c_bool_and_result_match_the_native_header(self):
        self.assertEqual(C.sizeof(C.c_bool), 1)
        if C.sizeof(C.c_void_p) == 8:
            self.assertEqual(C.sizeof(runtime.Result), 32)
            self.assertEqual(runtime.Result.samples.offset, 8)

    def test_pre_cancelled_request_never_enters_native_code(self):
        cancel = threading.Event(); cancel.set()
        self.assertIsNone(self.engine.speak('hello', 'Freya', 'en', cancel, lambda *a: True))
        self.assertEqual(self.engine._dll.delivered, 0)

    def test_callback_exception_returns_through_python_not_the_c_boundary(self):
        def fail(*args): raise ValueError('fixture failure')
        with self.assertRaisesRegex(ValueError, 'fixture failure'):
            self.engine.speak('hello', 'Freya', 'en', threading.Event(), fail)
        self.assertEqual(self.engine._dll.delivered, 0)

    def test_invalid_strings_are_rejected_before_c_string_conversion(self):
        for text in ('', '\0', 'x' * (runtime.MAX_INPUT + 1)):
            with self.subTest(length=len(text)), self.assertRaises(runtime.NativeError):
                self.engine.speak(text, 'Freya', 'en', threading.Event(), lambda *a: True)

    def test_other_threads_cannot_use_or_free_the_handle(self):
        errors = []
        def wrong_thread():
            try: self.engine.close()
            except RuntimeError as error: errors.append(error)
        worker = threading.Thread(target=wrong_thread)
        worker.start(); worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)

    def test_region_codes_and_unknown_language_fallback(self):
        self.assertEqual(runtime.language('fr-FR'), 'fr')
        self.assertEqual(runtime.language('pt_BR'), 'pt')
        self.assertEqual(runtime.language('unknown'), 'en')


class InitializingDLL(DLL):
    def __init__(self, handles=(1,), warm_success=True):
        super().__init__()
        self.handles = iter(handles)
        self.units, self.freed = [], []
        self.warm_success = warm_success

    def ptts_new(self, model, unit, locale):
        self.units.append(unit)
        return next(self.handles)

    def ptts_last_error(self, handle): return b'fixture backend unavailable'
    def ptts_free(self, handle): self.freed.append(handle)
    def ptts_speak(self, *args):
        if not self.warm_success:
            return False
        return super().ptts_speak(*args)


class BackendTests(unittest.TestCase):
    def setUp(self):
        state = patch.object(runtime, '_GPU_UNAVAILABLE', False, create=True)
        state.start()
        self.addCleanup(state.stop)

    def open(self, dll, gpu=False, loader_error=None):
        with patch.object(runtime, 'supported', return_value=True), \
                patch.object(runtime, '_library', return_value=dll), \
                patch.object(runtime, '_vulkan_loader', side_effect=loader_error) as loader:
            engine = runtime.Engine(use_gpu=gpu)
        self.addCleanup(engine.close)
        return engine, loader

    def test_cpu_default_does_not_load_gpu_driver(self):
        dll = InitializingDLL()
        engine, loader = self.open(dll)
        self.assertEqual(dll.units, [0])
        self.assertEqual(engine.backend, 'CPU')
        loader.assert_not_called()

    def test_gpu_is_validated_without_playing_its_warmup(self):
        dll = InitializingDLL()
        engine, loader = self.open(dll, gpu=True)
        self.assertEqual(dll.units, [3])
        self.assertEqual(engine.backend, 'GPU (Vulkan)')
        self.assertEqual(dll.delivered, 1)
        loader.assert_called_once_with()

    def test_gpu_initialization_failure_falls_back_to_cpu(self):
        dll = InitializingDLL(handles=(0, 2))
        engine, _ = self.open(dll, gpu=True)
        self.assertEqual(dll.units, [3, 0])
        self.assertEqual(engine.backend, 'CPU')

    def test_failed_gpu_warmup_frees_handle_before_cpu_fallback(self):
        dll = InitializingDLL(handles=(1, 2), warm_success=False)
        engine, _ = self.open(dll, gpu=True)
        self.assertEqual(dll.units, [3, 0])
        self.assertEqual(dll.freed, [1])
        self.assertEqual(engine.backend, 'CPU')

    def test_missing_vulkan_loader_does_not_prevent_cpu_speech(self):
        dll = InitializingDLL()
        engine, _ = self.open(dll, gpu=True, loader_error=OSError('fixture missing loader'))
        self.assertEqual(dll.units, [0])
        self.assertEqual(engine.backend, 'CPU')

    def test_cpu_failure_retains_the_native_error(self):
        with patch.object(runtime, 'supported', return_value=True), \
                patch.object(runtime, '_library', return_value=InitializingDLL(handles=(0,))):
            with self.assertRaisesRegex(runtime.NativeError, 'fixture backend unavailable'):
                runtime.Engine()

    def test_unavailable_gpu_is_logged_once_and_not_retried(self):
        dll = InitializingDLL(handles=(0, 2, 3))
        with patch.object(runtime.log, 'info') as info, patch.object(runtime.log, 'warning') as warning:
            first, _ = self.open(dll, gpu=True)
            second, loader = self.open(dll, gpu=True)
        self.assertEqual((first.backend, second.backend), ('CPU', 'CPU'))
        self.assertEqual(dll.units, [3, 0, 0])
        loader.assert_not_called()
        info.assert_called_once()
        warning.assert_not_called()


if __name__ == '__main__': unittest.main()
