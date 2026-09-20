#!/usr/bin/env python3
"""Assemble the System Sentinel identity board as one HTML file.

Everything on the board is drawn in code. The figure's vocabulary is Windows' own
(providers and event IDs anyone can look up) and the detector's defaults come from the
product's source (backend/services/whea/storms.py). The minutes on the schematic are
illustrative and say so; no capture from David's machine is used.
"""
import base64, io, pathlib
from PIL import Image

HERE = pathlib.Path(__file__).parent
OUT = HERE / 'system-sentinel-directions.html'

def img_data(path, width=None, quality=80):
    im = Image.open(path).convert('RGB')
    if width and im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    buf = io.BytesIO(); im.save(buf, 'JPEG', quality=quality, optimize=True)
    return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()

# ---------------------------------------------------------------- palettes
A = dict(field='#0B1A16', lit='#163429', deep='#06100D', ink='#EAF3EE', muted='#8FB0A3', rule='rgba(234,243,238,.14)',
         core='#C9FFE1', mid='#6FF0A8', dp='#1E8F5E')
A_AMBER = dict(core='#FFE4B0', mid='#FFB454', dp='#A8651A')
B = dict(paper='#EDF0F2', lit='#F9FAFB', shade='#DCE2E7', ink='#151A1F', muted='#66717C', rule='#C6CFD6', red='#D42B2B', red_deep='#9B1B1B')
C = dict(field='#08070B', lit='#1B0F2E', deep='#040306', ink='#F3EFF7', muted='#9B90B3', rule='rgba(243,239,247,.14)',
         iron=['#12003A', '#5B0F8C', '#C21E7A', '#F0552A', '#FFA630', '#FFF1B8'])

# ---------------------------------------------------------------- the record's vocabulary (public)
# minutes before the last record, provider, id, meaning (Windows' own message, shortened), count in that bucket
BEFORE = [
    (-31, 'WHEA-Logger', 19, 'corrected machine check', 1),
    (-24, 'WHEA-Logger', 19, 'corrected machine check', 1),
    (-17, 'Display', 4101, 'display driver recovered', 1),
    (-12, 'WHEA-Logger', 19, 'corrected machine check', 3),
    (-9, 'storahci', 129, 'reset to device issued', 1),
    (-4, 'WHEA-Logger', 19, 'corrected machine check', 5),
]
AFTER = [
    ('Kernel-General', 12, 'the operating system started'),
    ('Kernel-Power', 41, 'rebooted without cleanly shutting down'),
    ('EventLog', 6008, 'the previous shutdown was unexpected'),
]
# detector defaults, backend/services/whea/storms.py
BUCKET_S, BURST, RECENT_BUCKETS, BASELINE_BUCKETS, ACCEL = 60, 5, 10, 240, 2.0

# ---------------------------------------------------------------- marks
S_PATH = 'M45 19C42 12 22 11 21 20C20 29 44 30 44 41C44 50 23 53 18 45'
S_END = (18, 45)

def grat_defs(uid, color, cell=8):
    return (f'<pattern id="g{uid}" width="{cell}" height="{cell}" patternUnits="userSpaceOnUse">'
            f'<path d="M0 .5H{cell}M.5 0V{cell}" stroke="{color}" stroke-opacity=".13" stroke-width="1"/></pattern>')

def mark_trace(uid, size=64, tile='#0B1A16', core='var(--core)', mid='var(--mid)', tile_on=True, radius=.22, grat=True, label='System Sentinel mark'):
    r = round(64 * radius)
    parts = [f'<svg width="{size}" height="{size}" viewBox="0 0 64 64" role="img" aria-label="{label}"><defs>{grat_defs(uid, "#9CFFC4") if grat else ""}'
             f'<filter id="f{uid}" x="-40%" y="-40%" width="180%" height="180%"><feGaussianBlur stdDeviation="2.6"/></filter></defs>']
    if tile_on:
        parts.append(f'<rect width="64" height="64" rx="{r}" fill="{tile}"/>')
        if grat:
            parts.append(f'<rect x="1" y="1" width="62" height="62" rx="{max(r-1,0)}" fill="url(#g{uid})"/>')
            parts.append('<path d="M32 4v4M32 56v4M4 32h4M56 32h4" stroke="#9CFFC4" stroke-opacity=".35" stroke-width="1"/>')
    parts.append(f'<path d="{S_PATH}" fill="none" stroke-width="7" stroke-linecap="round" style="stroke:{mid};opacity:.55" filter="url(#f{uid})"/>')
    parts.append(f'<path d="{S_PATH}" fill="none" stroke-width="2.4" stroke-linecap="round" style="stroke:{core}"/>')
    parts.append(f'<circle cx="{S_END[0]}" cy="{S_END[1]}" r="4.2" style="fill:{mid};opacity:.6" filter="url(#f{uid})"/>')
    parts.append(f'<circle cx="{S_END[0]}" cy="{S_END[1]}" r="2.6" style="fill:{core}"/>')
    parts.append('</svg>')
    return ''.join(parts)

def mark_tag(uid, size=64, tile=None, ink='#D42B2B', letter='#FFFFFF', radius=.22, label='System Sentinel mark'):
    """An exhibit tag: a rounded label with a punched hole, the S set in it."""
    r = round(64 * radius)
    bg = f'<rect width="64" height="64" rx="{r}" fill="{tile}"/>' if tile else ''
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 64 64" role="img" aria-label="{label}">{bg}'
            f'<path d="M14 18h36a3 3 0 0 1 3 3v22a3 3 0 0 1-3 3H14a3 3 0 0 1-3-3V21a3 3 0 0 1 3-3z" fill="{ink}"/>'
            f'<circle cx="19" cy="32" r="3.2" fill="{tile or "#EDF0F2"}"/>'
            f'<text x="38" y="39.5" text-anchor="middle" font-family="IBM Plex Mono, Menlo, monospace" font-weight="600" font-size="20" fill="{letter}">S</text></svg>')

def mark_heat(uid, size=64, tile='#08070B', radius=.22, label='System Sentinel mark'):
    r = round(64 * radius)
    stops = ''.join(f'<stop offset="{i/(len(C["iron"])-1):.2f}" stop-color="{c}"/>' for i, c in enumerate(C['iron']))
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 64 64" role="img" aria-label="{label}"><defs>'
            f'<linearGradient id="h{uid}" x1="45" y1="19" x2="18" y2="45" gradientUnits="userSpaceOnUse">{stops}</linearGradient>'
            f'<filter id="f{uid}" x="-40%" y="-40%" width="180%" height="180%"><feGaussianBlur stdDeviation="3"/></filter></defs>'
            f'<rect width="64" height="64" rx="{r}" fill="{tile}"/>'
            f'<path d="{S_PATH}" fill="none" stroke="url(#h{uid})" stroke-width="9" stroke-linecap="round" opacity=".6" filter="url(#f{uid})"/>'
            f'<path d="{S_PATH}" fill="none" stroke="url(#h{uid})" stroke-width="3" stroke-linecap="round"/>'
            f'<circle cx="{S_END[0]}" cy="{S_END[1]}" r="3" fill="#FFF1B8"/></svg>')

