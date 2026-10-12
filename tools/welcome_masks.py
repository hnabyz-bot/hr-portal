"""환영 페이지에서 둥실 떠다니는 두 장비(포터블·디텍터)의 자리를 만든다.

환영 그림(public/img/home/welcome.jpg, 1672×941)은 한 장짜리다. 같은 그림을 "장비 자리만 남기고" 한 장 더 얹어 움직인다.
이 스크립트가 그 "자리"(투명도 그림)를 만든다. 윤곽을 그대로 따서 장비만 움직이고, 비켜난 자리에 제자리의 장비가 비치지 않도록
장비를 지우고 둘레 색으로 메운 배경 조각을 밑에 깐다.
(2026-10-12 "장비가 배경과 같이 움직여 화면이 깨져 보인다"는 지적 — 처음에는 둘레의 구름까지 넓게 함께 움직였다)
디텍터는 앞에 꽃이 놓여 있다. 꽃은 움직이지 않아야 하므로 꽃 자리는 디텍터 조각에서도 배경 조각에서도 빼 원본이 그대로 보이게 한다.

  python tools/welcome_masks.py        (Pillow·numpy 필요)

결과: public/img/home/welcome-a.png(포터블 자리), welcome-b.png(디텍터 자리), welcome-p.png(두 장비를 지운 배경 조각), welcome-t.png(휴대폰용 Better Together 글자 둘레)
그림을 바꾸면 아래 좌표를 새 그림의 장비 윤곽에 맞춰 고치고 다시 돌린 뒤, index.html에서 이 그림들의 주소 뒤 ?v= 번호를 올린다.
"""
import pathlib
import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

OUT = pathlib.Path(__file__).resolve().parent.parent / 'public' / 'img' / 'home'
W, H = 1672, 941
# 포터블의 대강의 윤곽(그림 크기에 대한 %) — 휴대폰용 글자 둘레에서 포터블 모서리를 뺄 때만 쓴다
PORTABLE = [(28.2, 40), (34, 37.6), (45, 33), (50, 32.3), (54, 33.3), (55.8, 36), (56.4, 50), (55.6, 54.5), (55.5, 64), (55.4, 73.2), (53.3, 72.6),
            (49.5, 62.5), (47, 61.5), (44, 63), (40, 63.5), (37.6, 62.5), (36.6, 82.2), (34.3, 82), (32.3, 75), (32, 63), (29.6, 61), (28.4, 57)]
# 두 장비 사이의 꽃(그림 크기에 대한 %) — 디텍터 앞에 놓여 있으니 디텍터와 함께 움직이지 않게 뺀다 (경계가 디텍터의 밋밋한 면 위를 지나가 티가 나지 않는다).
# 디텍터 조각에서는 HOLE px 넓혀 빼고 그 가장자리를 SOFT로 번지게 한다. 배경 조각에서는 KEEP px 넓혀 뺀다 — 디텍터 조각의 구멍이 움직여도
# 그 밑에 늘 원본(제자리의 디텍터 변)이 있도록 KEEP을 HOLE + 움직이는 거리 + 번짐보다 크게 둔다.
# 디텍터의 변이 이 경계를 지나는 두 곳(왼쪽 변·아랫변)에서 변이 꺾여 보이지 않으려면 그 자리가 변을 따라서만 움직여야 한다
# → index.html의 .wc-fl.b 축(transform-origin)을 "왼쪽 변이 지나는 높이 × 아랫변이 지나는 가로 자리"(지금 1255, 476 = 75.1% 50.6%)에 두고 기울이기만 한다
FLOWERS = [(56.5, 60), (61, 54.5), (66, 51.8), (71.5, 52.2), (73.5, 57), (75.8, 61.5), (75.8, 68), (73, 72.3), (62, 73), (56.5, 71)]
HOLE, SOFT, KEEP = 12, 6, 28
# 포터블의 정확한 윤곽(원본 px, 시계 방향: 왼쪽 위 모서리 → 윗변 → 손잡이 → 오른쪽 다리 → 센서 아랫변 → 왼쪽 다리 → 몸통 왼쪽).
# 그림을 4~5배로 키우고 대비를 높여 눈금을 놓고 딴 것이다. 오차는 ±3px쯤이면 된다 — 움직이는 조각은 1px 안쪽으로 줄이고, 배경 조각은 6px 넓게 지운다
P_OUT = [(470.5, 430), (470.5, 415), (472.5, 405), (476, 395), (482.5, 386), (492.5, 379), (505, 372.5), (520, 367.5), (540, 362), (560, 357), (600, 348), (640, 340),
         (690, 327.5), (740, 314), (780, 307.5), (820, 306), (850, 309), (880, 315), (901, 324), (915, 335), (927.5, 350), (936, 370), (940, 392), (942, 420), (943, 450),
         (942, 480), (939, 497), (934, 506), (928, 514), (925, 525), (924, 545), (924, 600), (924, 640), (921, 660), (915, 674), (905, 685), (900, 686), (890, 684), (878, 677), (865, 664), (851, 648), (837, 630), (823, 610),
         (809, 590), (800, 577), (793, 567), (790, 564), (775, 577), (745, 588), (700, 597), (660, 598), (640, 598), (636, 604), (633, 640), (626, 680), (621, 715), (616, 740),
         (610, 755), (603, 762), (594, 763), (582, 757), (571, 742), (561, 722), (553, 700), (546, 662), (541, 640), (540, 605), (542, 597),
         (546, 588), (537, 585), (524, 580), (513, 571), (505, 560), (499, 545), (495, 530), (490, 515), (485, 500), (481, 485), (477.5, 470), (475, 455), (472.5, 442.5)]
