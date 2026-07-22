import os
import unittest
from pt_api import ProToolsSession

class TestOttoAlignOutputs(unittest.TestCase):
    def test_target3_aligned_is_valid(self):
        path = r"Y:\OttoAlign2\tests_21_juil\target3\target3_aligned.ptx"
        self.assertTrue(os.path.exists(path), "File does not exist")
        session = ProToolsSession(path)
        session._validate_session_envelope()
        
    def test_target4_aligned_is_valid(self):
        path = r"Y:\OttoAlign2\tests_21_juil\target4\target4_aligned.ptx"
        self.assertTrue(os.path.exists(path), "File does not exist")
        session = ProToolsSession(path)
        session._validate_session_envelope()

    def test_target5_aligned_is_valid(self):
        path = r"Y:\OttoAlign2\tests_21_juil\target5\target5_aligned.ptx"
        self.assertTrue(os.path.exists(path), "File does not exist")
        session = ProToolsSession(path)
        session._validate_session_envelope()

if __name__ == '__main__':
    unittest.main()
