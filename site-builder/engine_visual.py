"""Four views of the instrument, rendered from the copy as HTML and a portable SVG.

The HTML uses ordinary flow layout. The SVG uses generously wrapped monospace text
and content-sized rows; it needs no foreignObject, script, font or remote resource.
"""
from html import escape as esc
import json
from pathlib import Path
import textwrap

HERE = Path(__file__).resolve().parent


def render_engine(copy, data=None, fill=None, site=None, *, embedded=False):
    e = copy['engine']
    out = ['<div class="engine-visual">']
    if embedded:
        out.append(f'<p class="engine-launch"><a href="engine.html">{esc(e["open_label"])}</a> · <a href="images/engine.svg">{esc(e["image_label"])}</a></p>')
    out.append(f'<nav class="engine-tabs" aria-label="{esc(e["nav_label"])}">')
    for i, label in enumerate(e['panels'], 1):
        out.append(f'<a href="#engine-{i}"><span aria-hidden="true">0{i}</span> {esc(label)}</a>')
    out.append('</nav>')

    def panel(i):
        out.append(f'<section class="engine-panel" id="engine-{i}" aria-labelledby="engine-title-{i}">')
        out.append(f'<p class="engine-label">0{i} / 04</p><h2 id="engine-title-{i}">{esc(e["panels"][i-1])}</h2>')

    panel(1)
    out.append(f'<p class="engine-thesis">{esc(e["thesis"])}</p><p class="engine-intro">{esc(e["intro"])}</p>')
    out.append('<ol class="engine-path">')
    for i, step in enumerate(e['steps'], 1):
        pending = ' engine-pending' if step.get('planned') else ''
        out.append(f'<li class="engine-card{pending}"><span class="engine-number" aria-hidden="true">{i:02d}</span><h3>{esc(step["title"])}</h3><p>{esc(step["short"])}</p>')
        if step.get('planned'):
            out.append(f'<p class="engine-state">{esc(e["next_label"])}</p>')
        out.append('</li>')
    out.append(f'</ol><p class="engine-path-note">{esc(e["path_note"])}</p>')
    out.append(f'<div class="engine-ledger"><h3>{esc(e["ledger_title"])}</h3><p>{esc(e["ledger_text"])}</p></div>')
    out.append(f'<p class="engine-label engine-snapshot">{esc(e["metrics_label"])}</p><ul class="engine-metrics">')
    out.extend(f'<li>{esc(v)}</li>' for v in e['metrics'])
    out.append(f'</ul><details class="engine-details"><summary>{esc(e["detail_label"])}</summary><ol>')
    for s in e['steps']:
        out.append(f'<li><h3>{esc(s["title"])}</h3><p>{esc(s["detail"])}</p></li>')
    out.append('</ol></details></section>')

    panel(2)
    out.append(f'<div class="engine-inventory"><section class="engine-built"><h3>{esc(e["built_label"])}</h3><dl>')
    for item in e['built']:
        out.append(f'<div><dt>{esc(item["title"])}</dt><dd>{esc(item["text"])}</dd></div>')
    out.append(f'</dl></section><section class="engine-next"><h3>{esc(e["next_label"])}</h3><ul>')
    out.extend(f'<li>{esc(s)}</li>' for s in e['next'])
    out.append('</ul></section></div></section>')

    panel(3)
    out.append(f'<p class="engine-thesis">{esc(e["safety_intro"])}</p><div class="engine-safety">')
    for item in e['safety']:
        out.append(f'<article><h3>{esc(item["title"])}</h3><p>{esc(item["text"])}</p><a href="{esc(item["link"])}">{esc(item["link_label"])}</a></article>')
    out.append(f'</div><p class="engine-caution">{esc(e["safety_note"])}</p></section>')

    panel(4)
    out.append(f'<p class="engine-state">{esc(e["planned_label"])}</p><p class="engine-intro">{esc(e["theory_intro"])}</p>')
    for group in ('theory_inputs', 'theory_branches'):
        out.append(f'<div class="engine-{group.replace("_", "-")}">')
        for item in e[group]:
            out.append(f'<article><h3>{esc(item["title"])}</h3><p>{esc(item["text"])}</p></article>')
        out.append('</div><div class="engine-arrow" aria-hidden="true">↓</div>')
    out.append(f'<div class="engine-result"><h3>{esc(e["theory_result_title"])}</h3><p>{esc(e["theory_result"])}</p></div>')
    out.append(f'<p class="engine-caution">{esc(e["theory_note"])}</p><p><a href="reading.html#theory-overview">{esc(e["theory_link_label"])}</a></p></section>')
    out.append(f'<nav class="engine-links"><a href="method.html">{esc(e["method_label"])}</a><a href="images/engine.svg">{esc(e["image_label"])}</a></nav></div>')
    return ''.join(out)


