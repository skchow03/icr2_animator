import tempfile
import unittest
from pathlib import Path

from app_settings import (
    AppSettings,
    DEFAULT_CONFIG_PATH,
    DEFAULT_FPS,
    get_window_keywords,
    load_app_settings,
    save_app_settings,
)
from icr2_versions import DEFAULT_ICR2_VERSION, ICR2_VERSION_CONFIGS


class AppSettingsTest(unittest.TestCase):
    def test_missing_sections_use_builtin_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = load_app_settings(Path(tmpdir) / "missing.ini")

        self.assertEqual(settings.selected_version(), DEFAULT_ICR2_VERSION)
        self.assertEqual(settings.config_path(), DEFAULT_CONFIG_PATH)
        self.assertEqual(settings.fps(), DEFAULT_FPS)
        self.assertFalse(settings.tooltips_enabled())
        self.assertEqual(
            get_window_keywords(DEFAULT_ICR2_VERSION, settings),
            list(ICR2_VERSION_CONFIGS[DEFAULT_ICR2_VERSION].window_keywords),
        )

    def test_invalid_values_fall_back_to_safe_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bad.ini"
            path.write_text(
                "[launcher]\n"
                "version = unknown\n"
                "config_path =    \n"
                "fps = 0\n"
                "tooltips_enabled = perhaps\n"
                "[window_keywords]\n"
                f"{DEFAULT_ICR2_VERSION} = , , \n",
                encoding="utf-8",
            )
            settings = AppSettings(path)

        self.assertEqual(settings.selected_version(), DEFAULT_ICR2_VERSION)
        self.assertEqual(settings.config_path(), DEFAULT_CONFIG_PATH)
        self.assertEqual(settings.fps(), DEFAULT_FPS)
        self.assertFalse(settings.tooltips_enabled())
        self.assertEqual(
            get_window_keywords("unknown", settings),
            list(ICR2_VERSION_CONFIGS[DEFAULT_ICR2_VERSION].window_keywords),
        )

    def test_save_settings_sanitizes_invalid_launcher_and_keywords(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "settings.ini"
            settings = AppSettings(path)
            settings.set_launcher_settings(
                version="unknown",
                config_path="   ",
                fps="-5",
                tooltips_enabled=True,
            )
            settings.set_window_keywords_for_version(DEFAULT_ICR2_VERSION, [" ", ""])
            save_app_settings(settings)
            reloaded = AppSettings(path)

        self.assertEqual(reloaded.selected_version(), DEFAULT_ICR2_VERSION)
        self.assertEqual(reloaded.config_path(), DEFAULT_CONFIG_PATH)
        self.assertEqual(reloaded.fps(), DEFAULT_FPS)
        self.assertTrue(reloaded.tooltips_enabled())
        self.assertEqual(
            get_window_keywords(DEFAULT_ICR2_VERSION, reloaded),
            list(ICR2_VERSION_CONFIGS[DEFAULT_ICR2_VERSION].window_keywords),
        )

    def test_persists_selected_version_and_per_version_keywords(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "settings.ini"
            settings = AppSettings(path)
            settings.set_launcher_settings(
                version="WINDY",
                config_path="objects.json",
                fps="60",
                tooltips_enabled=False,
            )
            settings.set_window_keywords_for_version("REND32A", ["dosbox", "cart"])
            settings.set_window_keywords_for_version("WINDY", ["cart racing"])
            save_app_settings(settings)
            reloaded = AppSettings(path)

        self.assertEqual(reloaded.selected_version(), "WINDY")
        self.assertEqual(get_window_keywords("REND32A", reloaded), ["dosbox", "cart"])
        self.assertEqual(get_window_keywords("WINDY", reloaded), ["cart racing"])


if __name__ == "__main__":
    unittest.main()