# ---------------------------------------------------------------- the ribbon (hero light)
def ribbon(uid, mode='phos', narrow=False):
    """One trace in flow between the hero and the figure: the horizon the text sits on, flat, one pulse at the
    right, flat. Phosphor uses the accent vars; heat uses the ironbow. The narrow path keeps its pulse wide
    enough to survive the non-uniform scale of a phone-width panel."""
    d = ('M-10 84H700C760 84 790 22 840 22C890 22 920 84 980 84H1290' if narrow
         else 'M-10 84H960C990 84 1000 22 1030 22C1060 22 1070 84 1100 84H1290')
    if mode == 'heat':
        stops = ''.join(f'<stop offset="{i/(len(C["iron"])-1):.2f}" stop-color="{c}"/>' for i, c in enumerate(C['iron']))
        defs = f'<linearGradient id="r{uid}" x1="0" y1="0" x2="1" y2="0">{stops}</linearGradient>'
        glow, core = f'url(#r{uid})', '#FFF1B8'
        return (f'<div class="trace"><svg viewBox="0 0 1280 120" preserveAspectRatio="none" aria-hidden="true"><defs>{defs}'
                f'<filter id="b{uid}" x="-10%" y="-100%" width="120%" height="300%"><feGaussianBlur stdDeviation="16"/></filter>'
                f'<filter id="c{uid}" x="-10%" y="-100%" width="120%" height="300%"><feGaussianBlur stdDeviation="3"/></filter></defs>'
                f'<path d="{d}" fill="none" stroke="{glow}" stroke-width="26" opacity=".5" filter="url(#b{uid})"/>'
                f'<path d="{d}" fill="none" stroke="{glow}" stroke-width="5" opacity=".9" filter="url(#c{uid})"/>'
                f'<path d="{d}" fill="none" stroke="{core}" stroke-width="1.3" opacity=".85"/></svg></div>')
    return (f'<div class="trace"><svg viewBox="0 0 1280 120" preserveAspectRatio="none" aria-hidden="true"><defs>'
            f'<filter id="b{uid}" x="-10%" y="-100%" width="120%" height="300%"><feGaussianBlur stdDeviation="14"/></filter>'
            f'<filter id="c{uid}" x="-10%" y="-100%" width="120%" height="300%"><feGaussianBlur stdDeviation="2.5"/></filter></defs>'
            f'<path d="{d}" fill="none" stroke-width="22" style="stroke:var(--mid);opacity:.35" filter="url(#b{uid})"/>'
            f'<path d="{d}" fill="none" stroke-width="4" style="stroke:var(--mid);opacity:.8" filter="url(#c{uid})"/>'
            f'<path d="{d}" fill="none" stroke-width="1.4" style="stroke:var(--core);opacity:.95"/></svg></div>')

# ---------------------------------------------------------------- the figure: the record, read back
W, H = 1040, 300
T0, T1 = -40, 0
X0, X1 = 70, 700
PX = (X1 - X0) / (T1 - T0)
AXIS = 176
GAP = (700, 790)
def tx(t): return X0 + (t - T0) * PX

def figure_svg(uid, mode='phos'):
    ink, muted = ('#EAF3EE', '#8FB0A3') if mode != 'heat' else ('#F3EFF7', '#9B90B3')
    if mode == 'heat':
        stops = ''.join(f'<stop offset="{i/(len(C["iron"])-1):.2f}" stop-color="{c}"/>' for i, c in enumerate(C['iron']))
        defs = (f'<linearGradient id="hg{uid}" x1="0" y1="1" x2="0" y2="0">{stops}</linearGradient>'
                f'<linearGradient id="hs{uid}" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{C["iron"][0]}"/><stop offset=".55" stop-color="{C["iron"][2]}"/><stop offset="1" stop-color="{C["iron"][4]}"/></linearGradient>')
        stroke, glow = f'url(#hg{uid})', f'url(#hg{uid})'
    else:
        defs = ''
        stroke, glow = 'var(--core)', 'var(--mid)'
    p = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="The last forty minutes of a system log before a freeze, then the gap where the freeze left no record, then the three records Windows writes at the next start. Providers and event IDs are Windows\' own; the minutes are illustrative.">'
         f'<defs>{defs}<filter id="fg{uid}" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3"/></filter>'
         f'<pattern id="hatch{uid}" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><path d="M0 0v8" stroke="{muted}" stroke-opacity=".35" stroke-width="1"/></pattern></defs>']
    mono = 'font-family="JetBrains Mono, Menlo, monospace"'
    # axis and minute ticks
    if mode == 'heat':
        p.append(f'<rect x="{X0}" y="{AXIS-9}" width="{X1-X0}" height="18" fill="url(#hs{uid})" opacity=".35"/>')
    p.append(f'<path d="M{X0} {AXIS}H{X1}" stroke="{muted}" stroke-width="1" opacity=".9"/>')
    for t in range(T0, T1 + 1):
        x = tx(t); big = t % 5 == 0
        p.append(f'<path d="M{x:.1f} {AXIS}v{9 if big else 4}" stroke="{muted}" stroke-width="1" opacity="{.9 if big else .5}"/>')
        if big:
            lbl = '0' if t == 0 else f'−{abs(t)}'
            p.append(f'<text x="{x:.1f}" y="{AXIS+22}" text-anchor="middle" {mono} font-size="9.5" fill="{muted}">{lbl}</text>')
    p.append(f'<text x="{X0}" y="{AXIS+40}" {mono} font-size="9.5" letter-spacing=".08em" fill="{muted}">MINUTES BEFORE THE LAST RECORD</text>')
    # the events, labels on three heights so neighbours never share one
    for i, (t, prov, eid, meaning, n) in enumerate(BEFORE):
        x = tx(t); up = (44, 72, 100)[i % 3]
        for k in range(n):
            xx = x + (k - (n - 1) / 2) * 2.6
            p.append(f'<path d="M{xx:.1f} {AXIS-2}V{AXIS-up}" stroke="{glow}" stroke-width="5" opacity=".35" filter="url(#fg{uid})"/>')
            p.append(f'<path d="M{xx:.1f} {AXIS-2}V{AXIS-up}" stroke="{stroke}" stroke-width="1.4"/>')
        count = f' ×{n}' if n > 1 else ''
        p.append(f'<text x="{x:.1f}" y="{AXIS-up-16}" text-anchor="middle" {mono} font-size="10.5" font-weight="500" fill="{ink}">{prov} {eid}{count}</text>')
        note = meaning if n < BURST else f'{n} in one bucket: a burst'
        p.append(f'<text x="{x:.1f}" y="{AXIS-up-5}" text-anchor="middle" {mono} font-size="9" fill="{muted}">{note}</text>')
    # the gap
    p.append(f'<rect x="{GAP[0]}" y="{AXIS-52}" width="{GAP[1]-GAP[0]}" height="104" fill="url(#hatch{uid})"/>')
    p.append(f'<rect x="{GAP[0]}" y="{AXIS-52}" width="{GAP[1]-GAP[0]}" height="104" fill="none" stroke="{muted}" stroke-opacity=".5" stroke-dasharray="3 3"/>')
    gx = (GAP[0] + GAP[1]) / 2
    p.append(f'<text x="{gx}" y="{AXIS-72}" text-anchor="middle" {mono} font-size="10.5" font-weight="500" fill="{ink}">no record</text>')
    p.append(f'<text x="{gx}" y="{AXIS-60}" text-anchor="middle" {mono} font-size="9" fill="{muted}">the freeze writes nothing</text>')
    # the next start
    for i, (prov, eid, meaning) in enumerate(AFTER):
        x = 822 + i * 34; up = (44, 76, 108)[i]
        sel = eid == 41
        p.append(f'<path d="M{x} {AXIS-2}V{AXIS-up}" stroke="{glow}" stroke-width="5" opacity=".35" filter="url(#fg{uid})"/>')
        p.append(f'<path d="M{x} {AXIS-2}V{AXIS-up}" stroke="{stroke}" stroke-width="{2 if sel else 1.4}"/>')
        p.append(f'<text x="{x+6}" y="{AXIS-up+3}" {mono} font-size="10.5" font-weight="500" fill="{ink}">{prov} {eid}</text>')
        if sel:
            p.append(f'<circle cx="{x}" cy="{AXIS-up-7}" r="3.4" fill="{stroke if mode != "heat" else "#FFF1B8"}"/>')
            p.append(f'<text x="{x+6}" y="{AXIS-up+16}" {mono} font-size="9" fill="{muted}">the selected record</text>')
    p.append(f'<path d="M{822} {AXIS}H{822+2*34}" stroke="{muted}" stroke-width="1" opacity=".9"/>')
    p.append(f'<text x="822" y="{AXIS+22}" {mono} font-size="9.5" fill="{muted}">next start</text>')
    # brackets under the axis
    def bracket(xa, xb, y, text, anchor='middle'):
        ax = {'middle': (xa + xb) / 2, 'end': xb - 2, 'start': xa + 2}[anchor]
        return (f'<path d="M{xa:.1f} {y-5}v5H{xb:.1f}v-5" fill="none" stroke="{muted}" stroke-width="1"/>'
                f'<text x="{ax:.1f}" y="{y+13}" text-anchor="{anchor}" {mono} font-size="9" letter-spacing=".04em" fill="{muted}">{text}</text>')
    p.append(bracket(tx(-RECENT_BUCKETS), tx(0), AXIS + 56, f'RECENT WINDOW · {RECENT_BUCKETS} × {BUCKET_S} S · BURST AT {BURST}/MIN', 'end'))
    p.append(bracket(tx(BEFORE[0][0]), tx(BEFORE[-1][0]), AXIS + 86, 'EARLIER EVENTS, ATTACHED TO THE SELECTED RECORD BY THE COMPOSER'))
    p.append('</svg>')
    return ''.join(p)

