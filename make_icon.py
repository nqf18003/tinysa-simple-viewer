"""アイコン icon.ico を作る（デザイン案 2「本体の画面」: 緑のマス目に黄色の波形。2026-11-01 ユーザーが選んだ）。

作り直すとき:  .venv\\Scripts\\python.exe make_icon.py
"""

from PIL import Image, ImageDraw

SIZES = [16, 24, 32, 48, 64, 128, 256]
SS = 4  # なめらかにするため 4 倍で描いて縮める

# 64×64 の座標で描いた形（案 2 と同じ）
TRACE = [(8, 48), (16, 47), (22, 49), (28, 46), (32, 16), (36, 46), (42, 48), (48, 44), (56, 47)]


def draw(size):
    s = size * SS / 64
    img = Image.new("RGBA", (size * SS, size * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size * SS - 1, size * SS - 1], radius=12 * s, fill=(10, 10, 10, 255))
    # 小さいアイコンはマス目を減らして太くする（つぶれて見えなくならないように）
    small = size <= 32
    lines = [20, 44] if small else [8, 20, 32, 44, 56]
    gw = max(1, round((2 if small else 1) * s))
    for v in lines:
        d.line([(v * s, 8 * s), (v * s, 56 * s)], fill=(46, 94, 46, 255), width=gw)
        d.line([(8 * s, v * s), (56 * s, v * s)], fill=(46, 94, 46, 255), width=gw)
    tw = round((4 if small else 2.5) * s)
    d.line([(x * s, y * s) for x, y in TRACE], fill=(255, 212, 0, 255), width=tw, joint="curve")
    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    imgs = [draw(n) for n in SIZES]
    imgs[-1].save("icon.ico", sizes=[(n, n) for n in SIZES], append_images=imgs[:-1])
    print("icon.ico を作りました")