class Drawing:
    """Text remains text. Line widths allow more than one mono character of spare room."""
    width = 1800
    def __init__(self):
        self.parts = []

    def line(self, x1, y1, x2, y2, *, red=False, dashed=False):
        dash = ' stroke-dasharray="9 7"' if dashed else ''
        self.parts.append(f'<path d="M{x1} {y1}H{x2}" class="{"red-line" if red else "rule"}"{dash}/>' if y1 == y2 else f'<path d="M{x1} {y1}L{x2} {y2}" class="{"red-line" if red else "rule"}"{dash}/>')

    def text(self, value, x, y, width, size=24, *, klass='body'):
        # Courier's normal glyph advance is .6em; reserve .67em plus 2 chars.
        lines = textwrap.wrap(value, width=max(8, int(width / (size * .67)) - 2), break_long_words=False, break_on_hyphens=False) or ['']
        leading = round(size * 1.4)
        self.parts.append(f'<text x="{x}" y="{y+size}" class="{klass}" font-size="{size}">')
        for i, line in enumerate(lines):
            self.parts.append(f'<tspan x="{x}" dy="{0 if i == 0 else leading}">{esc(line)}</tspan>')
        self.parts.append('</text>')
        return y + len(lines) * leading

    def heading(self, number, title, y):
        self.line(64, y, 1736, y, red=True)
        y = self.text(f'{number:02d} / 04', 64, y+22, 190, 22, klass='red')
        return self.text(title, 64, y+8, 1672, 38, klass='heading') + 26

    def cards(self, cards, y, columns, *, numbered=False, size=23):
        gap = 30
        width = (1672 - gap * (columns - 1)) / columns
        for start in range(0, len(cards), columns):
            bottom = y
            for i, card in enumerate(cards[start:start+columns]):
                x = round(64 + i * (width + gap))
                top = y
                self.line(x, top, round(x+width), top, dashed=card.get('planned', False))
                if numbered:
                    top = self.text(f'{start+i+1:02d}', x, top+14, width, 24, klass='red')+5
                else:
                    top += 15
                top = self.text(card['title'], x, top, width, 27, klass='card-title')+8
                top = self.text(card.get('short', card.get('text', '')), x, top, width, size)
                if card.get('planned'):
                    top = self.text(self.planned, x, top+10, width, 19, klass='red')
                bottom = max(bottom, top)
            y = bottom + 28
        return y


def render_svg(copy):
    e = copy['engine']
    # The repository poster has shorter captions; the HTML retains the full path.
    # Titles and ordering are shared, and all captions still live in site.json.
    poster = e['poster']
    def cards(group):
        if len(e[group]) != len(poster[group]):
            raise ValueError(f'engine poster captions do not match {group}')
        return [{**item, 'short': caption} for item, caption in zip(e[group], poster[group])]
    d = Drawing()
    d.planned = e['next_label']
    y = d.text(e['heading'], 64, 40, 1672, 48, klass='heading')
    y = d.text(e['thesis'], 64, y+10, 1672, 32, klass='heading')+32
    y = d.heading(1, e['panels'][0], y)
    y = d.cards(cards('steps'), y, 5, numbered=True, size=22)
    y = d.text(e['path_note'], 64, y, 1672, 21)+20
    d.line(64, y, 1736, y, red=True)
    y = d.text(e['ledger_title'], 64, y+16, 1672, 28, klass='card-title')
    y = d.text(e['ledger_text'], 64, y+8, 1672, 23)+14
    y = d.text(' · '.join(e['metrics']), 64, y, 1672, 23, klass='red')
    y = d.text(e['metrics_label'], 64, y+8, 1672, 19)+38

    y = d.heading(2, e['panels'][1], y)
    left = d.text(e['built_label'], 64, y, 786, 30, klass='card-title')+14
    right = d.text(e['next_label'], 950, y, 786, 30, klass='red')+14
    for item in cards('built'):
        left = d.text(item['title'], 64, left, 786, 24, klass='card-title')+3
        left = d.text(item['short'], 64, left, 786, 22)+17
    for item in poster['next']:
        right = d.text('— '+item, 950, right, 786, 22)+15
    d.line(899, y, 899, max(left, right), dashed=True)
    y = max(left, right)+28

    y = d.heading(3, e['panels'][2], y)
    y = d.text(e['safety_intro'], 64, y, 1672, 27, klass='heading')+24
    y = d.cards(cards('safety'), y, 3, size=23)
    y = d.text(e['safety_note'], 64, y, 1672, 22, klass='red')+40

    y = d.heading(4, e['panels'][3], y)
    y = d.text(e['planned_label'], 64, y, 1672, 23, klass='red')+20
    y = d.cards(cards('theory_inputs'), y, 2)
    y = d.text('↓', 870, y-12, 100, 32, klass='red')+8
    y = d.cards(cards('theory_branches'), y, 3)
    y = d.text('↓', 870, y-12, 100, 32, klass='red')+8
    y = d.text(e['theory_result_title'], 64, y, 1672, 30, klass='heading')+8
    y = d.text(e['theory_result'], 64, y, 1672, 23)+16
    y = d.text(e['theory_note'], 64, y, 1672, 21)+30
    d.line(64, y, 1736, y, red=True)
    y = d.text(e['svg_footer'], 64, y+18, 1672, 26, klass='heading')+45
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="1800" height="{y}" viewBox="0 0 1800 {y}" role="img" aria-labelledby="title description">\n'
            f'<title id="title">{esc(e["svg_title"])}</title><desc id="description">{esc(e["svg_description"])}</desc>\n'
            '<style>svg{background:#f4f0e7}text{fill:#25231f;font-family:"Courier New",Courier,monospace}.heading{font-family:Georgia,"Times New Roman",serif}.card-title{font-weight:bold}.red{fill:#b52d25}.rule{stroke:#d7d0c3;stroke-width:2}.red-line{stroke:#b52d25;stroke-width:2}</style>\n'
            f'<path fill="#f4f0e7" d="M0 0H1800V{y}H0Z"/>\n'+''.join(d.parts)+'\n</svg>\n')


if __name__ == '__main__':
    copy = json.loads((HERE/'site.json').read_text())
    path = HERE/'images/engine.svg'
    path.write_text(render_svg(copy), encoding='utf-8')
    print(f'Wrote {path}')
