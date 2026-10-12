import importlib.util
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


PLUGIN_PATH = (
    Path(__file__).parents[1]
    / "repo/repository.thokodi/plugin.video.thokodi/default.py"
)


def load_plugin():
    modules = {}
    for name in ("xbmc", "xbmcaddon", "xbmcgui", "xbmcplugin", "xbmcvfs"):
        module = types.ModuleType(name)
        if name == "xbmc":
            module.LOGINFO = 1
            module.LOGERROR = 2
            module.log = lambda *args: None
        elif name == "xbmcaddon":
            class Addon:
                def getAddonInfo(self, key):
                    return key

            module.Addon = Addon
        modules[name] = module

    with patch.dict(sys.modules, modules), patch.object(sys, "argv", ["plugin", "1"]):
        spec = importlib.util.spec_from_file_location("thokodi_default_test", PLUGIN_PATH)
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
    return plugin


class ZipRootTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plugin = load_plugin()

    def assert_archive_installs_as(self, entries, root, expected_file):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_directory = Path(temporary_directory)
            archive_path = temporary_directory / "build.zip"
            destination = temporary_directory / "install"
            destination.mkdir()

            with zipfile.ZipFile(archive_path, "w") as archive:
                for entry in entries:
                    archive.writestr(entry, "" if entry.endswith("/") else "content")

            self.assertEqual(self.plugin.detect_wrapper_folder(archive_path), root)
            self.plugin.safe_extract_zip(archive_path, destination)
            self.assertEqual((destination / expected_file).read_text(), "content")

    def test_nested_kodi_home_path(self):
        self.assert_archive_installs_as(
            [
                "outer/",
                "outer/files/",
                "outer/files/.kodi/",
                "outer/files/.kodi/addons/plugin/addon.xml",
            ],
            "outer/files/.kodi",
            "addons/plugin/addon.xml",
        )

    def test_kodi_home_at_archive_root(self):
        self.assert_archive_installs_as(
            [".kodi/", ".kodi/userdata/guisettings.xml"],
            ".kodi",
            "userdata/guisettings.xml",
        )

    def test_direct_kodi_home_contents(self):
        self.assert_archive_installs_as(
            ["addons/plugin/addon.xml", "userdata/guisettings.xml"],
            "",
            "addons/plugin/addon.xml",
        )

    def test_single_wrapper_without_kodi_directory(self):
        self.assert_archive_installs_as(
            ["Build/", "Build/addons/plugin/addon.xml"],
            "Build",
            "addons/plugin/addon.xml",
        )

    def test_rejects_files_outside_detected_kodi_home(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_directory = Path(temporary_directory)
            archive_path = temporary_directory / "build.zip"
            destination = temporary_directory / "install"
            destination.mkdir()
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("Build/.kodi/addons/plugin/addon.xml", "content")
                archive.writestr("Build/readme.txt", "unexpected")

            with self.assertRaises(ValueError):
                self.plugin.safe_extract_zip(archive_path, destination)
            self.assertFalse((destination / "addons").exists())

    def test_rejects_multiple_kodi_roots(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_path = Path(temporary_directory) / "build.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("First/.kodi/addons/plugin/addon.xml", "content")
                archive.writestr("Second/.kodi/userdata/guisettings.xml", "content")

            with self.assertRaisesRegex(ValueError, "Multiple Kodi home roots"):
                self.plugin.detect_wrapper_folder(archive_path)

    def test_dot_kodi_without_build_folders_uses_wrapper_detection(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_path = Path(temporary_directory) / "build.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("Build/.kodi/custom/", "")
                archive.writestr("Build/readme.txt", "content")

            self.assertEqual(
                self.plugin.detect_wrapper_folder(archive_path),
                "Build",
            )


if __name__ == "__main__":
    unittest.main()
