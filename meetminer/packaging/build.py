"""Build the Windows installer.

    .\\.build-venv\\Scripts\\python packaging\\build.py

Stages ONLY the app's own package (no transcripts, keys, logs, tests or development scripts), builds the app with
PyInstaller, packs it inside a setup program, and writes everything to dist\\ . Run it from the project folder, then run
packaging\\scan_release.py to check the result.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACK = ROOT / "packaging"
PACKAGE = ROOT / "src" / "meetminer"
sys.path.insert(0, str(ROOT / "src"))
from meetminer.version import APP_NAME, __version__  # noqa: E402

WORK = ROOT / "build"  # PyInstaller's scratch space (ignored by git)
STAGE = WORK / "stage"
BUILT = WORK / "dist"
RELEASE = ROOT / "dist"  # the finished downloadable files (ignored by git; attach them to a GitHub release)

README = f"""{APP_NAME} {__version__}
Search your Otter.ai transcripts in plain language. Answers come with the exact quote highlighted in the transcript.

INSTALL
  1. Double-click  MeetMiner-Setup-{__version__}.exe  and follow the steps. It installs for your Windows account only
     and needs no administrator rights.
  2. Windows may say "Windows protected your PC" because this installer is not code-signed. Choose "More info", then
     "Run anyway". (Only do this for a file you got from someone you trust.)
  3. Open "{APP_NAME}" from the Start menu or the Desktop.

FIRST RUN: the app asks for two things
  - Your Anthropic API key (Settings, or the "Open Settings" button). You need your own key from
    https://console.anthropic.com. Answers cost a fraction of a cent each; you pay Anthropic directly.
  - Your transcripts: in Otter open a conversation, choose Export, then Text (.txt), and save the file in the
    folder the app shows (or use "Add files..."). Default folder: Documents\\{APP_NAME}\\Transcripts

PRIVACY
  Your question, the earlier questions in the chat, and the text of your transcripts are sent to Anthropic to write each
  answer. Nothing else leaves your computer. The API key is stored encrypted for your Windows account. This package
  contains no transcripts, no keys and no personal data.

NO INSTALL?  Unzip  MeetMiner-{__version__}-no-install.zip  anywhere and run MeetMiner.exe inside it.

UNINSTALL
  Windows Settings > Apps > Installed apps > {APP_NAME} > Uninstall. It offers to delete your settings and saved key too.
  Your transcripts are never deleted.

{APP_NAME} is not affiliated with Otter.ai or Anthropic.
"""


def run(cmd: list, cwd: Path) -> None:
    print(">", " ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], cwd=cwd, check=True)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def zip_folder(folder: Path, zip_path: Path, prefix: str = "") -> int:
    count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                z.write(path, (Path(prefix) / path.relative_to(folder)).as_posix())
                count += 1
    return count


def version_file(name: str, original: str, description: str) -> Path:
    """The file-properties block (right-click > Properties > Details), made from the template."""
    text = (PACK / "version_info.txt").read_text(encoding="utf-8")
    text = text.replace("MeetMiner.exe", original).replace("u'MeetMiner'", f"u'{name}'")
    text = text.replace("MeetMiner - search your Otter.ai transcripts", description)
    numbers = (list(map(int, __version__.split("."))) + [0, 0, 0, 0])[:4]  # the template says 1.0.0.0
    text = text.replace("(1, 0, 0, 0)", f"({', '.join(map(str, numbers))})").replace("1.0.0.0", ".".join(map(str, numbers)))
    out = WORK / f"version_{name}.txt"
    out.write_text(text, encoding="utf-8")
    return out


def main() -> int:
    py = sys.executable
    shutil.rmtree(WORK, ignore_errors=True)
    shutil.rmtree(RELEASE, ignore_errors=True)
    STAGE.mkdir(parents=True)
    RELEASE.mkdir()

    print("== staging the app package (the only source that goes into a release)")
    shutil.copytree(PACKAGE, STAGE / "meetminer", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(PACK / "entry_app.py", STAGE / "entry_app.py")
    shutil.copy2(PACK / "installer.py", STAGE / "installer.py")
    shutil.copy2(PACKAGE / "assets" / "icon.png", STAGE / "icon.png")
    icon = STAGE / "meetminer" / "assets" / "icon.ico"
    data = ["--add-data", f"{STAGE / 'meetminer' / 'config'};meetminer/config",
            "--add-data", f"{STAGE / 'meetminer' / 'assets'};meetminer/assets"]
    scorecard = ROOT / "scorecards" / "latest.json"  # the Reports page shows this release's own scorecard
    if scorecard.is_file():
        (STAGE / "meetminer" / "reports").mkdir()
        shutil.copy2(scorecard, STAGE / "meetminer" / "reports" / "release-scorecard.json")
        data += ["--add-data", f"{STAGE / 'meetminer' / 'reports'};meetminer/reports"]
        print("   including scorecards/latest.json as the release scorecard")

    common = ["--noconfirm", "--clean", "--windowed", "--distpath", BUILT, "--workpath", WORK / "work",
              "--specpath", WORK / "spec", "--icon", icon, "--paths", STAGE,
              "--exclude-module", "pytest", "--exclude-module", "unittest"]

    print("== building the app (folder build, fast to start)")
    run([py, "-m", "PyInstaller", *common, "--onedir", "--name", "MeetMiner",
         "--version-file", version_file("MeetMiner", "MeetMiner.exe", "MeetMiner - search your Otter.ai transcripts"),
         *data, STAGE / "entry_app.py"], cwd=STAGE)
    app_dir = BUILT / "MeetMiner"
    if not (app_dir / "MeetMiner.exe").is_file():
        print("the app build failed")
        return 1

    print("== packing the app into the payload")
    payload = WORK / "payload.zip"
    files = zip_folder(app_dir, payload)
    print(f"   {files} files, {payload.stat().st_size / 1e6:.1f} MB")

    print("== building the setup program")
    run([py, "-m", "PyInstaller", *common, "--onefile", "--name", f"MeetMiner-Setup-{__version__}",
         "--version-file", version_file("MeetMinerSetup", f"MeetMiner-Setup-{__version__}.exe", "MeetMiner Setup"),
         "--add-data", f"{payload};.", "--add-data", f"{STAGE / 'icon.png'};.", STAGE / "installer.py"], cwd=STAGE)
    setup = BUILT / f"MeetMiner-Setup-{__version__}.exe"

    print("== writing dist/")
    shutil.copy2(setup, RELEASE / setup.name)
    zip_folder(app_dir, RELEASE / f"MeetMiner-{__version__}-no-install.zip", prefix=APP_NAME)
    (RELEASE / "README.txt").write_text(README, encoding="utf-8")
    sums = [f"{sha256(p)}  {p.name}" for p in sorted(RELEASE.iterdir()) if p.name != "SHA256SUMS.txt"]
    (RELEASE / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")
    for p in sorted(RELEASE.iterdir()):
        print(f"   {p.name}  {p.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
