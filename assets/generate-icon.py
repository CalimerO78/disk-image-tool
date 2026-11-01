"""Optionaler Entwicklerhelfer: das Startmenü-Icon neu zeichnen (Pillow)."""

from pathlib import Path

from PIL import Image, ImageDraw


SCALE = 4
image = Image.new("RGBA", (48 * SCALE, 48 * SCALE), (0, 0, 0, 0))
draw = ImageDraw.Draw(image)


def box(values):
    return tuple(value * SCALE for value in values)


# Klar erkennbare Festplatte mit Kopierpfeil; ohne Schrift auch klein lesbar.
draw.rounded_rectangle(box((2, 2, 46, 46)), radius=11 * SCALE, fill="#174C70")
draw.rounded_rectangle(box((7, 12, 41, 36)), radius=5 * SCALE,
                       fill="#F4FAFD", outline="#B7D3E2", width=2 * SCALE)
draw.ellipse(box((11, 18, 19, 26)), fill="#2A91B9")
draw.ellipse(box((14, 21, 16, 23)), fill="#F4FAFD")
draw.line([box((24, 20))[:2], box((35, 20))[:2]], fill="#174C70", width=3 * SCALE)
draw.line([box((24, 26))[:2], box((31, 26))[:2]], fill="#2A91B9", width=3 * SCALE)
draw.rounded_rectangle(box((11, 32, 37, 41)), radius=3 * SCALE, fill="#24B69B")
draw.polygon([box((28, 29))[:2], box((38, 36))[:2], box((28, 43))[:2]], fill="#FFFFFF")

output = Path(__file__).with_name("disk-image-tool.png")
image.resize((48, 48), Image.Resampling.LANCZOS).save(output, optimize=True)
print(output)
