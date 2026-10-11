"""환영 페이지에서 둥실 떠다니는 두 장비(포터블·디텍터)의 자리를 만든다.

환영 그림(public/img/home/welcome.jpg, 1672×941)은 한 장짜리라 장비를 따로 오려 낼 수 없다.
그래서 같은 그림을 "장비 둘레만 남기고" 한 장 더 얹어 살짝 움직인다. 이 스크립트가 그 "둘레"(투명도 그림)를 만든다.
가장자리를 부드럽게 번지게 해 두어, 움직여도 구름 위에서 섞여 이음매가 보이지 않는다.

  python tools/welcome_masks.py        (Pillow 필요)

결과: public/img/home/welcome-a.png(포터블), welcome-b.png(디텍터), welcome-t.png(휴대폰용 Better Together 글자 둘레)
그림을 바꾸면 아래 좌표(그림 크기에 대한 %)를 새 그림의 장비 윤곽에 맞춰 고치고 다시 돌린다.
"""
import pathlib
from PIL import Image, ImageChops, ImageDraw, ImageFilter

OUT = pathlib.Path(__file__).resolve().parent.parent / 'public' / 'img' / 'home'
W, H = 1672, 941
# 윤곽 밖으로 넓히는 폭과 가장자리 번짐(원본 px). 넓히는 폭 ≥ 움직이는 거리 + 번짐×2 여야 제자리에 있는 장비가 비치지 않는다
GROW, SOFT = 22, 6
PORTABLE = [(28.2, 40), (34, 37.6), (45, 33), (50, 32.3), (54, 33.3), (55.8, 36), (56.4, 50), (55.6, 54.5), (55.5, 64), (55.4, 73.2), (53.3, 72.6),
            (49.5, 62.5), (47, 61.5), (44, 63), (40, 63.5), (37.6, 62.5), (36.6, 82.2), (34.3, 82), (32.3, 75), (32, 63), (29.6, 61), (28.4, 57)]
DETECTOR = [(65.0, 35.6), (86.2, 27.6), (86.4, 24), (88.5, 23), (91, 24.5), (91.2, 29.5), (89.3, 30), (92.8, 72.5), (92.3, 73.8), (67.6, 70.8)]
# 두 장비 사이의 꽃 — 디텍터 앞에 놓여 있으니 디텍터와 함께 움직이지 않게 뺀다 (경계가 디텍터의 밋밋한 면 위를 지나가 티가 나지 않는다)
FLOWERS = [(56.5, 60), (61, 54.5), (66, 51.8), (71.5, 52.2), (73.5, 57), (75.8, 61.5), (75.8, 68), (73, 72.3), (62, 73), (56.5, 71)]


def shape(points, grow):
    """윤곽을 grow px 만큼 넓힌 흑백 그림"""
    m = Image.new('L', (W, H), 0)
    d = ImageDraw.Draw(m)
    p = [(x * W / 100, y * H / 100) for x, y in points]
    d.polygon(p, fill=255)
    d.line(p + p[:1], fill=255, width=grow * 2, joint='curve')
    for x, y in p:
        d.ellipse((x - grow, y - grow, x + grow, y + grow), fill=255)
    return m


def save(name, mask, soft):
    out = Image.new('LA', (W // 2, H // 2))   # 번진 그림이라 절반 크기면 충분하다
    out.putalpha(mask.filter(ImageFilter.GaussianBlur(soft)).resize((W // 2, H // 2), Image.LANCZOS))
    out.save(OUT / name, optimize=True)
    print(name, (OUT / name).stat().st_size, 'bytes')


save('welcome-a.png', shape(PORTABLE, GROW), SOFT)
save('welcome-b.png', ImageChops.subtract(shape(DETECTOR, GROW), shape(FLOWERS, 12)), SOFT)
# 휴대폰: 그림 왼쪽 위의 Better Together 글자 둘레. 바로 옆 포터블의 모서리는 빼고, 흐린 바탕에 녹도록 넓게 번지게 한다
title = Image.new('L', (W, H), 0)
ImageDraw.Draw(title).rounded_rectangle((0.035 * W, 0.07 * H, 0.335 * W, 0.445 * H), radius=70, fill=255)
save('welcome-t.png', ImageChops.subtract(title, shape(PORTABLE, 34)), 16)
