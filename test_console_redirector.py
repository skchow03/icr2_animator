import unittest
from io import StringIO

from icr2_animator import ConsoleRedirector


class ConsoleRedirectorTest(unittest.TestCase):
    def test_write_mirrors_to_original_stream_and_callback(self) -> None:
        original = StringIO()
        messages: list[str] = []
        redirector = ConsoleRedirector(original, messages.append)

        count = redirector.write("hello")
        redirector.flush()

        self.assertEqual(count, 5)
        self.assertEqual(original.getvalue(), "hello")
        self.assertEqual(messages, ["hello"])

    def test_write_handles_windowed_pyinstaller_without_console_stream(self) -> None:
        messages: list[str] = []
        redirector = ConsoleRedirector(None, messages.append)

        count = redirector.write("hello")
        redirector.flush()

        self.assertEqual(count, 5)
        self.assertEqual(messages, ["hello"])


if __name__ == "__main__":
    unittest.main()