def ledger_html():
    rows = []
    for t, prov, eid, meaning, n in BEFORE:
        tag = '<span class="tagx">in context</span>' if prov == 'WHEA-Logger' else ''
        cnt = f' <span class="cnt">×{n}</span>' if n > 1 else ''
        note = meaning if n < BURST else f'{meaning}; {n} in one bucket, burst, warning'
        rows.append(f'<tr><td>−{abs(t):02d}:00</td><td>{prov}</td><td>{eid}{cnt}</td><td>Warning</td><td>{note}{tag}</td></tr>')
    rows.append('<tr class="gap"><td colspan="5">no record · the freeze writes nothing</td></tr>')
    for prov, eid, meaning in AFTER:
        lvl = {12: 'Information', 41: 'Critical', 6008: 'Error'}[eid]
        tag = '<span class="tagx sel">selected record</span>' if eid == 41 else ''
        rows.append(f'<tr{" class=sel" if eid == 41 else ""}><td>next start</td><td>{prov}</td><td>{eid}</td><td>{lvl}</td><td>{meaning}{tag}</td></tr>')
    return ('<table class="ledger"><thead><tr><th>Minute</th><th>Provider</th><th>Event</th><th>Level</th><th>Windows\' own message, shortened</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>')

# ---------------------------------------------------------------- hero panels
HEADLINE = 'Bring the <em>evidence</em> together.'
LEDE = 'I built a workspace for understanding a Windows computer: inspect its diagnostics, select the evidence that matters, and compose a context package to take into an AI conversation.'
CHIPS = ['Diagnostics & AI context', 'Windows application', 'Context Composer']

def chrome():
    return '<div class="chrome"><b>Mainthread</b><div><span>Work</span><span>Experiments</span><span>About</span></div></div>'

def hero_a(uid, narrow=False):
    return (f'<div class="panel inst{" narrow" if narrow else ""}"><div class="grat"></div>{chrome()}'
            f'<div class="hero"><div class="chips">{"".join(f"<span>{c}</span>" for c in CHIPS)}</div>'
            f'<div class="lockup">{mark_trace(uid+"l", 44)}<span class="wm">System Sentinel</span></div>'
            f'<p class="h">{HEADLINE}</p><p class="lede2">{LEDE}</p>'
            f'<div class="cta"><span class="p">Inside the project</span><span class="s">How the composer works</span></div></div>{ribbon(uid, narrow=narrow)}'
            + ('' if narrow else f'<div class="fig"><div class="figlabel"><span>The record, read back · schematic</span><span>Providers and IDs are Windows\' own</span></div>{figure_svg(uid)}</div>')
            + '</div>')

def hero_b(uid, narrow=False):
    return (f'<div class="panel rec{" narrow" if narrow else ""}"><div class="ruled"></div>{chrome()}'
            f'<div class="hero"><div class="chips">{"".join(f"<span>{c}</span>" for c in CHIPS)}</div>'
            f'<div class="lockup">{mark_tag(uid+"l", 44)}<span class="wm">System Sentinel</span></div>'
            f'<p class="h">{HEADLINE}</p><p class="lede2">{LEDE}</p>'
            f'<div class="cta"><span class="p">Inside the project</span><span class="s">How the composer works</span></div></div>'
            + ('' if narrow else f'<div class="fig"><div class="figlabel"><span>The record, read back · schematic</span><span>Providers and IDs are Windows\' own</span></div>{ledger_html()}</div>')
            + '</div>')

def hero_c(uid, narrow=False):
    return (f'<div class="panel heat{" narrow" if narrow else ""}">{chrome()}'
            f'<div class="hero"><div class="chips">{"".join(f"<span>{c}</span>" for c in CHIPS)}</div>'
            f'<div class="lockup">{mark_heat(uid+"l", 44)}<span class="wm">System Sentinel</span></div>'
            f'<p class="h">{HEADLINE}</p><p class="lede2">{LEDE}</p>'
            f'<div class="cta"><span class="p">Inside the project</span><span class="s">How the composer works</span></div></div>{ribbon(uid, "heat", narrow=narrow)}'
            + ('' if narrow else f'<div class="fig"><div class="figlabel"><span>The record, read back · schematic</span><span>Providers and IDs are Windows\' own</span></div>{figure_svg(uid, "heat")}</div>')
            + '</div>')

def swatches(items):
    return '<div class="swatches">' + ''.join(f'<div class="sw"><div class="c" style="background:{c}"></div><div class="t"><b>{n}</b><span>{c if not note else note}</span></div></div>' for n, c, note in items) + '</div>'

def tiles_a(uid):
    return ('<div class="tiles">'
            f'<div class="tile"><div class="art inst-bg">{mark_trace(uid+"1", 96)}</div><div class="t"><b>On the field</b><span>the tile carries a faint graticule</span></div></div>'
            f'<div class="tile"><div class="art" style="background:#F5F6F7">{mark_trace(uid+"2", 96, tile="#0B1A16")}</div><div class="t"><b>On paper</b><span>same tile, any ground</span></div></div>'
            f'<div class="tile"><div class="art inst-bg">{mark_trace(uid+"3", 32, grat=False)}<span style="width:14px"></span>{mark_trace(uid+"4", 16, grat=False)}</div><div class="t"><b>Tab icon</b><span>32 and 16, graticule dropped</span></div></div>'
            f'<div class="tile"><div class="art inst-bg">{mark_trace(uid+"5", 112, radius=.2)}</div><div class="t"><b>App icon</b><span>for the application, later</span></div></div>'
            '</div>')

def tiles_b(uid):
    return ('<div class="tiles">'
            f'<div class="tile"><div class="art" style="background:#EDF0F2">{mark_tag(uid+"1", 96)}</div><div class="t"><b>On paper</b><span>the tag, red on the record</span></div></div>'
            f'<div class="tile"><div class="art" style="background:#151A1F">{mark_tag(uid+"2", 96, tile="#151A1F")}</div><div class="t"><b>On ink</b><span>same tag on a dark ground</span></div></div>'
            f'<div class="tile"><div class="art" style="background:#EDF0F2">{mark_tag(uid+"3", 32)}<span style="width:14px"></span>{mark_tag(uid+"4", 16)}</div><div class="t"><b>Tab icon</b><span>32 and 16</span></div></div>'
            f'<div class="tile"><div class="art" style="background:#EDF0F2">{mark_tag(uid+"5", 112, tile="#D42B2B", ink="#FFFFFF", letter="#D42B2B", radius=.2)}</div><div class="t"><b>App icon</b><span>inverted: white tag on red</span></div></div>'
            '</div>')

