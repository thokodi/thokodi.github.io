# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo("id")
ADDON_NAME = ADDON.getAddonInfo("name")
ADDON_VERSION = ADDON.getAddonInfo("version")
HANDLE = int(sys.argv[1]) if len(sys.argv) > 1 else 1

MANIFEST_URL = "https://raw.githubusercontent.com/thokodi/thokodi.github.io/master/builds/manifest.json"


def log(message: str, level=xbmc.LOGINFO) -> None:
    xbmc.log(f"[{ADDON_ID}] {message}", level)


def build_url(**query) -> str:
    return sys.argv[0] + "?" + urllib.parse.urlencode(query)


def parse_params() -> dict:
    if len(sys.argv) < 3 or not sys.argv[2]:
        return {}
    query = sys.argv[2][1:] if sys.argv[2].startswith("?") else sys.argv[2]
    return dict(urllib.parse.parse_qsl(query))


def open_url(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Kodi"})
    with urllib.request.urlopen(req, timeout=20) as response:
        data = response.read()
    return data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data


def add_item(label: str, action: str, folder: bool = True, art: Optional[dict] = None, info: Optional[dict] = None, **params) -> None:
    item = xbmcgui.ListItem(label=label)
    if art:
        item.setArt(art)
    if info:
        if hasattr(item, "getVideoInfoTag"):
            video_info = item.getVideoInfoTag()
            if info.get("title"):
                video_info.setTitle(info["title"])
            if info.get("plot"):
                video_info.setPlot(info["plot"])
        else:
            item.setInfo("video", {k: v for k, v in info.items() if v})
    xbmcplugin.addDirectoryItem(HANDLE, build_url(action=action, **params), item, isFolder=folder)


def render_main_menu() -> None:
    add_item(
        label="Available builds",
        action="list_builds",
        folder=True,
        art={
            "icon": ADDON.getAddonInfo("icon"),
            "thumb": ADDON.getAddonInfo("icon"),
            "fanart": ADDON.getAddonInfo("fanart"),
        },
    )
    xbmcplugin.setContent(HANDLE, "files")
    xbmcplugin.endOfDirectory(HANDLE)


def load_manifest() -> dict:
    raw = open_url(MANIFEST_URL)
    return json.loads(raw)


def render_build_list() -> None:
    try:
        manifest = load_manifest()
    except Exception as exc:
        log(f"Failed to load manifest: {exc}", xbmc.LOGERROR)
        xbmcgui.Dialog().ok(ADDON_NAME, f"Failed to load build manifest.\n{exc}")
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return

    builds = manifest.get("builds", [])
    if not builds:
        xbmcgui.Dialog().ok(ADDON_NAME, "No builds found in the manifest.")
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return

    for build in builds:
        label = build.get("name", "Unnamed build")
        add_item(
            label=label,
            action="install_build",
            folder=False,
            art={
                "icon": build.get("icon", ADDON.getAddonInfo("icon")),
                "thumb": build.get("icon", ADDON.getAddonInfo("icon")),
                "fanart": build.get("fanart", ADDON.getAddonInfo("fanart")),
            },
            info={
                "title": label,
                "plot": build.get("description", ""),
            },
            name=label,
            url=build.get("url", ""),
            checksum=build.get("checksum", ""),
        )

    xbmcplugin.setContent(HANDLE, "files")
    xbmcplugin.endOfDirectory(HANDLE)


def verify_checksum(local_path: Path, expected: str) -> bool:
    if not expected:
        return True

    if ":" in expected:
        algo, expected_hash = expected.split(":", 1)
    else:
        algo, expected_hash = "sha256", expected

    algo = algo.lower().strip()
    expected_hash = expected_hash.strip().lower()

    if algo not in {"md5", "sha1", "sha256"}:
        raise ValueError(f"Unsupported checksum algorithm: {algo}")

    import hashlib

    digest = hashlib.new(algo)
    with local_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest().lower() == expected_hash


BUILD_FOLDERS = {"media", "addons", "userdata"}


def _normalized_names(archive) -> list:
    names = []
    for member in archive.infolist():
        name = member.filename.replace("\\", "/").lstrip("/")
        if name:
            names.append(name)
    return names


def detect_wrapper_folder(zip_path: Path) -> str:
    """Return the single top-level wrapper folder name, or "" if the archive is rooted directly."""
    import zipfile

    with zipfile.ZipFile(zip_path, "r") as archive:
        names = _normalized_names(archive)

    tops = {name.split("/", 1)[0] for name in names}
    if len(tops) != 1:
        return ""
    top = next(iter(tops))
    if top in BUILD_FOLDERS or top in {".", ".."}:
        return ""
    # a lone top-level entry must be a directory (i.e. have children) to be a wrapper
    if not any(name.startswith(top + "/") and len(name) > len(top) + 1 for name in names):
        return ""
    return top


def safe_extract_zip(zip_path: Path, destination: Path) -> None:
    import shutil
    import zipfile

    destination = destination.resolve()
    wrapper = detect_wrapper_folder(zip_path)
    log(f"Zip layout: {'wrapper folder ' + repr(wrapper) + ' detected (will be stripped)' if wrapper else 'no wrapper folder, top-level folders are at the archive root'}")
    log(f"Extracting {zip_path.name} to {destination}")

    with zipfile.ZipFile(zip_path, "r") as archive:
        plan = []
        for member in archive.infolist():
            name = member.filename.replace("\\", "/").lstrip("/")
            if wrapper:
                if name == wrapper or name == wrapper + "/":
                    continue
                if not name.startswith(wrapper + "/"):
                    raise ValueError(f"Unexpected zip entry outside wrapper folder: {member.filename}")
                name = name[len(wrapper) + 1:]
            if not name:
                continue
            target = (destination / name).resolve()
            if target != destination and destination not in target.parents:
                raise ValueError(f"Blocked unsafe zip entry: {member.filename}")
            plan.append((member, target))

        count = 0
        for member, target in plan:
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member, "r") as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            count += 1

    log(f"Extracted {count} files to {destination}")