# 뒤의 하늘·구름이 비쳐 보이는 구멍 — 손잡이 안, 오른쪽 다리 안, 왼쪽 다리 안. 구멍 속 배경은 움직이지 않는다
P_HOLES = [[(860, 356), (884, 352), (890, 362), (896, 395), (902, 430), (905, 455), (905, 470), (900, 478), (890, 481), (880, 476), (872, 463), (867, 450), (861, 420), (858, 385), (858, 365)],
           [(809, 529), (872, 528), (886, 536), (892, 550), (891, 600), (889, 620), (883, 630), (873, 620), (849, 592), (830, 568), (814, 548), (808, 537)],
           [(568, 592), (611, 591), (611, 600), (606, 620), (602, 640), (598, 660), (594, 680), (589, 697), (581, 682), (575, 662), (570, 642), (566, 620), (564, 602)]]
# 디텍터의 정확한 윤곽(원본 px, 시계 방향: 왼쪽 위 모서리 → 윗변 → 모서리에 얹힌 꽃 → 오른쪽 변(걸쳐 있는 꽃잎은 함께 움직인다) → 아랫변 → 꽃에 가린 왼쪽 아래 모서리 → 왼쪽 변)
D_OUT = [(1085, 342), (1087, 336), (1092, 333), (1105, 330), (1255, 298.6), (1330, 283), (1440, 260), (1456, 256.6), (1456, 252.5), (1455, 235), (1458.8, 225), (1465, 221),
         (1473.8, 222), (1481, 227), (1485, 220), (1492.5, 216.5), (1502.5, 218.5), (1510, 225), (1512.5, 235), (1511.5, 243.5), (1516.5, 248.5), (1519, 255), (1516.5, 262.5),
         (1510.5, 265.5), (1511.5, 272.5), (1512.5, 281), (1510, 287), (1506, 288.5), (1500, 286), (1493, 285), (1488.1, 292), (1495.1, 350), (1501.2, 400), (1506.1, 440),
         (1511, 480), (1514.5, 509), (1515.8, 511), (1521, 520), (1527.5, 530), (1530, 540), (1529.5, 547.5), (1532.5, 553.8), (1537, 558.8), (1533.8, 560.5), (1528.8, 554.5),
         (1523.8, 552), (1520, 552.5), (1520.8, 560), (1525.6, 600), (1533, 660), (1534, 667), (1532, 674), (1527, 680), (1519, 683), (1510, 684.5), (1495, 682.5),
         (1362.5, 673.6), (1249, 666), (1143, 658.9), (1141.8, 659), (1113.1, 498), (1099.2, 420)]


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


def exact(points):
    """원본 px 좌표의 윤곽 안쪽을 흰색으로 칠한 흑백 그림"""
    m = Image.new('L', (W, H), 0)
    ImageDraw.Draw(m).polygon(points, fill=255)
    return m