def tiles_c(uid):
    return ('<div class="tiles">'
            f'<div class="tile"><div class="art" style="background:#08070B">{mark_heat(uid+"1", 96)}</div><div class="t"><b>On the field</b><span>the S as a heat trace</span></div></div>'
            f'<div class="tile"><div class="art" style="background:#F5F6F7">{mark_heat(uid+"2", 96)}</div><div class="t"><b>On paper</b><span>the tile carries its own black</span></div></div>'
            f'<div class="tile"><div class="art" style="background:#08070B">{mark_heat(uid+"3", 32)}<span style="width:14px"></span>{mark_heat(uid+"4", 16)}</div><div class="t"><b>Tab icon</b><span>32 and 16</span></div></div>'
            f'<div class="tile"><div class="art" style="background:#08070B">{mark_heat(uid+"5", 112, radius=.2)}</div><div class="t"><b>App icon</b><span>for the application, later</span></div></div>'
            '</div>')

TODAY_HOME = img_data(HERE / 'today-home.png', 1100)
TODAY_PAGE = img_data(HERE / 'today-page.png', 1100)
TODAY_PHONE = img_data(HERE / 'today-phone.png', 480)

FONTS = ('https://fonts.googleapis.com/css2?family=Azeret+Mono:wght@200..500&family=Martian+Mono:wght@200..500'
         '&family=Newsreader:ital,opsz,wght@0,6..72,300..600;1,6..72,300..600&family=IBM+Plex+Mono:wght@400;500;600'
         '&family=DM+Sans:wght@400;500;600&family=JetBrains+Mono:wght@400;500&family=Literata:wght@400;500&display=swap')

