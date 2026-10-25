"""Look for anything personal or secret before something is published, and fail loudly if something is found.

    python packaging/scan_release.py              scan dist/ (the installer, the no-install zip, the guide)
    python packaging/scan_release.py --source DIR scan a source tree before it goes to GitHub

What it looks for:
  * API keys, in the usual shape and (read from .local/.env, never printed) your actual key
  * the paths of this machine: your Windows profile, this project's own folder
  * anything listed in .local/scan-needles.txt: your own names, companies and phrases, one "label | regex" per line.
    That file is private (git-ignored), which is why the words are not in this script.
  * files that must never ship: .env, settings.json, usage logs, recordings, shortcuts, transcripts (outside samples/)
  * for a release: the text and file paths compiled into the app's own Python code, which a plain search cannot see
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from meetminer.version import __version__  # noqa: E402

LOCAL = ROOT / ".local"
EXPECTED = {f"MeetMiner-Setup-{__version__}.exe", f"MeetMiner-{__version__}-no-install.zip",
            "README.txt", "SHA256SUMS.txt"}
NEVER_SHIP = re.compile(r"(^\.env(\.txt)?$|^settings\.json$|^usage\.csv$|^meetings\.yaml$|desktop-error\.log$"
                        r"|\.(mp4|m4a|mp3|wav|lnk)$|\.pem$|^\.git$|^\.venv$|^\.local$)", re.IGNORECASE)
MAX_SOURCE_FILE = 2_000_000  # a source repository should not hold big binaries (releases go in GitHub Releases)
FICTIONAL_TRANSCRIPTS = ("samples/", "evals/probes/transcripts/")  # the only places a transcript may be published
OTTER_LINE = re.compile(rb"(?m)^Speaker \d+  \d{1,2}:\d{2}")
EMAIL = re.compile(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}", re.IGNORECASE)
# Files this project writes itself. Libraries legitimately contain their authors' public e-mail addresses,
# so the e-mail check is limited to these.
OWN_TEXT = {"README.txt", "SHA256SUMS.txt", "default.yaml", "pyproject.toml", "CHANGELOG.md"}


def generic_needles() -> list[tuple[str, re.Pattern]]:
    # A token containing the word FAKE is a test value (the tests use them); your real key is checked separately, by value.
    needles = [("an Anthropic API key", re.compile(rb"sk-ant-(?![A-Za-z0-9_\-]*FAKE)[A-Za-z0-9_\-]{10,}"))]
    profile = Path(os.environ.get("USERPROFILE", str(Path.home())))
    for label, text in (("this Windows profile", str(profile)), ("this project's own folder", str(ROOT))):
        for variant in {text, text.replace("\\", "/")}:
            needles.append((label, re.compile(re.escape(variant.encode()), re.IGNORECASE)))
    needles.append(("a Windows user path", re.compile(rb"Users[\\/]+" + re.escape(profile.name.encode()) + rb"[\\/]", re.IGNORECASE)))
    private = LOCAL / "scan-needles.txt"
    if private.is_file():
        for line in private.read_text(encoding="utf-8").splitlines():
            if "|" in line and not line.strip().startswith("#"):
                label, _, pattern = line.partition("|")
                needles.append((f"private term ({label.strip()})", re.compile(pattern.strip().encode(), re.IGNORECASE)))
    return needles


def real_key() -> str | None:
    env = LOCAL / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            if line.strip().startswith("ANTHROPIC_API_KEY") and "=" in line:
                return line.split("=", 1)[1].strip().strip("'\"") or None
    return None


class Scanner:
    def __init__(self) -> None:
        self.needles = generic_needles()
        self.key = real_key()
        self.hits: list[str] = []
        self.files = 0
        self.bytes = 0

    def bytes_in(self, label: str, data: bytes, own: bool = False) -> None:
        for what, pattern in self.needles + ([("an e-mail address", EMAIL)] if own else []):
            m = pattern.search(data)
            if m:
                shown = "(hidden)" if what == "an Anthropic API key" else repr(m.group(0)[:48].decode("latin-1"))
                self.hits.append(f"{label}: {what} {shown}")
        if self.key and (self.key.encode() in data or self.key[:24].encode() in data):
            self.hits.append(f"{label}: YOUR REAL API KEY (hidden)")

    def file(self, label: str, path: Path, allow_transcripts: bool = False) -> None:
        self.files += 1
        data = path.read_bytes()
        self.bytes += len(data)
        if NEVER_SHIP.search(path.name):
            self.hits.append(f"{label}: a file that should never be published")
        if path.suffix.lower() == ".txt" and OTTER_LINE.search(data) and not allow_transcripts:
            self.hits.append(f"{label}: looks like a real Otter transcript")
        self.bytes_in(label, data, own=path.name in OWN_TEXT)


def python_strings(exe: Path) -> list[str]:
    """Every string constant and source path inside the Python code compiled into the exe."""
    from PyInstaller.archive.readers import CArchiveReader  # needs PyInstaller: run this with the build environment

    out: list[str] = []

    def walk(code) -> None:
        out.extend((code.co_filename, code.co_name))
        for const in code.co_consts:
            if isinstance(const, str):
                out.append(const)
            elif isinstance(const, bytes):
                out.append(const.decode("latin-1"))
            elif hasattr(const, "co_consts"):
                walk(const)

    pyz = CArchiveReader(str(exe)).open_embedded_archive("PYZ.pyz")
    for name in pyz.toc:
        code = pyz.extract(name)
        if hasattr(code, "co_consts"):
            walk(code)
    return out


def scan_source(folder: Path) -> int:
    scan = Scanner()
    print(f"scanning the source tree {folder}")
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder).as_posix()
        if any(part in {".git", "__pycache__"} for part in path.parts):
            continue
        if path.is_dir():
            if NEVER_SHIP.search(path.name):
                scan.hits.append(f"{rel}/: a folder that should never be published")
            continue
        if path.stat().st_size > MAX_SOURCE_FILE:
            scan.hits.append(f"{rel}: {path.stat().st_size / 1e6:.1f} MB is too big for a source repository")
        scan.file(rel, path, allow_transcripts=rel.startswith(FICTIONAL_TRANSCRIPTS))
    return finish(scan, f"{scan.files} files ({scan.bytes / 1e6:.2f} MB)")


def scan_release(release: Path) -> int:
    scan = Scanner()
    print(f"scanning {release}")
    names = {p.name for p in release.iterdir()}
    for extra in sorted(names - EXPECTED):
        scan.hits.append(f"unexpected file in the release: {extra}")
    for missing in sorted(EXPECTED - names):
        scan.hits.append(f"expected file missing: {missing}")
    for exe in release.glob("*.exe"):  # the setup program's payload is compressed, so this only catches the obvious
        scan.bytes_in(exe.name, exe.read_bytes())
    for txt in release.glob("*.txt"):
        scan.bytes_in(txt.name, txt.read_bytes(), own=True)
    strings: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        for archive in release.glob("*.zip"):
            with zipfile.ZipFile(archive) as z:
                z.extractall(tmp)
        for path in sorted(Path(tmp).rglob("*")):
            if path.is_file():
                scan.file(path.relative_to(tmp).as_posix(), path)
        exes = list(Path(tmp).rglob("MeetMiner.exe"))
        if exes:
            strings = python_strings(exes[0])
            scan.bytes_in("compiled app code", "\n".join(strings).encode("utf-8", "replace"))
    return finish(scan, f"{scan.files} files ({scan.bytes / 1e6:.1f} MB) in the no-install package and "
                        f"{len(strings)} strings inside the compiled app code")


def finish(scan: Scanner, summary: str) -> int:
    private = (LOCAL / "scan-needles.txt").is_file()
    print(f"checked {summary}; real key from .local/.env: {'yes' if scan.key else 'no'}; "
          f"private search terms: {'yes' if private else 'no (.local/scan-needles.txt not found)'}")
    if scan.hits:
        print(f"\nFOUND {len(scan.hits)} PROBLEM(S):")
        for hit in scan.hits:
            print("  -", hit)
        return 1
    print("CLEAN: no API key, no personal names or paths, no transcripts, no logs, no settings files.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", nargs="?", help="a release folder (default: dist/)")
    parser.add_argument("--source", metavar="DIR", help="scan a source tree instead of a release")
    args = parser.parse_args()
    if args.source:
        return scan_source(Path(args.source))
    return scan_release(Path(args.folder) if args.folder else ROOT / "dist")


if __name__ == "__main__":
    sys.exit(main())
