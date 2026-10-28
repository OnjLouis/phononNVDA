import ctypes as C
import threading
import unittest

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


if __name__ == '__main__': unittest.main()