CSS = r"""
:root { --core:#C9FFE1; --mid:#6FF0A8; --dp:#1E8F5E; }
:root[data-accent="amber"] { --core:#FFE4B0; --mid:#FFB454; --dp:#A8651A; }
:root { --paper:#F4F5F6; --ink:#131A1F; --muted:#5F6B74; --rule:#D3D9DE; --accent:#1E8F5E; --chip:#E6EAED; --code:#EBEEF0; color-scheme: light;
  --body:'DM Sans','Helvetica Neue',Arial,sans-serif; --serif:'Literata',Georgia,serif; --mono:'JetBrains Mono',Menlo,monospace;
  --azeret:'Azeret Mono',Menlo,monospace; --martian:'Martian Mono',Menlo,monospace; --news:'Newsreader',Georgia,serif; --plex:'IBM Plex Mono',Menlo,monospace; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --paper:#0F1416; --ink:#EEF2F3; --muted:#9AA7AE; --rule:#26313A; --accent:#6FF0A8; --chip:#161E22; --code:#161E22; color-scheme: dark; } }
:root[data-theme="dark"] { --paper:#0F1416; --ink:#EEF2F3; --muted:#9AA7AE; --rule:#26313A; --accent:#6FF0A8; --chip:#161E22; --code:#161E22; color-scheme: dark; }
* { box-sizing: border-box; }
body { margin:0; background:var(--paper); color:var(--ink); font-family:var(--body); font-size:16px; line-height:1.6; -webkit-font-smoothing:antialiased; }
.wrap { max-width:1160px; margin-inline:auto; padding-block:40px 80px; padding-inline:clamp(16px,4vw,40px); }
.eyebrow { font:500 11px/1.6 var(--mono); letter-spacing:.12em; text-transform:uppercase; color:var(--muted); max-width:none; }
h1 { font:400 clamp(36px,5.6vw,64px)/1.05 var(--serif); letter-spacing:-.04em; margin:12px 0 18px; text-wrap:balance; }
h2 { font:400 clamp(26px,3.2vw,36px)/1.15 var(--serif); letter-spacing:-.03em; margin:0 0 6px; text-wrap:balance; }
h3 { font:600 15px/1.3 var(--body); letter-spacing:-.01em; margin:0 0 8px; }
p { max-width:70ch; margin:0 0 14px; }
.lede { font-size:19px; line-height:1.55; letter-spacing:-.01em; max-width:62ch; }
a { color:var(--accent); text-underline-offset:.2em; }
a:focus-visible, button:focus-visible { outline:2px solid var(--accent); outline-offset:3px; }
code { font:400 .9em var(--mono); background:var(--code); padding:.1em .35em; border-radius:3px; }
.facts { display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:24px 40px; padding:22px 0; border-block:1px solid var(--rule); margin:28px 0 40px; }
.facts p { font-size:14.5px; margin:0 0 8px; }
.facts ul { margin:0; padding-left:18px; font-size:14.5px; } .facts li { margin-bottom:6px; }
.refs { display:grid; grid-template-columns:2fr 2fr 1fr; gap:16px; margin:0 0 20px; }
@media (max-width:820px) { .refs { grid-template-columns:1fr; } }
.refs figure { margin:0; }
.refs img { width:100%; height:auto; display:block; border-radius:4px; border:1px solid var(--rule); }
.refs figcaption { font:400 12.5px/1.5 var(--mono); color:var(--muted); margin-top:8px; }
section.dir { margin:0 0 72px; padding-top:32px; border-top:1px solid var(--rule); }
.dir-head { display:flex; flex-wrap:wrap; align-items:baseline; gap:8px 20px; margin-bottom:16px; }
.tag { font:600 11px/1 var(--mono); letter-spacing:.12em; text-transform:uppercase; padding:7px 10px; border-radius:999px; background:var(--chip); color:var(--ink); }
.tag.rec { background:#1E8F5E; color:#EAF3EE; }
.idea { font:400 clamp(20px,2.3vw,26px)/1.35 var(--serif); letter-spacing:-.02em; max-width:40ch; margin:0 0 22px; }
.grid { display:grid; grid-template-columns:repeat(12,1fr); gap:20px; }
.span-12 { grid-column:span 12; } .span-8 { grid-column:span 8; } .span-7 { grid-column:span 7; } .span-6 { grid-column:span 6; } .span-5 { grid-column:span 5; } .span-4 { grid-column:span 4; }
@media (max-width:820px) { .span-8,.span-7,.span-6,.span-5,.span-4 { grid-column:span 12; } }
.panel { position:relative; overflow:hidden; border-radius:4px; isolation:isolate; }
.cap { font:400 12.5px/1.55 var(--mono); color:var(--muted); margin-top:10px; max-width:80ch; }
.cap b { color:var(--ink); font-weight:500; }
.swatches { display:grid; grid-template-columns:repeat(auto-fill,minmax(140px,1fr)); gap:12px; }
.sw { border-radius:4px; overflow:hidden; border:1px solid var(--rule); font:400 12px/1.5 var(--mono); }
.sw .c { height:60px; } .sw .t { padding:8px 10px; background:var(--chip); } .sw .t b { display:block; font-weight:600; color:var(--ink); } .sw .t span { color:var(--muted); }
.type .row { display:grid; grid-template-columns:150px 1fr; gap:16px; align-items:baseline; padding:12px 0; border-top:1px solid var(--rule); }
.type .row:last-child { border-bottom:1px solid var(--rule); }
.type .lbl { font:500 11px/1.6 var(--mono); letter-spacing:.1em; text-transform:uppercase; color:var(--muted); }
.type .note { display:block; font:400 12.5px/1.5 var(--mono); color:var(--muted); margin-top:4px; }
@media (max-width:600px) { .type .row { grid-template-columns:1fr; gap:4px; } }
.why { display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:20px 32px; margin-top:8px; }
.why ul { margin:0; padding-left:18px; font-size:14.5px; } .why li { margin-bottom:6px; }
.prompt { margin-bottom:18px; }
.prompt textarea { display:block; width:100%; min-height:120px; resize:vertical; border:1px solid var(--rule); border-radius:4px; background:var(--code); color:var(--ink); padding:14px 16px; font:400 13.5px/1.6 var(--mono); }
.prompt .bar { display:flex; gap:12px; align-items:center; margin-top:8px; }
.prompt button, .switch button { font:500 13px/1 var(--body); padding:10px 14px; border:1px solid var(--ink); background:var(--ink); color:var(--paper); border-radius:3px; cursor:pointer; }
.switch { display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin:0 0 16px; font:400 12.5px/1.5 var(--mono); color:var(--muted); }
.switch button[aria-pressed="false"] { background:transparent; color:var(--ink); }
.prompt .status { font:400 12.5px/1.5 var(--mono); color:var(--muted); }
.next { padding:28px; border:1px solid var(--rule); border-radius:4px; background:var(--chip); }
.next ol { margin:0; padding-left:20px; } .next li { margin-bottom:8px; }
.tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:14px; }
.tile { border-radius:4px; overflow:hidden; border:1px solid var(--rule); }
.tile .art { height:150px; display:flex; align-items:center; justify-content:center; }
.tile .t { padding:8px 10px; background:var(--chip); font:400 11.5px/1.5 var(--mono); }
.tile .t b { display:block; font-weight:600; color:var(--ink); letter-spacing:.06em; text-transform:uppercase; font-size:10.5px; } .tile .t span { color:var(--muted); }
.side { display:grid; grid-template-columns:repeat(3,1fr); gap:14px; } @media (max-width:820px) { .side { grid-template-columns:1fr; } .side .panel { max-width:390px; margin-inline:auto; } }
.hl { display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:14px; }
.hl div { padding:14px 16px; border:1px solid var(--rule); border-radius:4px; }
.hl b { display:block; font:500 11px/1.6 var(--mono); letter-spacing:.1em; text-transform:uppercase; color:var(--muted); margin-bottom:6px; }
.hl p { margin:0; font-size:15px; }

/* ---------- shared hero structure ---------- */
.panel .chrome { position:relative; z-index:2; display:flex; justify-content:space-between; align-items:center; padding:20px 36px; font:400 13px/1 var(--body); }
.panel .chrome b { font:500 19px/1 var(--serif); letter-spacing:-.055em; }
.panel .chrome span { margin-left:22px; }
.panel .hero { position:relative; z-index:2; padding:38px 36px 20px; max-width:760px; }
.panel .chips { display:flex; flex-wrap:wrap; gap:8px; margin:0 0 22px; }
.panel .chips span { font:500 10.5px/1 var(--mono); letter-spacing:.12em; text-transform:uppercase; padding:8px 10px; border-radius:999px; border:1px solid; }
.panel .lockup { display:flex; align-items:center; gap:14px; margin:0 0 26px; }
.panel .h { margin:0 0 16px; text-wrap:balance; }
.panel .lede2 { font:400 16px/1.6 var(--body); max-width:56ch; margin:0 0 22px; }
.panel .cta { display:flex; gap:22px; align-items:center; font:500 14px/1 var(--body); margin-bottom:8px; }
.panel .cta .p { padding:12px 16px; border-radius:3px; }
.panel .fig { position:relative; z-index:2; margin:16px 36px 36px; padding:18px 20px 12px; border:1px solid; border-radius:4px; }
.panel .figlabel { display:flex; justify-content:space-between; flex-wrap:wrap; gap:6px 16px; font:500 10.5px/1.5 var(--mono); letter-spacing:.1em; text-transform:uppercase; margin-bottom:10px; }
.panel.narrow .chrome { padding:16px 20px; } .panel.narrow .chrome span { margin-left:14px; font-size:12px; }
.panel.narrow .hero { padding:26px 20px 24px; } .panel.narrow .h { font-size:34px !important; } .panel.narrow .lede2 { font-size:14.5px; }
.panel.narrow .cta { flex-wrap:wrap; gap:14px; }
.trace { position:relative; z-index:1; height:120px; margin-top:-24px; pointer-events:none; }
.trace svg { display:block; width:100%; height:100%; overflow:visible; }
.panel.narrow .trace { height:90px; margin-top:-10px; }

/* ---------- A. Instrument ---------- */
.inst { color:#EAF3EE; background:
  radial-gradient(70% 55% at 8% 0%, rgba(120,200,160,.22) 0%, rgba(120,200,160,.07) 34%, rgba(120,200,160,0) 68%),
  radial-gradient(60% 50% at 100% 100%, rgba(0,0,0,.5) 0%, rgba(0,0,0,0) 60%),
  linear-gradient(170deg, #163429 0%, #0B1A16 52%, #06100D 100%); }
.inst-bg { background:linear-gradient(160deg, #163429, #0B1A16 60%, #06100D); }
.inst .grat { position:absolute; z-index:1; top:0; right:0; width:62%; height:78%; pointer-events:none; opacity:.9;
  background:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='40' height='40' viewBox='0 0 40 40'%3E%3Cpath d='M0 .5H40M.5 0V40' stroke='%239CFFC4' stroke-opacity='.13' stroke-width='1'/%3E%3C/svg%3E") 0 0/40px 40px;
  -webkit-mask-image:radial-gradient(60% 60% at 72% 30%, #000 0%, rgba(0,0,0,.5) 45%, transparent 100%); mask-image:radial-gradient(60% 60% at 72% 30%, #000 0%, rgba(0,0,0,.5) 45%, transparent 100%); }
.inst .chrome { color:#B7CCC3; border-bottom:1px solid rgba(234,243,238,.12); } .inst .chrome b { color:#EAF3EE; }
.inst .chips span { color:#B7CCC3; border-color:rgba(234,243,238,.22); }
.inst .wm { font:400 17px/1 var(--azeret); letter-spacing:.2em; text-transform:uppercase; color:#EAF3EE; }
.inst .h { font:300 clamp(34px,4.6vw,64px)/1.08 var(--azeret); letter-spacing:-.03em; color:#EAF3EE; }
.inst .h em { font-style:normal; color:var(--core); text-shadow:0 0 18px color-mix(in srgb, var(--mid) 55%, transparent); }
.inst .lede2 { color:#C3D6CD; }
.inst .cta .p { background:#EAF3EE; color:#0B1A16; } .inst .cta .s { color:#EAF3EE; text-decoration:underline; text-decoration-color:var(--mid); text-underline-offset:.35em; }
.inst .fig { border-color:rgba(234,243,238,.14); background:rgba(6,16,13,.55); backdrop-filter:blur(2px); }
.inst .figlabel { color:#8FB0A3; }

/* ---------- B. Record ---------- */
.rec { color:#151A1F; background:
  linear-gradient(112deg, rgba(255,255,255,.9) 0%, rgba(255,255,255,.35) 30%, rgba(255,255,255,0) 58%, rgba(60,75,90,.14) 100%),
  linear-gradient(180deg, #F9FAFB 0%, #EDF0F2 60%, #E6EAEE 100%); }
.rec .ruled { position:absolute; inset:0; z-index:0; pointer-events:none; background:repeating-linear-gradient(180deg, transparent 0 27px, rgba(102,113,124,.16) 27px 28px);
  -webkit-mask-image:linear-gradient(180deg, transparent 0, #000 120px, #000 100%); mask-image:linear-gradient(180deg, transparent 0, #000 120px, #000 100%); }
.rec .chrome { color:#3E4A55; border-bottom:1px solid #C6CFD6; } .rec .chrome b { color:#151A1F; }
.rec .chips span { color:#3E4A55; border-color:#B9C3CB; background:rgba(255,255,255,.55); }
.rec .wm { font:500 15px/1 var(--plex); letter-spacing:.18em; text-transform:uppercase; color:#151A1F; }
.rec .h { font:400 clamp(38px,5.4vw,74px)/1.02 var(--news); font-variation-settings:'opsz' 72; letter-spacing:-.025em; color:#151A1F; }
.rec .h em { font-style:italic; color:#D42B2B; }
.rec .lede2 { color:#3E4A55; }
.rec .cta .p { background:#151A1F; color:#F9FAFB; } .rec .cta .s { color:#151A1F; text-decoration:underline; text-decoration-color:#D42B2B; text-underline-offset:.35em; }
.rec .fig { border-color:#C6CFD6; background:rgba(255,255,255,.72); overflow-x:auto; }
.rec .figlabel { color:#66717C; }
.ledger { width:100%; border-collapse:collapse; font:400 12.5px/1.5 var(--plex); color:#151A1F; }
.ledger th { font:500 10px/1.6 var(--plex); letter-spacing:.1em; text-transform:uppercase; color:#66717C; text-align:left; padding:6px 10px 8px 0; border-bottom:1px solid #C6CFD6; }
.ledger td { padding:7px 10px 7px 0; border-bottom:1px solid #DCE2E7; vertical-align:top; }
.ledger tr.gap td { padding:14px 0; text-align:center; color:#66717C; background:repeating-linear-gradient(45deg, transparent 0 6px, rgba(102,113,124,.12) 6px 7px); }
.ledger tr.sel td { font-weight:500; }
.ledger .cnt { color:#66717C; }
.ledger .tagx { display:inline-block; margin-left:10px; padding:2px 7px; border-radius:2px; font:600 9.5px/1.5 var(--plex); letter-spacing:.08em; text-transform:uppercase; background:#D42B2B; color:#fff; }
.ledger .tagx.sel { background:#151A1F; }
@media (max-width:700px) { .ledger th:nth-child(4), .ledger td:nth-child(4) { display:none; } }

/* ---------- C. Thermal ---------- */
.heat { color:#F3EFF7; background:
  radial-gradient(55% 45% at 85% 10%, rgba(91,15,140,.35) 0%, rgba(91,15,140,0) 70%),
  linear-gradient(170deg, #120B1E 0%, #08070B 50%, #040306 100%); }
.heat .chrome { color:#B9AFCB; border-bottom:1px solid rgba(243,239,247,.12); } .heat .chrome b { color:#F3EFF7; }
.heat .chips span { color:#B9AFCB; border-color:rgba(243,239,247,.22); }
.heat .wm { font:300 15px/1 var(--martian); letter-spacing:.16em; text-transform:uppercase; color:#F3EFF7; }
.heat .h { font:200 clamp(30px,4vw,54px)/1.12 var(--martian); letter-spacing:-.02em; color:#F3EFF7; }
.heat .h em { font-style:normal; color:#FFA630; }
.heat .lede2 { color:#CFC6DC; }
.heat .cta .p { background:#F3EFF7; color:#08070B; } .heat .cta .s { color:#F3EFF7; text-decoration:underline; text-decoration-color:#F0552A; text-underline-offset:.35em; }
.heat .fig { border-color:rgba(243,239,247,.14); background:rgba(4,3,6,.6); }
.heat .figlabel { color:#9B90B3; }
"""

