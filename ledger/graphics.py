"""Post graphics: the pick "ticket", the card-day image, and the before/after stamp GIF.

Everything is drawn with Pillow and the bundled brand fonts, so it runs anywhere (GitHub Actions
included) with no browser. Images are 1080x1350 (4:5 portrait, the shape that takes the most room in a
feed); GIFs are half size to stay small."""
from __future__ import annotations
import random
from pathlib import Path
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops
from . import config as C
from .model import parse_time

ASSETS = Path(__file__).parent / "assets"
FONTS = ASSETS / "fonts"
TZ = ZoneInfo(C.DISPLAY_TZ)
W, H = 1080, 1350
PAPER, INK, DARK, MUTED, RULE = "#F2ECE1", "#C42B1C", "#17140F", "#5A5247", "#DCD2C2"
LOSS_INK, PUSH_INK = "#3B3631", "#7A746B"


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / f"{name}.woff"), size)


def fit(text: str, name: str, max_size: int, max_width: int, min_size: int = 28) -> ImageFont.FreeTypeFont:
    size = max_size
    while size > min_size:
        f = font(name, size)
        if f.getlength(text) <= max_width:
            return f
        size -= 4
    return font(name, min_size)


def _paper() -> Image.Image:
    im = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(im)
    for y in range(80, H, 54):
        d.line([(0, y), (W, y)], fill=RULE, width=2)
    for x in (96, 104):
        d.line([(x, 0), (x, H)], fill="#D89C93", width=3)
    return im


def _ink(layer: Image.Image, seed: int, strength: int = 52) -> Image.Image:
    """Rubber-stamp texture: knock small speckles out of the ink."""
    random.seed(seed)
    noise = Image.effect_noise(layer.size, strength).filter(ImageFilter.GaussianBlur(0.6))
    mask = noise.point(lambda v: 0 if v < 70 else 255)
    a = ImageChops.multiply(layer.getchannel("A"), mask)
    layer.putalpha(a)
    return layer


