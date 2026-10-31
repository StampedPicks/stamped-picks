"""Builds docs/index.html: the public results ledger (host free on GitHub Pages)."""
import html
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from . import config as C, store
from .model import fmt_american, parse_time

TZ = ZoneInfo(C.DISPLAY_TZ)
esc = html.escape


def _units_svg(rows):
    done = [r for r in rows if r.get("result") in ("win", "loss", "push")]
    if len(done) < 2:
        return '<p class="muted">The chart appears after the first two graded plays.</p>', "[]"
    pts, total = [(0, 0.0)], 0.0
    for i, r in enumerate(done, 1):
        total += float(r["profit"])
        pts.append((i, total))
    W, H, L, R, T, B = 720, 240, 44, 12, 14, 26
    lo, hi = min(0, min(y for _, y in pts)), max(0, max(y for _, y in pts))
    pad = max(1.0, (hi - lo) * 0.12)
    lo, hi = lo - pad, hi + pad
    x = lambda i: L + (W - L - R) * i / (len(pts) - 1)
    y = lambda v: T + (H - T - B) * (hi - v) / (hi - lo)
    step = max(1, round((hi - lo) / 4))
    grid, v = [], (int(lo) // step) * step
    while v <= hi:
        if v >= lo:
            cls = "zero" if v == 0 else "grid"
            grid.append(f'<line class="{cls}" x1="{L}" x2="{W-R}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>'
                        f'<text class="tick" x="{L-8}" y="{y(v)+4:.1f}" text-anchor="end">{v:+g}u</text>')
        v += step
    path = " ".join(f"{'M' if k == 0 else 'L'}{x(i):.1f},{y(val):.1f}" for k, (i, val) in enumerate(pts))
    lx, ly = x(pts[-1][0]), y(pts[-1][1])
    svg = (f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Cumulative units won by play number, '
           f'ending at {total:+.2f} units" id="chart">{"".join(grid)}'
           f'<text class="tick" x="{L}" y="{H-6}">Play 1</text>'
           f'<text class="tick" x="{W-R}" y="{H-6}" text-anchor="end">Play {len(done)}</text>'
           f'<path class="line" d="{path}"/>'
           f'<circle class="end" cx="{lx:.1f}" cy="{ly:.1f}" r="4"/>'
           f'<line id="xh" class="xh" y1="{T}" y2="{H-B}" x1="0" x2="0" visibility="hidden"/>'
           f'<circle id="dot" class="dot" r="5" visibility="hidden"/>'
           f'<rect class="hit" x="{L}" y="{T}" width="{W-L-R}" height="{H-T-B}"/></svg>')
    data = [{"x": round(x(i), 1), "y": round(y(val), 1), "n": i, "u": round(val, 2),
             "lab": "Start" if i == 0 else f"#{done[i-1]['pick_no']} {done[i-1]['label']}"} for i, val in pts]
    return svg, json.dumps(data)


def _pct(v, signed=True):
    return "—" if v is None else (f"{v:+.1%}" if signed else f"{v:.0%}")


def _row(r):
    label, emoji = C.SPORTS.get(r["sport"], (r["sport"], ""))
    t = parse_time(r["commence_time"]).astimezone(TZ)
    res = r.get("result") or ""
    badge = {"win": ("W", "✓ Win"), "loss": ("L", "✗ Loss"), "push": ("P", "– Push")}.get(res, ("", "Pending"))
    clv = r.get("clv")
    close = fmt_american(float(r["close_price"])) if r.get("close_price") else "—"
    prof = f"{float(r['profit']):+.2f}" if r.get("profit") not in (None, "") else "—"
    lean = ' <span class="lean">lean</span>' if r.get("tier") == "lean" else ""
    u = float(r["units"])
    stake = f'{u:g}u<div class="sub">${u * C.UNIT_DOLLARS:,.0f}</div>'
    if r.get("profit") not in (None, ""):
        d = round(float(r["profit"]), 2) * C.UNIT_DOLLARS
        prof += f'<div class="sub">{"+" if d >= 0 else "-"}${abs(d):,.0f}</div>'
    why = _why(r["pick_no"])
    return (f'<tr><td class="num">{esc(r["pick_no"])}</td><td>{t:%b %-d}</td><td>{esc(label)}</td>'
            f'<td class="play">{esc(r["label"])}{lean}<div class="sub">{esc(r["away"])} @ {esc(r["home"])}</div>{why}</td>'
            f'<td>{esc(C.BET_BOOKS.get(r["book"], r["book"]))}</td><td class="num">{esc(r["price_american"])}</td>'
            f'<td class="num">{stake}</td><td class="num">{float(r["ev"]):+.1%}</td><td class="num">{close}</td>'
            f'<td class="num">{_pct(float(clv)) if clv else "—"}</td>'
            f'<td><span class="res {badge[0]}">{badge[1]}</span>'
            f'<div class="sub">{esc(r.get("final_score") or "")}</div></td>'
            f'<td class="num">{prof}</td></tr>')


def _why(pick_no):
    f = C.DATA / "writeups" / f"{pick_no}.json"
    if not f.exists():
        return ""
    w = json.loads(f.read_text())
    items = [esc(x.lstrip("• ")) for x in w.get("market", [])]
    rs = w.get("research") or {}
    items += [esc(x) for x in rs.get("points", [])]
    if rs.get("risk"):
        items.append("Risk: " + esc(rs["risk"]))
    src = "".join(f' <a href="{esc(u)}" rel="nofollow">[{i}]</a>' for i, u in enumerate(rs.get("sources", []), 1))
    return ('<details><summary>Why we liked it</summary><ul>' + "".join(f"<li>{x}</li>" for x in items)
            + "</ul>" + (f'<div class="sub">Sources:{src}</div>' if src else "") + "</details>")


def _cta():
    """Join buttons under the header: the free Telegram channel, and VIP once it's on sale."""
    links = []
    if C.TG_FREE_URL:
        links.append(f'<a class="btn primary" href="{esc(C.TG_FREE_URL)}">Join the free Telegram</a>')
    if C.VIP_URL:
        links.append(f'<a class="btn" href="{esc(C.VIP_URL)}">Get VIP</a>')
    return f'<nav class="cta" aria-label="Join">{"".join(links)}</nav>' if links else ""


def _money(units):
    d = round(units, 2) * C.UNIT_DOLLARS
    return f'{"+" if d >= 0 else "-"}${abs(d):,.0f}'


def render(demo: bool = False):
    rows = store.read(store.LEDGER)
    board = store.read(store.BOARD_LEDGER)
    seals = store.read(store.SEALS)
    st, bst = store.stats(rows), store.stats(board)
    svg, data = _units_svg(rows)
    rec = f"{st['wins']}-{st['losses']}" + (f"-{st['pushes']}" if st["pushes"] else "")
    brec = f"{bst['wins']}-{bst['losses']}" + (f"-{bst['pushes']}" if bst["pushes"] else "")
    from .seal import verify
    verified = sum(1 for b in board if b.get("seal") and verify(b))
    updated = datetime.now(timezone.utc).astimezone(TZ).strftime("%b %-d, %Y %-I:%M %p ET")
    table = "".join(_row(r) for r in reversed(rows)) or \
        '<tr><td colspan="12" class="muted">No plays logged yet. The first Daily Play will appear here.</td></tr>'
    repo = (f'<a href="{esc(C.REPO_URL)}/commits/main">public commit history</a>' if C.REPO_URL
            else "public commit history")
    banner = ('<div class="demo">SIMULATED DATA. This page is a preview built from randomly generated games, '
              'not real picks. Do not share it as a track record.</div>') if demo else ""
    out = TEMPLATE
    for k, v in {
        "BRAND": esc(C.BRAND_NAME), "HANDLE": esc(C.BRAND_HANDLE), "BANNER": banner, "CTA": _cta(),
        "REC": rec, "UNITS": f"{st['profit']:+.2f}u", "ROI": _pct(st["roi"]),
        "WINPCT": _pct(st["win_pct"], False), "CLV": _pct(st["avg_clv"]),
        "BEAT": _pct(st["beat_close"], False), "NCLV": str(st["n_clv"]),
        "N": str(len(rows)), "PENDING": str(st["pending"]),
        "CHART": svg, "DATA": data, "TABLE": table, "REPO": repo, "UPDATED": updated,
        "BREC": brec, "BUNITS": f"{bst['profit']:+.2f}u ({_money(bst['profit'])})", "MONEY": _money(st["profit"]), "UD": str(C.UNIT_DOLLARS), "BROI": _pct(bst["roi"]),
        "BN": str(len(board)), "BSEALS": str(len(seals)), "BVER": str(verified),
    }.items():
        out = out.replace("{{" + k + "}}", v)
    C.DOCS.mkdir(parents=True, exist_ok=True)
    (C.DOCS / "index.html").write_text(out)
    return C.DOCS / "index.html"


TEMPLATE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{BRAND}} Ledger</title>
<meta name="description" content="Every {{BRAND}} Daily Play, timestamped, graded and measured against the closing line.">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{color-scheme:light;--bg:#f6f3ec;--card:#fcfbf7;--ink:#151513;--ink2:#52514e;--muted:#7a786f;--rule:#dcd7ca;
--line:#2a78d6;--good:#0f7a3d;--bad:#b42318;--goodbg:#e3f1e7;--badbg:#fbe4e1;--accent:#151513}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#131312;--card:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--muted:#8f8d84;--rule:#34332f;--line:#3987e5;--good:#5bc785;--bad:#f07a6e;--goodbg:#173323;--badbg:#3a1d1a;--accent:#f0efec}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#131312;--card:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--muted:#8f8d84;--rule:#34332f;--line:#3987e5;--good:#5bc785;--bad:#f07a6e;--goodbg:#173323;--badbg:#3a1d1a;--accent:#f0efec}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 "IBM Plex Sans",system-ui,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:28px 16px 48px}
.demo{background:#7a1f12;color:#fff;padding:10px 14px;border-radius:6px;font-weight:600;margin-bottom:20px}
header{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap;border-bottom:2px solid var(--accent);padding-bottom:14px}
h1{font:600 40px/1 Fraunces,Georgia,serif;margin:0;letter-spacing:-.5px}
.tag{color:var(--ink2);margin:6px 0 0}.handle{font-family:"IBM Plex Mono",monospace;color:var(--ink2)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1px;background:var(--rule);border:1px solid var(--rule);margin:22px 0}
.tile{background:var(--card);padding:14px 16px}.tile .k{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}
.tile .v{font:500 26px/1.2 "IBM Plex Mono",monospace;margin-top:4px}.tile .s{font-size:12px;color:var(--muted)}
section{background:var(--card);border:1px solid var(--rule);padding:18px;margin:18px 0}
h2{font:600 20px/1.2 Fraunces,Georgia,serif;margin:0 0 4px}.muted,.sub{color:var(--muted)}.sub{font-size:12px}
svg{width:100%;height:auto;display:block;margin-top:10px}
.grid{stroke:var(--rule);stroke-width:1}.zero{stroke:var(--ink2);stroke-width:1}.tick{fill:var(--muted);font:11px "IBM Plex Mono",monospace}
.line{fill:none;stroke:var(--line);stroke-width:2;stroke-linejoin:round}.end{fill:var(--line);stroke:var(--card);stroke-width:2}
.xh{stroke:var(--muted);stroke-dasharray:3 3}.dot{fill:var(--line);stroke:var(--card);stroke-width:2}.hit{fill:transparent;cursor:crosshair}
.chartbox{position:relative}#tip{position:absolute;pointer-events:none;background:var(--ink);color:var(--bg);font:12px "IBM Plex Mono",monospace;padding:6px 8px;border-radius:4px;white-space:nowrap;display:none}
.two{display:grid;grid-template-columns:1fr 1fr;gap:18px}@media(max-width:760px){.two{grid-template-columns:1fr}h1{font-size:32px}.tile:last-child:nth-child(odd){grid-column:1/-1}.tick{font-size:22px}}
.line,.grid,.zero,.xh{vector-effect:non-scaling-stroke}
.two section{margin:0}ol{padding-left:20px;margin:8px 0 0}li{margin:4px 0}
.scroll{overflow-x:auto;margin-top:10px}table{border-collapse:collapse;width:100%;min-width:820px;font-size:14px}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);border-bottom:1px solid var(--accent);padding:8px 8px}
td{border-bottom:1px solid var(--rule);padding:9px 8px;vertical-align:top}.num{font-family:"IBM Plex Mono",monospace;text-align:right;white-space:nowrap}
th.num{text-align:right}.play{font-weight:600}.lean{font-weight:400;font-size:11px;border:1px solid var(--rule);border-radius:3px;padding:0 4px;color:var(--muted)}
.cta{display:flex;flex-wrap:wrap;gap:12px;margin:18px 0 0}.btn{display:inline-block;padding:11px 18px;border:2px solid var(--ink);border-radius:6px;color:var(--ink);font-weight:600;text-decoration:none;min-height:44px;box-sizing:border-box}.btn.primary{background:#C42B1C;border-color:#C42B1C;color:#fff}.btn:hover{opacity:.88}
details{margin-top:4px;font-weight:400;font-size:13px}summary{cursor:pointer;color:var(--line)}details ul{margin:4px 0;padding-left:18px;color:var(--ink2)}
.res{font-weight:600;font-size:13px;padding:2px 6px;border-radius:3px}.res.W{color:var(--good);background:var(--goodbg)}.res.L{color:var(--bad);background:var(--badbg)}.res.P{color:var(--ink2)}
a{color:var(--line)}footer{color:var(--muted);font-size:13px;margin-top:28px;border-top:1px solid var(--rule);padding-top:14px}
</style></head><body><div class="wrap">
{{BANNER}}
<header><div><h1>{{BRAND}}</h1><p class="tag">Every pick stamped before game time. Every result public. Graded against the closing line.</p></div>
<div class="handle">{{HANDLE}} · updated {{UPDATED}}</div></header>
{{CTA}}

<div class="tiles">
<div class="tile"><div class="k">Record</div><div class="v">{{REC}}</div><div class="s">{{WINPCT}} wins · {{PENDING}} pending</div></div>
<div class="tile"><div class="k">Units won</div><div class="v">{{UNITS}}</div><div class="s">{{MONEY}} at ${{UD}}/unit · ROI {{ROI}}</div></div>
<div class="tile"><div class="k">Avg CLV</div><div class="v">{{CLV}}</div><div class="s">edge vs closing no-vig line</div></div>
<div class="tile"><div class="k">Beat the close</div><div class="v">{{BEAT}}</div><div class="s">of {{NCLV}} plays measured</div></div>
<div class="tile"><div class="k">Plays logged</div><div class="v">{{N}}</div><div class="s">none deleted, ever</div></div>
</div>

<section><h2>Cumulative units, Daily Play</h2><p class="muted" style="margin:0">1 unit = your normal bet. At ${{UD}} a unit, +1u means +${{UD}}. Hover for each play.</p>
<div class="chartbox">{{CHART}}<div id="tip"></div></div></section>

<div class="two">
<section><h2>Why closing line value matters</h2>
<p>Win streaks can be luck. Beating the closing line can't be faked for long: it means we consistently got a better price than the sharpest market settled on by game time. Bettors who beat the close over hundreds of plays win long term. This page shows that number for every play, wins and losses alike.</p></section>
<section><h2>How to verify us</h2><ol>
<li>Every play is committed to our {{REPO}} before the game starts. Git timestamps can't be backdated without it showing.</li>
<li>Members Board plays are published as sealed fingerprints before games, then revealed. {{BVER}} of {{BN}} revealed plays match their seal ({{BSEALS}} sealed so far).</li>
<li>Members Board record: <b>{{BREC}}</b>, {{BUNITS}}, ROI {{BROI}}.</li></ol></section>
</div>

<section><h2>Every Daily Play</h2><div class="scroll"><table>
<thead><tr><th class="num">#</th><th>Date</th><th>Sport</th><th>Play</th><th>Book</th><th class="num">Odds</th><th class="num">Bet</th><th class="num">Edge</th><th class="num">Close</th><th class="num">CLV</th><th>Result</th><th class="num">Won/lost</th></tr></thead>
<tbody>{{TABLE}}</tbody></table></div></section>

<footer>21+ only. Picks are opinions based on a statistical model, not guarantees, and past results don't predict future results. Bet only what you can afford to lose. If gambling stops being fun, call or text 1-800-GAMBLER.</footer>
</div>
<script>
(function(){const pts={{DATA}};const svg=document.getElementById('chart');if(!svg||!pts.length)return;
const tip=document.getElementById('tip'),xh=document.getElementById('xh'),dot=document.getElementById('dot'),box=svg.parentNode;
svg.querySelector('.hit').addEventListener('mousemove',e=>{const r=svg.getBoundingClientRect(),sx=(e.clientX-r.left)*svg.viewBox.baseVal.width/r.width;
let b=pts[0];for(const p of pts)if(Math.abs(p.x-sx)<Math.abs(b.x-sx))b=p;
xh.setAttribute('x1',b.x);xh.setAttribute('x2',b.x);dot.setAttribute('cx',b.x);dot.setAttribute('cy',b.y);xh.setAttribute('visibility','visible');dot.setAttribute('visibility','visible');
tip.textContent=b.lab+' · '+(b.u>=0?'+':'')+b.u.toFixed(2)+'u';tip.style.display='block';const k=r.width/svg.viewBox.baseVal.width;
tip.style.left=Math.min(b.x*k+10,r.width-tip.offsetWidth)+'px';tip.style.top=Math.max(0,b.y*k-34)+'px';});
svg.querySelector('.hit').addEventListener('mouseleave',()=>{tip.style.display='none';xh.setAttribute('visibility','hidden');dot.setAttribute('visibility','hidden');});})();
</script></body></html>"""
