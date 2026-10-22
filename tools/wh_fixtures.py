"""원천징수영수증 시험용 가짜 zip·PDF를 만든다 (실제 자료 없음 — 이름·번호·금액 모두 가상).

  python tools/wh_fixtures.py zip-samples            → base64 두 줄 (ZipCrypto zip · AES-256 zip). index.html의 WH_ZIP_ZC · WH_ZIP_AES
  python tools/wh_fixtures.py receipt-zip <폴더>      → 가상 3명의 원천징수영수증 PDF를 zip 다섯으로 (화면에서 손으로 올려 보는 시험용)
                                                       receipts-glyph-zc.zip = 위하고처럼 한 글자씩 따로 적은 PDF (pdf.js가 글자마다 조각 하나)

암호는 둘 다 hr-portal-check. 필요한 것: cryptography(AES) · reportlab(PDF, receipt-zip만).
ZipCrypto 암호화는 여기서 직접 한다(외부 도구 없이). AES는 WinZip AE-2 방식(반디집에서 AES256을 고르면 이 방식).
같은 값으로 다시 돌리면 같은 파일이 나온다(난수 씨앗 고정) — 시험용이라 괜찮다.
"""
import base64, hashlib, hmac, io, random, struct, sys, zlib
from pathlib import Path
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

