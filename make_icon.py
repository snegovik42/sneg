# -*- coding: utf-8 -*-
"""Рисует icon.ico для sneg: фиолетовый квадрат со снежинкой.

Без внешних библиотек: считаем пиксели сами, собираем BMP-иконку вручную.
Запуск: python make_icon.py <путь к .ico>
"""

import math
import struct
import sys

VIOLET = (0x68, 0x21, 0x7A)
WHITE = (0xFF, 0xFF, 0xFF)


def rounded_rect(x, y, size, radius):
    """Внутри ли точка скруглённого квадрата (координаты 0..size)."""
    if x < 0 or y < 0 or x > size or y > size:
        return False
    cx = min(max(x, radius), size - radius)
    cy = min(max(y, radius), size - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2


def seg_distance(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    if length2 == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def snowflake_segments(size):
    """Шесть лучей из центра, на каждом — две короткие веточки."""
    c = size / 2.0
    length = size * 0.36
    segments = []

    for k in range(6):
        angle = math.radians(60 * k)
        segments.append((c, c,
                         c + math.cos(angle) * length,
                         c + math.sin(angle) * length))

        # веточка отходит от луча на 62% его длины
        bx = c + math.cos(angle) * length * 0.62
        by = c + math.sin(angle) * length * 0.62

        for delta in (-42, 42):
            branch = angle + math.radians(delta)
            segments.append((bx, by,
                             bx + math.cos(branch) * length * 0.26,
                             by + math.sin(branch) * length * 0.26))

    return segments


def render(size, supersample=4):
    """Возвращает пиксели BGRA сверху вниз."""
    big = size * supersample
    radius = big * 0.24
    thickness = big * 0.028
    segments = snowflake_segments(big)

    # сначала копим цвета и прозрачность в крупном масштабе
    coarse = []
    for gy in range(big):
        row = []
        for gx in range(big):
            x, y = gx + 0.5, gy + 0.5
            if not rounded_rect(x, y, big, radius):
                row.append((0, 0, 0, 0))
                continue
            if any(seg_distance(x, y, *seg) <= thickness for seg in segments):
                row.append(WHITE + (255,))
            else:
                row.append(VIOLET + (255,))
        coarse.append(row)

    # усредняем блоки supersample x supersample — получаем сглаженные края
    pixels = bytearray()
    area = supersample * supersample
    for y in range(size):
        for x in range(size):
            r = g = b = a = 0
            for sy in range(supersample):
                for sx in range(supersample):
                    cr, cg, cb, ca = coarse[y * supersample + sy][x * supersample + sx]
                    r += cr * ca
                    g += cg * ca
                    b += cb * ca
                    a += ca
            if a == 0:
                pixels += bytes((0, 0, 0, 0))
            else:
                pixels += bytes((b // a, g // a, r // a, a // area))
    return bytes(pixels)


def ico_entry(size, pixels):
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    body = bytearray()
    for y in range(size - 1, -1, -1):
        body += pixels[y * size * 4:(y + 1) * size * 4]

    mask_row = ((size + 31) // 32) * 4
    mask = b"\x00" * (mask_row * size)
    return header + bytes(body) + mask


def build_ico(path, sizes=(16, 24, 32, 48, 64, 128, 256)):
    images = []
    for size in sizes:
        ss = 4 if size <= 64 else 2
        images.append((size, ico_entry(size, render(size, ss))))

    out = bytearray(struct.pack("<HHH", 0, 1, len(images)))
    offset = 6 + 16 * len(images)
    for size, data in images:
        out += struct.pack("<BBBBHHII",
                           0 if size >= 256 else size,
                           0 if size >= 256 else size,
                           0, 0, 1, 32, len(data), offset)
        offset += len(data)

    for _, data in images:
        out += data

    with open(path, "wb") as handle:
        handle.write(out)
    return len(out)


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "icon.ico"
    size = build_ico(target)
    print(f"готово: {target} ({size} байт)")