def widen(mask, px):
    """흰 곳을 px 만큼 넓힌다(음수면 줄인다)"""
    f = ImageFilter.MaxFilter if px > 0 else ImageFilter.MinFilter
    for n in [4] * (abs(px) // 4) + [abs(px) % 4] * (abs(px) % 4 > 0):   # 크기 1짜리 필터는 Pillow가 죽는다
        mask = mask.filter(f(n * 2 + 1))
    return mask


def fill_from_around(pic, gone):
    """gone(흰 곳)을 둘레의 색으로 메운다 — 아는 색을 번지게 해 바깥에서 안으로 한 겹씩 채운다.
    정교한 복원이 아니라 흐린 하늘·구름빛이다. 장비가 비켜난 가장자리(20px 안팎)에 잠깐 보일 뿐이라 이것으로 충분하다"""
    rgb, known = np.asarray(pic, float).copy(), np.asarray(gone) < 128
    for s in (2, 3, 5, 8, 12, 18, 27, 40, 60, 90):
        blur = lambda a: np.asarray(Image.fromarray(a.astype('uint8')).filter(ImageFilter.GaussianBlur(s)), float)
        den = blur(known * 255) / 255
        new = ~known & (den > .2)
        for c in range(3):
            rgb[..., c][new] = blur(rgb[..., c] * known)[new] / den[new]
        known = known | new
    assert known.all(), '메우지 못한 곳이 남았다 — 번지는 폭을 늘린다'
    return Image.fromarray(rgb.clip(0, 255).astype('uint8'))


def save(name, alpha, half=False):
    out = Image.new('LA', (W, H))
    out.putalpha(alpha)
    if half:   # 흐린 그림은 절반 크기면 충분하다
        out = out.resize((W // 2, H // 2), Image.LANCZOS)
    out.save(OUT / name, optimize=True)
    print(name, (OUT / name).stat().st_size, 'bytes')


# 움직이는 조각: 윤곽보다 1px 안쪽(배경을 묻혀 오지 않게), 구멍은 1px 넓게. 선명해야 하므로 원본 크기 그대로 둔다
holes = Image.new('L', (W, H), 0)
for h in P_HOLES:
    holes = ImageChops.lighter(holes, exact(h))
save('welcome-a.png', ImageChops.subtract(widen(exact(P_OUT), -1), widen(holes, 1)).filter(ImageFilter.GaussianBlur(.8)))
save('welcome-b.png', ImageChops.multiply(widen(exact(D_OUT), -1).filter(ImageFilter.GaussianBlur(.8)),
                                          ImageChops.invert(shape(FLOWERS, HOLE).filter(ImageFilter.GaussianBlur(SOFT)))))
# 배경 조각: 장비를 윤곽보다 6px 넓게 지우고(포터블 구멍 속 배경은 4px 안쪽부터 남긴다) 둘레 색으로 메운다. 흐린 그림이라 절반 크기면 충분하다.
# 디텍터는 가장자리 띠(안쪽으로 32px)만 쓴다 — 꽃 자리와 그 안쪽은 원본(제자리의 디텍터)이 보여야 한다
gone_p, gone_d = ImageChops.subtract(widen(exact(P_OUT), 6), widen(holes, -4)), widen(exact(D_OUT), 6)
plate = fill_from_around(Image.open(OUT / 'welcome.jpg').convert('RGB'), ImageChops.lighter(gone_p, gone_d))
band = ImageChops.subtract(ImageChops.subtract(widen(gone_d, 2), widen(exact(D_OUT), -32)), shape(FLOWERS, KEEP))
plate.putalpha(ImageChops.lighter(widen(gone_p, 2), band).filter(ImageFilter.GaussianBlur(1.5)))   # 메운 곳 밖은 원본 그대로라 가장자리가 번져도 이음매가 없다
plate.resize((W // 2, H // 2), Image.LANCZOS).save(OUT / 'welcome-p.png', optimize=True)
print('welcome-p.png', (OUT / 'welcome-p.png').stat().st_size, 'bytes')
# 휴대폰: 그림 왼쪽 위의 Better Together 글자 둘레. 바로 옆 포터블의 모서리는 빼고, 흐린 바탕에 녹도록 넓게 번지게 한다
title = Image.new('L', (W, H), 0)
ImageDraw.Draw(title).rounded_rectangle((0.035 * W, 0.07 * H, 0.335 * W, 0.445 * H), radius=70, fill=255)
save('welcome-t.png', ImageChops.subtract(title, shape(PORTABLE, 34)).filter(ImageFilter.GaussianBlur(16)), half=True)