PW = b'hr-portal-check'
DOS_TIME, DOS_DATE = (14 << 11) | (30 << 5) | (20 // 2), ((2026 - 1980) << 9) | (10 << 5) | 21   # 2026-10-21 14:30:20
CRC = []
for n in range(256):
    c = n
    for _ in range(8):
        c = (c >> 1) ^ (0xEDB88320 if c & 1 else 0)
    CRC.append(c)


def zipcrypto(pw, data):   # 옛 zip 암호(PKWARE). 열쇠 셋을 글자마다 섞는다
    k = [0x12345678, 0x23456789, 0x34567890]
    def upd(b):
        k[0] = (k[0] >> 8) ^ CRC[(k[0] ^ b) & 0xFF]
        k[1] = ((k[1] + (k[0] & 0xFF)) * 134775813 + 1) & 0xFFFFFFFF
        k[2] = (k[2] >> 8) ^ CRC[(k[2] ^ (k[1] >> 24)) & 0xFF]
    for b in pw:
        upd(b)
    out = bytearray()
    for b in data:
        t = k[2] | 2
        out.append(b ^ ((t * (t ^ 1)) >> 8) & 0xFF)
        upd(b)
    return bytes(out)


def winzip_aes(pw, data, rnd):   # WinZip AES-256: 소금 16 + 확인 2 + 암호문 + HMAC-SHA1 앞 10. 카운터는 작은 끝(little-endian), 1부터
    salt = bytes(rnd.randrange(256) for _ in range(16))
    dk = hashlib.pbkdf2_hmac('sha1', pw, salt, 1000, 66)
    enc = Cipher(algorithms.AES(dk[:32]), modes.ECB()).encryptor()
    ks = enc.update(b''.join((i + 1).to_bytes(16, 'little') for i in range((len(data) + 15) // 16)))
    ct = bytes(a ^ b for a, b in zip(data, ks))
    return salt + dk[64:] + ct + hmac.new(dk[32:64], ct, 'sha1').digest()[:10]


def make_zip(files, mode, seed=1):
    """files = [(이름, 내용, 압축방식 0|8)], mode = plain | zc | zc-dd(데이터 설명자 · 확인 바이트 = 시각) | aes"""
    rnd, out, cd = random.Random(seed), bytearray(), bytearray()
    for name, data, method in files:
        nb = name.encode('utf-8')
        flags = 0x800 if not name.isascii() else 0   # 0x800 = 이름이 UTF-8
        crc = zlib.crc32(data)
        if method == 8:
            co = zlib.compressobj(9, zlib.DEFLATED, -15)
            body = co.compress(data) + co.flush()
        else:
            body = data
        need, extra, m, crc_field = 20, b'', method, crc
        if mode in ('zc', 'zc-dd'):
            flags |= 1 | (8 if mode == 'zc-dd' else 0)
            check = (DOS_TIME >> 8) & 0xFF if mode == 'zc-dd' else crc >> 24
            body = zipcrypto(PW, bytes(rnd.randrange(256) for _ in range(11)) + bytes([check]) + body)
        elif mode == 'aes':
            flags |= 1
            need, m, crc_field = 51, 99, 0   # AE-2: CRC 칸은 0 (HMAC이 대신 확인)
            extra = struct.pack('<HHH2sBH', 0x9901, 7, 2, b'AE', 3, method)
            body = winzip_aes(PW, body, rnd)
        dd = mode == 'zc-dd'
        at = len(out)
        out += struct.pack('<IHHHHHIIIHH', 0x04034B50, need, flags, m, DOS_TIME, DOS_DATE,
                           0 if dd else crc_field, 0 if dd else len(body), 0 if dd else len(data), len(nb), len(extra)) + nb + extra + body
        if dd:
            out += struct.pack('<IIII', 0x08074B50, crc_field, len(body), len(data))
        cd += struct.pack('<IHHHHHHIIIHHHHHII', 0x02014B50, need, need, flags, m, DOS_TIME, DOS_DATE,
                          crc_field, len(body), len(data), len(nb), len(extra), 0, 0, 0, 0, at) + nb + extra
    n = len(files)
    return bytes(out + cd + struct.pack('<IHHHHIIH', 0x06054B50, 0, 0, n, n, len(cd), len(out), 0))


def zip_samples():
    files = [('a.txt', '가상 시험 123'.encode('utf-8'), 8)]
    for mode in ('zc', 'aes'):
        print(base64.b64encode(make_zip(files, mode)).decode())


# ---------- receipt-zip ----------
def deduct(g):   # 근로소득공제 (소득세법 제47조) — 원 단위 버림, 한도 2,000만
    d = (g * 70 // 100 if g <= 5_000_000 else
         3_500_000 + (g - 5_000_000) * 40 // 100 if g <= 15_000_000 else
         7_500_000 + (g - 15_000_000) * 15 // 100 if g <= 45_000_000 else
         12_000_000 + (g - 45_000_000) * 5 // 100 if g <= 100_000_000 else
         14_750_000 + (g - 100_000_000) * 2 // 100)
    return min(d, 20_000_000)


def t10(x):   # 10원 미만 절사(0 쪽으로)
    return abs(x) // 10 * 10 * (1 if x >= 0 else -1)


# 이름, 쪽 수, 총급여, 결정(소득세, 지방), 주(현)(소득세, 지방), 종(전)(소득세, 지방) | None
PEOPLE = [
    ('가상일', 4, 48_350_000, (1_234_567, 123_456), (2_345_670, 234_560), None),          # 환급
    ('가상이', 5, 62_000_000, (3_456_789, 345_678), (3_000_000, 300_000), None),          # 추가납부
    ('가상삼', 3, 35_500_000, (987_654, 98_765), (700_000, 70_000), (400_000, 40_000)),   # 종(전)근무지 있음
]


def people():
    assert [deduct(g) for g in (5_000_000, 15_000_000, 45_000_000, 100_000_000, 10**9)] == [3_500_000, 7_500_000, 12_000_000, 14_750_000, 20_000_000]
    assert t10(-1_111_103) == -1_111_100 and t10(456_789) == 456_780
    rows = []
    for i, (name, pages, gross, dec, cur, prev) in enumerate(PEOPLE):
        pre = tuple(c + (prev[j] if prev else 0) for j, c in enumerate(cur))
        settle = tuple(t10(dec[j] - pre[j]) for j in range(2))
        rrn = str(85 + i) + '0101' + '-' + str(1 + i % 2) + '00000' + str(i + 1)   # 가짜 13자리 (이어 붙여 만든다)
        d = deduct(gross)
        rows.append(dict(name=name, pages=pages, gross=gross, deduct=d, earned=gross - d, dec=dec, cur=cur, prev=prev, pre=pre, settle=settle, rrn=rrn))
    return rows


def receipt_pdf(rows, glyph=False):   # glyph: 위하고처럼 글자마다 따로 적는다 — 한 낱말(금액) 안 글자 사이 0.1em, 띄어쓰기 = 1em 빈칸, 칸(소득세 · 지방소득세)은 멀리
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas
    pdfmetrics.registerFont(UnicodeCIDFont('HYSMyeongJo-Medium'))
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    f = lambda n: f'{n:,}'
    adv = lambda ch: 10 if ch == ' ' else pdfmetrics.stringWidth(ch, 'HYSMyeongJo-Medium', 10) + 1   # 10pt 글자: 띄어쓰기 1em, 글자 사이 1pt(0.1em)
    def put(x, y, text, right=False):   # right = x에서 끝나게(오른쪽 맞춤)
        if not glyph:
            return (c.drawRightString if right else c.drawString)(x, y, text)
        if right:
            x -= sum(map(adv, text)) - 1
        at = []
        for ch in text:
            at.append((x, ch))
            x += adv(ch)
        for x, ch in reversed(at):   # 뒤 글자부터 — pdf.js는 왼쪽으로 돌아가면 조각을 끊는다 → 실제 파일처럼 글자마다 조각 하나 (실제 파일이 왜 갈라지는지는 모른다, 결과만 같게)
            if ch != ' ':
                c.drawString(x, y, ch)
    def page(n, lines):   # lines = [(글자, 금액들)] — 금액은 칸 자리에 오른쪽 맞춤(소득세 · 지방소득세)
        c.setFont('HYSMyeongJo-Medium', 10)
        put(40, 810, f'■ 소득세법 시행규칙 [별지 제24호서식(1)] ({n}쪽)')
        y = 770
        for text, nums in lines:
            put(50, y, text)
            for x, v in zip((360, 460), nums):
                put(x, y, f(v), right=True)
            y -= 24
        c.showPage()
    for p in rows:
        dec, cur, prev, st = p['dec'], p['cur'], p['prev'], p['settle']
        page(1, [('근로소득 원천징수영수증', ()), ('② 대표자(성 명) 가상대표', ()), ('⑥ 성 명 ' + p['name'], ()), ('⑦ 주민등록번호 ' + p['rrn'], ()),   # 칸 번호(②⑥⑦)는 실제 서식처럼 — 화면이 ⑥ 성명 뒤 이름을 'PDF 속 이름'으로 읽는다
                 ('구 분 소득세 지방소득세 농어촌특별세', ()), ('결정세액', dec), ('종(전)근무지', prev or ()),
                 ('기납부 (결정세액의 세액을 적습니다)', ()),   # 스펙 3장: 결정세액이 든 금액 없는 줄 — 결정세액 줄로 고르면 안 된다
                 ('○ 주(현)근무지', cur), ('납부특례세액', ()), ('차감징수세액( - - - )', st)])
        page(2, [('근로소득공제 ' + f(p['deduct']) + ' 본 인 1,500,000', ()), ('근로소득금액 ' + f(p['earned']), ()),
                 ('결정세액( - - ) ' + f(dec[0]), ())])
        fill = ['※ 원천징수영수증 작성 방법 (가상 안내)', '차감징수세액이 음수이면 돌려받습니다 (가상 안내)', '소득공제 및 세액공제 명세 (가상)']
        for n in range(3, p['pages'] + 1):
            page(n, [(fill[(n - 3) % 3], ()), ('가상 보험료 공제 123,000', ()), ('가상 의료비 공제 45,600', ())])
    c.save()
    return buf.getvalue()


def receipt_zip(outdir):
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    rows = people()
    files = [('2025 원천징수영수증.pdf', receipt_pdf(rows), 8), ('note.txt', b'not a pdf', 0)]   # PDF가 아닌 파일 하나 — 건너뛰는지 본다
    for fname, mode in (('receipts-zc.zip', 'zc-dd'), ('receipts-aes.zip', 'aes'), ('receipts-plain.zip', 'plain')):
        (out / fname).write_bytes(make_zip(files, mode))
    (out / 'receipts-glyph-zc.zip').write_bytes(make_zip([('2025 원천징수영수증.pdf', receipt_pdf(rows, glyph=True), 8)], 'zc-dd'))   # 실제 파일 모양: ZipCrypto · PDF 하나
    (out / 'fake.7z').write_bytes(b'7z\xbc\xaf\x27\x1c\x00\x04' + bytes(random.Random(7).randrange(256) for _ in range(64)))
    print(f'{out}: receipts-zc.zip(ZipCrypto · 데이터 설명자) receipts-aes.zip(AES-256) receipts-plain.zip(암호 없음) receipts-glyph-zc.zip(한 글자씩) fake.7z — 암호 hr-portal-check')
    start, f = 1, lambda t: f'{t[0]:,} / {t[1]:,}'
    for p in rows:
        print(f"{p['name']}: {start}~{start + p['pages'] - 1}쪽 · 총급여 {p['gross']:,} (근로소득공제 {p['deduct']:,} + 근로소득금액 {p['earned']:,}) · "
              f"결정 {f(p['dec'])} · 기납부 {f(p['pre'])}{' (종전 ' + f(p['prev']) + ')' if p['prev'] else ''} · 차감 {f(p['settle'])}")
        start += p['pages']
    total = [sum(p['settle']) for p in rows]
    print(f'환급 합계 {sum(t for t in total if t < 0):,} · 추가납부 합계 {sum(t for t in total if t > 0):,}')


if __name__ == '__main__':
    cmd = sys.argv[1:2]
    if cmd == ['zip-samples']:
        zip_samples()
    elif cmd == ['receipt-zip'] and len(sys.argv) == 3:
        receipt_zip(sys.argv[2])
    else:
        sys.exit(__doc__)