def install_build(url: str, checksum: str, name: str) -> None:
    if not url:
        xbmcgui.Dialog().ok(ADDON_NAME, "Missing build URL.")
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return

    progress = xbmcgui.DialogProgress()
    progress.create(ADDON_NAME, f"Downloading {name}...")

    tmp_dir = Path(tempfile.mkdtemp(prefix="thokodi_"))
    zip_path = tmp_dir / "build.zip"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Kodi"})
        with urllib.request.urlopen(req, timeout=60) as response, zip_path.open("wb") as out:
            total = response.headers.get("Content-Length")
            total_size = int(total) if total and total.isdigit() else 0
            downloaded = 0

            while True:
                chunk = response.read(1024 * 64)
                if not chunk:
                    break
                out.write(chunk)
                downloaded += len(chunk)

                mb = downloaded / (1024 * 1024)
                if total_size:
                    percent = min(int((downloaded / total_size) * 100), 100)
                    progress.update(percent, f"Downloading {name}... {mb:.1f} MB")
                else:
                    progress.update(0, f"Downloading {name}... {mb:.1f} MB")

                if progress.iscanceled():
                    raise Exception("Download canceled")

        if checksum and not verify_checksum(zip_path, checksum):
            raise ValueError("Checksum verification failed")

        install_target = Path(xbmcvfs.translatePath("special://home"))
        progress.update(0, f"Installing {name} to {install_target}...")
        safe_extract_zip(zip_path, install_target)

        progress.close()
        xbmcgui.Dialog().ok(ADDON_NAME, f"{name} installed to:\n{install_target}\nPlease restart Kodi if the new files do not appear immediately.")
        xbmc.executebuiltin("Container.Refresh")
    except Exception as exc:
        progress.close()
        import traceback
        log(f"Install failed: {exc}\n{traceback.format_exc()}", xbmc.LOGERROR)
        xbmcgui.Dialog().ok(ADDON_NAME, f"Installation failed.\n{exc}")
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
    finally:
        try:
            zip_path.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            tmp_dir.rmdir()
        except Exception:
            pass


def router() -> None:
    try:
        _route()
    except Exception as exc:
        import traceback
        log(f"Unhandled error: {exc}\n{traceback.format_exc()}", xbmc.LOGERROR)
        xbmcgui.Dialog().ok(ADDON_NAME, f"Unexpected error.\n{exc}")
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)


def _route() -> None:
    params = parse_params()
    action = params.get("action")

    log(f"version={ADDON_VERSION}, action={action}, params={params}")

    if not action:
        render_main_menu()
    elif action == "list_builds":
        render_build_list()
    elif action == "install_build":
        install_build(
            url=params.get("url", ""),
            checksum=params.get("checksum", ""),
            name=params.get("name", "build"),
        )
    else:
        xbmcgui.Dialog().ok(ADDON_NAME, f"Unknown action: {action}")
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False)


if __name__ == "__main__":
    router()