def _stamp(text: str, color: str, size: int, pad=(34, 18), border=8, inner=True, seed=1, angle=-6,
           fname="PlexMono-SemiBold") -> Image.Image:
    f = font(fname, size)
    tw = int(f.getlength(text))
    bb = f.getbbox(text)
    th = bb[3] - bb[1]
    w, h = tw + 2 * pad[0], th + 2 * pad[1]
    layer = Image.new("RGBA", (w + 20, h + 20), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle([10, 10, 10 + w, 10 + h], radius=10, outline=color, width=border)
    if inner:
        d.rounded_rectangle([10 + border + 6, 10 + border + 6, 10 + w - border - 6, 10 + h - border - 6],
                            radius=6, outline=color, width=max(2, border // 3))
    d.text((10 + pad[0], 10 + pad[1] - bb[1]), text, font=f, fill=color)
    return _ink(layer, seed).rotate(angle, expand=True, resample=Image.BICUBIC)


def _paste_center(base: Image.Image, layer: Image.Image, cx: int, cy: int):
    base.paste(layer, (cx - layer.width // 2, cy - layer.height // 2), layer)


def _header(im: Image.Image, title: str, sub: str):
    d = ImageDraw.Draw(im)
    seal = Image.open(ASSETS / "seal.png").convert("RGBA").resize((118, 118), Image.LANCZOS)
    m = Image.new("L", seal.size, 0)
    ImageDraw.Draw(m).ellipse([0, 0, 117, 117], fill=255)
    im.paste(seal, (136, 48), m)
    d.text((272, 66), "STAMPED PICKS", font=font("Oswald-Bold", 44), fill=DARK)
    d.text((274, 122), "EVERY PICK STAMPED BEFORE GAME TIME", font=font("PlexMono-SemiBold", 22), fill=INK)
    d.text((136, 214), title, font=fit(title, "Oswald-Bold", 88, W - 136 - 70), fill=DARK)
    d.text((138, 324), sub, font=fit(sub, "PlexMono-SemiBold", 34, W - 138 - 70), fill=INK)


def _footer(im: Image.Image):
    d = ImageDraw.Draw(im)
    d.line([(136, H - 120), (W - 70, H - 120)], fill=DARK, width=3)
    d.text((136, H - 96), f"{C.BRAND_HANDLE}  ·  t.me/StampedPicksHQ", font=font("Inter-SemiBold", 30), fill=DARK)
    d.text((136, H - 52), "21+ · Bet what you can afford · 1-800-GAMBLER", font=font("Inter-Regular", 24), fill=MUTED)


def _when(row) -> str:
    t = parse_time(row["commence_time"]).astimezone(TZ)
    return t.strftime("%a %b %-d · %-I:%M %p ET").upper()


def _stamped(row) -> str:
    return "STAMPED " + parse_time(row["posted_at"]).astimezone(TZ).strftime("%a %-I:%M %p ET").upper()


def _money(u: float) -> str:
    return f"${round(u, 2) * C.UNIT_DOLLARS:,.0f}"


def _box(d: ImageDraw.ImageDraw, x, y, w, h, label, value, note=""):
    d.rectangle([x, y, x + w, y + h], outline=DARK, width=4)
    d.text((x + 22, y + 16), label, font=font("PlexMono-SemiBold", 24), fill=MUTED)
    d.text((x + 20, y + 50), value, font=fit(value, "Oswald-Bold", 76, w - 40), fill=DARK)
    if note:
        d.text((x + 22, y + h - 40), note, font=font("Inter-SemiBold", 22), fill=MUTED)


def result_stamp(result: str, scale: float = 1.0) -> Image.Image:
    word, color = {"win": ("CASHED", INK), "loss": ("LOSS", LOSS_INK), "push": ("PUSH", PUSH_INK)}[result]
    return _stamp(word, color, int(170 * scale), pad=(int(46 * scale), int(26 * scale)),
                  border=max(4, int(14 * scale)), seed=7, angle=-12, fname="ArchivoBlack")


# ---------------------------------------------------------------- the pick ticket
def ticket(row: dict, title: str) -> Image.Image:
    im = _paper()
    sport = C.SPORTS.get(row["sport"], (row["sport"],))[0]
    _header(im, title, f"{sport} · {_when(row)}")
    d = ImageDraw.Draw(im)
    d.text((138, 386), f"{row['away']} @ {row['home']}", font=fit(f"{row['away']} @ {row['home']}", "Inter-SemiBold", 44, W - 220),
           fill=MUTED)
    bet = row["label"].upper()
    d.text((132, 452), bet, font=fit(bet, "ArchivoBlack", 150, W - 132 - 70), fill=DARK)
    book = C.BET_BOOKS.get(row["book"], row["book"]).upper()
    d.text((136, 640), f"{row['price_american']} AT {book}", font=font("Oswald-Bold", 72), fill=INK)
    u = float(row["units"])
    bw = (W - 136 - 70 - 2 * 24) // 3
    _box(d, 136, 780, bw, 180, "BET", f"{u:g}U", f"{_money(u)} at ${C.UNIT_DOLLARS}/unit")
    _box(d, 136 + bw + 24, 780, bw, 180, "GOOD TO", row["play_to"], "worst price to take")
    if row.get("confidence") and float(row["ev"]) <= 0.005:     # no price edge to brag about: show the research grade
        _box(d, 136 + 2 * (bw + 24), 780, bw, 180, "CONFIDENCE", f"{row['confidence']}/10", "from the research")
    else:
        _box(d, 136 + 2 * (bw + 24), 780, bw, 180, "EDGE", f"{float(row['ev']):+.1%}", "vs sharp fair price")
    _paste_center(im, _stamp(_stamped(row), INK, 38, seed=int(row.get("pick_no") or 3)), 470, 1060)
    _footer(im)
    return im


def ticket_result(row: dict, title: str) -> Image.Image:
    im = ticket(row, title)
    d = ImageDraw.Draw(im)
    p = float(row["profit"])
    line = f"FINAL: {row.get('final_score') or 'settled'}   {p:+.2f}U ({'+' if p >= 0 else '-'}{_money(abs(p))})"
    d.rectangle([136, 1132, W - 70, 1196], fill=PAPER)
    d.text((140, 1140), line, font=fit(line, "Oswald-Bold", 46, W - 136 - 80), fill=DARK)
    _paste_center(im, result_stamp(row["result"], 0.8), W // 2 + 120, 742)
    return im


# ---------------------------------------------------------------- card days
def card_image(rows: list[dict], title: str, sub: str, results: bool = False) -> Image.Image:
    im = _paper()
    _header(im, title, sub)
    d = ImageDraw.Draw(im)
    y, row_h = 400, 205
    bet_w = W - 222 - (330 if results else 300)
    for i, r in enumerate(rows[:4]):
        d.ellipse([136, y + 8, 196, y + 68], outline=DARK, width=4)
        d.text((166, y + 38), str(i + 1), font=font("Oswald-Bold", 40), fill=DARK, anchor="mm")
        bet = r["label"].upper()
        d.text((222, y + 4), bet, font=fit(bet, "ArchivoBlack", 70, bet_w, min_size=40), fill=DARK)
        book = C.BET_BOOKS.get(r["book"], r["book"])
        t = parse_time(r["commence_time"]).astimezone(TZ).strftime("%-I:%M %p ET")
        u = float(r["units"])
        meta = (f"{r['price_american']} · {book} · {t} · {u:g}U ({_money(u)})" if results
                else f"{r['price_american']} · {book} · {t} · good to {r['play_to']}")
        d.text((224, y + 92), meta, font=fit(meta, "Inter-SemiBold", 32, bet_w + (0 if results else 0)), fill=MUTED)
        if results and r.get("result"):
            _paste_center(im, result_stamp(r["result"], 0.3), W - 200, y + 62)
        else:
            d.text((W - 90, y + 6), f"{u:g}U", font=font("Oswald-Bold", 64), fill=INK, anchor="ra")
            d.text((W - 90, y + 92), _money(u), font=font("Inter-SemiBold", 30), fill=MUTED, anchor="ra")
        d.line([(136, y + row_h - 30), (W - 70, y + row_h - 30)], fill=RULE, width=3)
        y += row_h
    _paste_center(im, _stamp(_stamped(rows[0]) + ", BEFORE EVERY GAME", INK, 27, seed=11), W // 2 + 40, 1140)
    _footer(im)
    return im


# ---------------------------------------------------------------- the odds board (teaser posts)
def odds_board(away: str, home: str, when: str, sport: str, lines: dict) -> Image.Image:
    """A sportsbook-style board for one game: spread, moneyline and total for both sides.
    lines = {"away": {"spread": "+3.5 -108", "money": "+166", "total": "O 46.5 -120"}, "home": {...}}.
    Shows the market only; it never marks which side we're on."""
    im = _paper()
    _header(im, f"{away.split()[-1].upper()} @ {home.split()[-1].upper()}", f"{sport} · {when}")
    d = ImageDraw.Draw(im)
    cols = [("SPREAD", "spread"), ("MONEY", "money"), ("TOTAL", "total")]
    x0, top, name_w = 136, 470, 250
    cw, ch, gap = (W - 70 - x0 - name_w - 2 * 18) // 3, 170, 18
    for j, (label, _) in enumerate(cols):
        cx = x0 + name_w + j * (cw + gap) + cw // 2
        d.text((cx, top - 50), label, font=font("PlexMono-SemiBold", 30), fill=MUTED, anchor="mm")
    for i, (side, team) in enumerate((("away", away), ("home", home))):
        y = top + i * (ch + 40)
        d.text((x0, y + ch // 2), team.split()[-1].upper(), font=fit(team.split()[-1].upper(), "Oswald-Bold", 64, name_w - 20),
               fill=DARK, anchor="lm")
        for j, (_, key) in enumerate(cols):
            x = x0 + name_w + j * (cw + gap)
            d.rounded_rectangle([x, y, x + cw, y + ch], radius=14, outline=DARK, width=4)
            val = (lines.get(side) or {}).get(key) or "—"
            parts = val.split(" ")
            top_t, price = (" ".join(parts[:-1]), parts[-1]) if len(parts) > 1 else ("", parts[0])
            if top_t:
                d.text((x + cw // 2, y + 52), top_t, font=fit(top_t, "Oswald-Medium", 46, cw - 24), fill=MUTED, anchor="mm")
                d.text((x + cw // 2, y + 118), price, font=fit(price, "Oswald-Bold", 58, cw - 24), fill=DARK, anchor="mm")
            else:
                d.text((x + cw // 2, y + ch // 2), price, font=fit(price, "Oswald-Bold", 64, cw - 24), fill=DARK, anchor="mm")
    _paste_center(im, _stamp("PREMIUM PLAY STAMPED", INK, 40, seed=5, angle=-4), W // 2 + 40, 1060)
    _footer(im)
    return im


# ---------------------------------------------------------------- the before/after GIF
def stamp_gif(row: dict, title: str, path: Path) -> Path:
    """The pick ticket, then the result stamp slamming onto it. About 1 MB, loops once per view."""
    base = ticket(row, title).resize((W // 2, H // 2), Image.LANCZOS).convert("RGBA")
    final = ticket_result(row, title).resize((W // 2, H // 2), Image.LANCZOS).convert("RGBA")
    stamp = result_stamp(row["result"], 0.4)
    frames, durations = [base.convert("RGB")], [900]
    cx, cy = W // 4 + 60, 371
    for i, s in enumerate((2.2, 1.8, 1.45, 1.2, 1.05)):
        fr = base.copy()
        st = stamp.resize((int(stamp.width * s), int(stamp.height * s)), Image.LANCZOS)
        a = st.getchannel("A").point(lambda v, k=(i + 1) / 5: int(v * k))
        st.putalpha(a)
        fr.paste(st, (cx - st.width // 2, cy - st.height // 2), st)
        frames.append(fr.convert("RGB"))
        durations.append(55)
    for dx, dy in ((5, 3), (-4, -2), (0, 0)):           # impact shake
        fr = Image.new("RGBA", final.size, PAPER)
        fr.paste(final, (dx, dy))
        frames.append(fr.convert("RGB"))
        durations.append(50)
    frames.append(final.convert("RGB"))
    durations.append(3500)
    pal = [f.quantize(colors=96, method=Image.MEDIANCUT, dither=Image.NONE) for f in frames]
    pal[0].save(path, save_all=True, append_images=pal[1:], duration=durations, loop=0, optimize=True, disposal=1)
    return path


def save(im: Image.Image, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    im.convert("RGB").save(path, "PNG", optimize=True)
    return path
