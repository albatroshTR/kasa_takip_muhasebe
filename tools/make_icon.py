"""Program ikonunu üretir (assets/app.png ve assets/app.ico). Gerekirse yeniden çalıştırın."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
S = 512


def draw(size=S):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = size * 0.22
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=(30, 42, 68, 255))
    # kasa çekmecesi
    pad = size * 0.17
    top = size * 0.50
    d.rounded_rectangle([pad, top, size - pad, size * 0.80], radius=size * 0.05, fill=(255, 255, 255, 255))
    d.rounded_rectangle([size * 0.40, size * 0.62, size * 0.60, size * 0.67], radius=size * 0.02,
                        fill=(30, 42, 68, 255))
    # ekran / lira simgesi
    d.rounded_rectangle([size * 0.27, size * 0.18, size * 0.73, size * 0.44], radius=size * 0.05,
                        fill=(91, 155, 255, 255))
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", int(size * 0.22))
        d.text((size * 0.50, size * 0.31), "₺", font=font, fill=(255, 255, 255, 255), anchor="mm")
    except OSError:
        pass
    # yükselen yeşil çizgi
    d.rectangle([pad, size * 0.84, size - pad, size * 0.86], fill=(34, 197, 94, 255))
    return img


if __name__ == "__main__":
    big = draw()
    big.resize((256, 256), Image.LANCZOS).save(ROOT / "assets" / "app.png")
    big.save(ROOT / "assets" / "app.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128),
                                                 (256, 256)])
    print("ikon üretildi")