def type_rows(rows):
    return '<div class="type">' + ''.join(f'<div class="row"><span class="lbl">{l}</span><div>{s}<span class="note">{n}</span></div></div>' for l, s, n in rows) + '</div>'

def prompt(pid, text):
    return f'<div class="prompt"><textarea id="{pid}" readonly>{text}</textarea><div class="bar"><button data-copy="{pid}">Copy</button><span class="status"></span></div></div>'

# ---------------------------------------------------------------- the board
html = f"""<title>System Sentinel Directions</title>
<link rel="stylesheet" href="{FONTS}">
<style>{CSS}</style>
<div class="wrap">
  <p class="eyebrow">Mainthread · System Sentinel · identity directions · 2026-09-20</p>
  <h1>Three directions for System Sentinel</h1>
  <p class="lede">A Windows computer freezes, you reboot, and the only witness is the log. System Sentinel gathers that record, lets you choose what matters, and carries it into the conversation. Each direction below answers one question differently: what is the page? An instrument you read, a document you hold, or a heat image you look into. One is recommended. The choice is yours.</p>

  <div class="facts">
    <div><h3>Decided, from the studio's records</h3><ul>
      <li>The name, and the public label <b>Windows application</b>.</li>
      <li>The copy: present tense, source-backed, arguing from capability. No live telemetry, no prediction, no diagnosed machine, no product release claimed.</li>
      <li>The four sources it reads: hardware, system events, driver history, crash records. The verbs: inspect, select, compose.</li>
      <li>The four story sections stay; the page keeps its date and its place after Zoning Signal.</li>
    </ul></div>
    <div><h3>Open, for you</h3><ul>
      <li>The character. This board.</li>
      <li>The headline. The current line, <b>Bring the evidence together.</b>, is plain and positive, so every panel carries it. Two alternatives below.</li>
      <li>Whether the figure may draw from your own machine's record. Default: a schematic that says it is one.</li>
      <li>The application's own palette is the framework's default (zinc and blue-600), so the identity is decided here and can flow back into it later, as Zoning Signal's does.</li>
    </ul></div>
    <div><h3>The subject's own vocabulary</h3><ul>
      <li>Windows writes <b>Kernel-Power 41</b> when it rebooted without shutting down, <b>EventLog 6008</b> for the unexpected shutdown, <b>WHEA-Logger 17, 18, 19</b> for hardware errors corrected or fatal.</li>
      <li>The product's storm detector: 60-second buckets, a burst at 5 per minute, critical above 10, or 2× the four-hour baseline.</li>
      <li>Its evidence classes: raw, derived, invariant, inferred. Its signal classes: suppressions, gaps, pressure, transitions, mismatches.</li>
      <li>Its composer attaches the events that came <em>before</em> a chosen record. That is the figure.</li>
    </ul></div>
  </div>

  <h2>Today</h2>
  <p>Honest and flat. The blue is the framework's default, the diagram is a list in a box, the shield is a stock icon. Nothing here is wrong; nothing here is System Sentinel's own.</p>
  <div class="refs">
    <figure><img src="{TODAY_HOME}" alt="The current homepage entry: white headline on graphite, a blue shield, the workflow list in a panel." width="1100" height="473"><figcaption>Homepage entry, 1440 wide, 2026-09-20</figcaption></figure>
    <figure><img src="{TODAY_PAGE}" alt="The current page top: serif title on the magazine's light ground, the dark diagram panel below." width="1100" height="687"><figcaption>The page, 1440 wide</figcaption></figure>
    <figure><img src="{TODAY_PHONE}" alt="The current page on a phone." width="480" height="1039"><figcaption>The page, 390 wide</figcaption></figure>
  </div>

  <!-- ================= A ================= -->
  <section class="dir" id="a">
    <div class="dir-head"><h2>A. Instrument</h2><span class="tag rec">Recommended</span><span class="tag">Deep green, phosphor, graticule, a mono readout</span></div>
    <p class="idea">The page is the instrument's screen after dark. A satin deep green field, a graticule in one zone, one phosphor trace as the light, and the record read back against it in a monospaced readout. Your saved wallpaper, made into a place of measurement.</p>
    <div class="switch"><span>Phosphor, or amber:</span><button data-accent="phos" aria-pressed="true">Phosphor (P1 green)</button><button data-accent="amber" aria-pressed="false">Amber (P3)</button><span id="acc-hex">#C9FFE1 · #6FF0A8 · #1E8F5E</span></div>
    <div class="grid">
      <div class="span-12">{hero_a('a')}
        <p class="cap"><b>The hero and the figure.</b> The trace is the mark, the ribbon and the figure at three scales: one idea. The figure reads the last forty minutes of a system log before a freeze, the gap the freeze leaves, and the three records Windows writes at the next start. Providers and IDs are Windows' own and the window and threshold are the detector's defaults; the minutes are illustrative and the caption on the page will say so. The glow is light only; text stays off-white.</p></div>
      <div class="span-7"><h3>Palette</h3>{swatches([('Field', '#0B1A16', ''), ('Lit', '#163429', ''), ('Deep', '#06100D', ''), ('Ink', '#EAF3EE', ''), ('Muted', '#8FB0A3', ''), ('Phosphor core', '#C9FFE1', 'light only'), ('Phosphor', '#6FF0A8', 'light only'), ('Phosphor deep', '#1E8F5E', 'links on paper')])}
        <p class="cap">Green is new on the site: indigo field, charcoal bench, plum plate and black athletic are the four dark surfaces so far, and this is a fifth in a different hue and a different kind. The graticule is an instrument's grid, not a cutting mat, so it does not repeat Multithread's plus-grid. Amber is one click away above; it is the classic readout phosphor and the app's own warning color.</p></div>
      <div class="span-5"><h3>Type</h3>{type_rows([('Display', '<span style="font:300 30px/1.1 var(--azeret); letter-spacing:-.03em">Bring the evidence together.</span>', 'Azeret Mono 300 for the headline and the wordmark. A readout, light and wide; a fourth voice on the site after a tight sans, a heavy grotesk and a serif. Open licence, self-hosted at build.'), ('Readout', '<span style="font:400 15px/1.4 var(--mono)">WHEA-Logger 19 · corrected machine check</span>', 'JetBrains Mono, the site\'s own label face, carries every number and ID.'), ('Text', '<span style="font:400 16px/1.5 var(--body)">Inspect its diagnostics, select the evidence that matters.</span>', 'DM Sans, unchanged.')])}</div>
      <div class="span-12"><h3>The mark</h3>{tiles_a('a')}
        <p class="cap"><b>The S is the trace.</b> One continuous line, the beam's dot where it ends, on a tile that carries the faintest graticule. Drawn from one path in code; the same path animates once on the page (the trace draws itself on load, static under reduced motion) and sits on the tab.</p></div>
      <div class="span-12"><div class="why">
        <div><h3>Why it fits</h3><ul><li>An instrument is what the product is: it reads a machine's record so a person can.</li><li>Material and light, as you asked for: satin field, one luminous trace, a graticule zone.</li><li>Distinct from every surface on the site in hue and in kind.</li></ul></div>
        <div><h3>What to watch</h3><ul><li>Green on dark drifts toward the terminal cliché. The satin field, the restraint of one trace and off-white text keep it an instrument.</li><li>A mono headline at scale must stay a name, not a label: light weight, tight tracking, one word lit.</li></ul></div>
      </div></div>
    </div>
  </section>

  <!-- ================= B ================= -->
  <section class="dir" id="b">
    <div class="dir-head"><h2>B. Record</h2><span class="tag">Cold paper, graphite, exhibit red, ruled lines</span></div>
    <p class="idea">The page is the printed record. Cool paper under a raking light, ruled like a log book, the events typeset in a monospaced face with the ones carried into context tagged in red. The only light entry among the dark ones; forensic, not dramatic.</p>
    <div class="grid">
      <div class="span-12">{hero_b('b')}
        <p class="cap"><b>The hero and the figure.</b> The same record as a ledger: every row checkable against Windows' own documentation, the gap a hatched line, the selected record and the events attached to it tagged. Typographic, like Zoning Signal's index, which you chose over any drawing.</p></div>
      <div class="span-7"><h3>Palette</h3>{swatches([('Paper', '#EDF0F2', ''), ('Paper, lit', '#F9FAFB', ''), ('Shade', '#DCE2E7', ''), ('Ink', '#151A1F', ''), ('Muted', '#66717C', ''), ('Rule', '#C6CFD6', ''), ('Exhibit red', '#D42B2B', 'tags and one word'), ('Red, deep', '#9B1B1B', 'hover, print')])}
        <p class="cap">Cool paper with a blue bias, chosen, not inherited. Red is spent only on the tags and the one lit word, so it stays a signal.</p></div>
      <div class="span-5"><h3>Type</h3>{type_rows([('Display', '<span style="font:400 32px/1.05 var(--news); font-variation-settings:\'opsz\' 72; letter-spacing:-.025em">Bring the <em style="color:#D42B2B">evidence</em> together.</span>', 'Newsreader at its display size: a report\'s headline, not the magazine\'s Literata, so the page differs from the baseline in voice as well as material.'), ('Record', '<span style="font:400 15px/1.4 var(--plex)">Kernel-Power 41 · rebooted without cleanly shutting down</span>', 'IBM Plex Mono for the ledger and the tags; the wordmark is set in it, tracked, like a stamped label.'), ('Text', '<span style="font:400 16px/1.5 var(--body)">Inspect its diagnostics, select the evidence that matters.</span>', 'DM Sans, unchanged.')])}</div>
      <div class="span-12"><h3>The mark</h3>{tiles_b('b')}
        <p class="cap"><b>An exhibit tag.</b> A label with a punched hole and the S set in it. It is what the composer does: tag a record and carry it.</p></div>
      <div class="span-12"><div class="why">
        <div><h3>Why it fits</h3><ul><li>The product's words are a case file's: forensic signals, evidence classes, provenance, chain of custody.</li><li>A light surface gives the homepage a rhythm between four dark entries.</li><li>The figure is text a reader can check line by line.</li></ul></div>
        <div><h3>What to watch</h3><ul><li>Paper reads flat unless the raking light and the ruling carry material. Lichtenberg's page is light already.</li><li>You chose the object under light over the drawing set for Bendr; this is the drawing-set instinct again, done as a document.</li></ul></div>
      </div></div>
    </div>
  </section>

  <!-- ================= C ================= -->
  <section class="dir" id="c">
    <div class="dir-head"><h2>C. Thermal</h2><span class="tag">Near-black, ironbow, a wide mono</span></div>
    <p class="idea">The page is a thermal image. Near-black, one ironbow ribbon of heat as the only light, and the record read as a heat strip where the events glow and the freeze is cold. Heat is the diagnostic dimension the other two don't show.</p>
    <div class="grid">
      <div class="span-12">{hero_c('c')}
        <p class="cap"><b>The hero and the figure.</b> The same figure with the events drawn hot against a cold strip. The ironbow ramp is a published instrument convention (black, violet, magenta, orange, yellow, white), which is the point: it is unmistakably diagnostic.</p></div>
      <div class="span-7"><h3>Palette</h3>{swatches([('Field', '#08070B', ''), ('Lit', '#1B0F2E', ''), ('Ink', '#F3EFF7', ''), ('Muted', '#9B90B3', ''), ('Ironbow 1', '#12003A', 'light only'), ('Ironbow 2', '#5B0F8C', 'light only'), ('Ironbow 3', '#C21E7A', 'light only'), ('Ironbow 4', '#F0552A', 'light only'), ('Ironbow 5', '#FFA630', 'one word'), ('Ironbow 6', '#FFF1B8', 'core')])}
        <p class="cap">The ramp is used as a gradient and never as flat fills, so it stays one object of light rather than a set of colors.</p></div>
      <div class="span-5"><h3>Type</h3>{type_rows([('Display', '<span style="font:200 26px/1.15 var(--martian); letter-spacing:-.02em">Bring the evidence together.</span>', 'Martian Mono 200, wide and thin, the register of a readout on a bezel. Open licence.'), ('Readout', '<span style="font:400 15px/1.4 var(--mono)">storahci 129 · reset to device issued</span>', 'JetBrains Mono, unchanged.'), ('Text', '<span style="font:400 16px/1.5 var(--body)">Inspect its diagnostics, select the evidence that matters.</span>', 'DM Sans, unchanged.')])}</div>
      <div class="span-12"><h3>The mark</h3>{tiles_c('c')}
        <p class="cap"><b>The S as heat.</b> The same trace path as A, stroked with the ramp from cold to white-hot.</p></div>
      <div class="span-12"><div class="why">
        <div><h3>Why it fits</h3><ul><li>The most atmospheric of the three; the ribbon is a true object of light.</li><li>No other page on the site or in most portfolios looks like this.</li></ul></div>
        <div><h3>What to watch</h3><ul><li>The most decorative. The ramp's violets and magentas sit near Zoning Signal's plum and rose gold.</li><li>Heat implies a thermal cause, which the page must not claim; the copy would have to hold the line the image blurs.</li></ul></div>
      </div></div>
    </div>
  </section>

  <!-- ================= side by side ================= -->
  <section class="dir" id="side">
    <h2>Side by side</h2>
    <p>The same words on each, at about a phone's width, the way they would meet you on your phone.</p>
    <div class="side">{hero_a('sa', narrow=True)}{hero_b('sb', narrow=True)}{hero_c('sc', narrow=True)}</div>
    <h3 style="margin-top:28px">The headline</h3>
    <div class="hl">
      <div><b>Current, plain</b><p>Bring the evidence together.</p></div>
      <div><b>The product's verbs</b><p>Inspect, select, compose.</p></div>
      <div><b>The figure's line</b><p>The record, read back.</p></div>
    </div>
    <p class="cap">You chose the plain line for Zoning Signal over anything catchier; the current line is that here, and it is what the panels carry. The other two are offered, not recommended.</p>
  </section>

  <!-- ================= prompts ================= -->
  <section class="dir" id="prompts">
    <h2>Prompts for ChatGPT</h2>
    <p>Everything on this board is drawn in code and the page will be too. These are for raster references: a backdrop to study the light against, a texture to sample, the icon as a render. Each carries the hex values so the output lands in the palette. For amber, swap <code>#C9FFE1</code> and <code>#6FF0A8</code> for <code>#FFE4B0</code> and <code>#FFB454</code>.</p>
    <h3>A. Instrument</h3>
    {prompt('p1', 'Site backdrop, 2560 x 1200, no text. A matte deep green surface, color #0B1A16, filling the frame, lit by one cool pale-green light from the upper left so the surface is brightest (#163429) at the top left and falls off smoothly to a very deep green-black (#06100D) at the lower right, with a satin sheen like brushed dark lacquer. No objects, no text, no logo, no grid, no lines, no lens flare, no grain, no bokeh. Just the lit green surface.')}
    {prompt('p2', 'Texture reference, 2000 x 1200, no text. An oscilloscope screen graticule: a fine hairline square grid in pale mint green (#9CFFC4) at low opacity on a matte deep green ground (#0B1A16), ten divisions across, with a central horizontal and vertical axis carrying small minor tick marks, five per division. Flat, precise, evenly weighted, no trace, no glow, no text, no numbers, no bezel, no perspective, no photographic realism.')}
    {prompt('p3', 'Material reference, 2000 x 800, no text. A single thin luminous phosphor-green line running horizontally across a matte deep green (#0B1A16) field, with one gentle rounded pulse in the middle, the line a bright core (#C9FFE1) inside a soft halo (#6FF0A8), like a CRT oscilloscope trace photographed at night. Deep black-green everywhere else, no grid, no text, no bezel, no lens flare, no second trace.')}
    {prompt('p4', 'Share card ground, 1200 x 630, no text. A matte deep green surface (#0B1A16) lit by one cool pale-green light from the upper left, falling to deep green-black (#06100D) at the lower right. Across the right third, a very faint hairline square grid in pale mint (#9CFFC4) at low opacity, visible only where the light reaches. Leave the left two thirds empty for text. No text, no logo, no trace, no glow, no flare, no grain, no 3D scene.')}
    {prompt('p5', 'App icon exploration, 1024 x 1024, no text other than the letter. A matte deep green (#0B1A16) rounded square carrying a very faint hairline grid, and on it a single capital letter S drawn as one continuous thin oscilloscope trace in phosphor green, a bright core (#C9FFE1) inside a soft glow (#6FF0A8), ending at its lower-left tip in a small bright round dot like the beam at rest. Flat and precise, no gloss, no bevel, no drop shadow, no other text, no bezel.')}
    <h3>B. Record</h3>
    {prompt('p6', 'Site backdrop, 2560 x 1200, no text. A sheet of cool white paper (#EDF0F2) with a faint blue-grey cast, ruled with fine horizontal lines (#C6CFD6) at even spacing like a log book, lit by one soft raking light from the upper left so the paper is brightest (#F9FAFB) at the top left and shades gently (#DCE2E7) toward the lower right, with the subtlest paper grain. No text, no writing, no holes, no binding, no objects, no shadows of objects, no torn edges.')}
    <h3>C. Thermal</h3>
    {prompt('p7', 'Site backdrop, 2560 x 1200, no text. A near-black field (#08070B) with one soft band of heat running from the lower left to the upper right, shading through an ironbow thermal palette: deep violet (#12003A) at its cold edges, then purple (#5B0F8C), magenta (#C21E7A), orange (#F0552A), amber (#FFA630) and a thin pale yellow-white core (#FFF1B8). Smooth, out of focus, like a thermal camera image of a single warm line on a cold surface. No objects, no text, no grid, no lens flare, no second band.')}
  </section>

  <!-- ================= next ================= -->
  <section class="dir" id="next">
    <h2>What happens after a direction is named</h2>
    <div class="next"><ol>
      <li>Record the identity in the product repository's design docs (<code>system-sentinel/docs/design/</code>), which will own the values as Bendr's and Zoning Signal's repositories do. The studio's evidence note keeps the claim bindings.</li>
      <li>Build the page and the homepage entry as a surface end to end, as the other three were: page tokens through <code>data-surface</code>, the display face self-hosted, the mark as a component, the figure computed from the vocabulary and the detector's constants, the share card and the tab icon rendered from the same code, one motion (the trace draws once on load; static under reduced motion), verified at 390 and 1440, deployed, byte-checked, recorded in RELEASE and DECISIONS.</li>
      <li>The figure's data. Default: the schematic above, captioned as one. If you authorize your own machine's record for it (the dates and the event IDs from your March to May sessions, nothing else: no serial numbers, paths or message text), the figure becomes real and to scale, and the page says whose record it is. That is your call, not mine.</li>
      <li>The application follows later. Its palette is the framework's default, so when you return to finish it the identity flows back in: tokens, the mark, the display face.</li>
    </ol></div>
  </section>
</div>
<script>
(function () {{
  const root = document.documentElement;
  const hex = {{ phos: '#C9FFE1 · #6FF0A8 · #1E8F5E', amber: '#FFE4B0 · #FFB454 · #A8651A' }};
  function setAccent(name) {{
    if (name === 'amber') root.dataset.accent = 'amber'; else {{ delete root.dataset.accent; name = 'phos'; }}
    document.getElementById('acc-hex').textContent = hex[name];
    for (const b of document.querySelectorAll('[data-accent]')) b.setAttribute('aria-pressed', String(b.dataset.accent === name));
    try {{ localStorage.setItem('ss-accent', name); }} catch (e) {{}}
  }}
  for (const b of document.querySelectorAll('button[data-accent]')) b.addEventListener('click', () => setAccent(b.dataset.accent));
  try {{ const saved = localStorage.getItem('ss-accent'); if (saved === 'amber') setAccent('amber'); }} catch (e) {{}}
  for (const b of document.querySelectorAll('button[data-copy]')) b.addEventListener('click', async () => {{
    const ta = document.getElementById(b.dataset.copy); const status = b.parentElement.querySelector('.status');
    try {{ await navigator.clipboard.writeText(ta.value); status.textContent = 'Copied.'; }}
    catch (e) {{ ta.focus(); ta.select(); status.textContent = 'Selected; use Copy.'; }}
  }});
}})();
</script>
"""
OUT.write_text(html)
print(OUT, len(html) // 1024, 'KB')
