"""Draw the app icon (a magnifying glass on a blue tile) with no extra packages.

    python tools/make_icon.py        writes src/meetminer/assets/icon.png and icon.ico
"""
import math
import struct
import zlib
from pathlib import Path

SIZE = 256
SAMPLES = 3  # 3x3 samples per pixel for smooth edges


def inside_round_rect(x: float, y: float, size: float, radius: float) -> bool:
    cx = min(max(x, radius), size - radius)
    cy = min(max(y, radius), size - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2


def dist_to_segment(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def is_glass(x: float, y: float) -> bool:
    ring = 38 <= math.hypot(x - 112, y - 108) <= 62
    handle = dist_to_segment(x, y, 156, 152, 205, 201) <= 14
    return ring or handle


def pixel(px: int, py: int) -> tuple[int, int, int, int]:
    covered = glass = 0
    for sy in range(SAMPLES):
        for sx in range(SAMPLES):
            x, y = px + (sx + 0.5) / SAMPLES, py + (sy + 0.5) / SAMPLES
            if inside_round_rect(x, y, SIZE, 56):
                covered += 1
                glass += is_glass(x, y)
    if not covered:
        return 0, 0, 0, 0
    t = py / SIZE  # vertical gradient, light blue to deep blue
    bg = (int(59 + (29 - 59) * t), int(130 + (78 - 130) * t), int(246 + (216 - 246) * t))
    mix = glass / covered
    rgb = tuple(round(b + (255 - b) * mix) for b in bg)
    return (*rgb, round(255 * covered / SAMPLES ** 2))


def png_bytes(pixels: list[list[tuple[int, int, int, int]]]) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"".join(b"\x00" + bytes(c for p in row for c in p) for row in pixels)
    header = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def main() -> None:
    here = Path(__file__).resolve().parent.parent / "src" / "meetminer" / "assets"
    png = png_bytes([[pixel(x, y) for x in range(SIZE)] for y in range(SIZE)])
    (here / "icon.png").write_bytes(png)
    # An .ico may hold a PNG directly; width/height 0 means 256.
    ico = struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 22) + png
    (here / "icon.ico").write_bytes(ico)
    print(f"wrote {here / 'icon.png'} and {here / 'icon.ico'} ({len(png):,} bytes of PNG)")


if __name__ == "__main__":
    main()
