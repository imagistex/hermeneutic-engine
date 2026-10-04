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
    out = ['<div class="engine-visual">', f'<p class="engine-intro">{esc(e["definition"])}</p>']
    out.append(f'<nav class="engine-tabs" aria-label="{esc(e["nav_label"])}">')
    out.extend(f'<a href="#{part["id"]}">{esc(part["label"])}</a>' for part in e['parts'])
    out.append('</nav>')

    def part(index, legacy):
        value = e['parts'][index]
        out.append(f'<section class="engine-panel" id="{value["id"]}" aria-labelledby="part-title-{value["id"]}">')
        out.append(f'<span id="engine-{legacy}"></span><span id="engine-title-{legacy}"></span>')
        out.append(f'<h2 id="part-title-{value["id"]}">{esc(value["label"])}</h2><p class="engine-intro">{esc(value["intro"])}</p>')

    def cards(indices):
        out.append('<ul class="engine-path">')
        for index in indices:
            step = e['steps'][index]
            out.append(f'<li class="engine-card"><h3>{esc(step["title"])}</h3><p>{esc(step["short"])}</p></li>')
        out.append('</ul>')

    part(0, 4)
    out.append(f'<h3>{esc(e["panels"][3])}</h3><p>{esc(e["theory_intro"])}</p><ol class="engine-points engine-theory-steps">')
    out.extend(f'<li>{esc(v)}</li>' for v in e['theory_steps'])
    out.append(f'</ol><p><a href="reading.html">{esc(e["theory_link_label"])}</a></p></section>')

    part(1, 1)
    cards(e['kernel_steps'])
    out.append(f'<div class="engine-ledger"><h3>{esc(e["ledger_title"])}</h3><p>{esc(e["ledger_text"])}</p></div></section>')

    part(2, 2)
    cards(e['module_steps'])
    out.append(f'<div class="engine-inventory"><section class="engine-built"><h3>{esc(e["built_label"])}</h3><dl>')
    for item in e['built'][1:]:
        out.append(f'<div><dt>{esc(item["title"])}</dt><dd>{esc(item["text"])}</dd></div>')
    out.append(f'</dl></section><section class="engine-next"><h3>{esc(e["next_label"])}</h3><ul>')
    out.extend(f'<li>{esc(v)}</li>' for v in e['next'])
    out.append('</ul></section></div>')
    out.append(f'<details class="engine-details"><summary>{esc(e["detail_label"])}</summary><ol>')
    for step in e['steps']:
        out.append(f'<li><h3>{esc(step["title"])}</h3><p>{esc(step["detail"])}</p></li>')
    out.append(f'</ol><p class="engine-label engine-snapshot">{esc(e["metrics_label"])}</p><ul class="engine-metrics">')
    out.extend(f'<li>{esc(v)}</li>' for v in e['metrics'])
    out.append('</ul></details>')
    out.append(f'<section id="engine-3"><h3 id="engine-title-3">{esc(e["panels"][2])}</h3><p>{esc(e["safety_intro"])}</p><ul class="engine-points">')
    out.extend(f'<li><a href="{esc(item["link"])}">{esc(item["text"])}</a></li>' for item in e['safety'])
    out.append('</ul></section></section>')
    out.append(f'<p class="engine-links"><a href="images/engine.svg">{esc(e["image_label"])}</a></p></div>')
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
    d = Drawing()
    y = d.text(e['heading'], 64, 40, 1672, 48, klass='heading') + 25
    y = d.text(e['definition'], 64, y, 1672, 27) + 35
    p = e['parts'][0]
    y = d.heading(1, p['label'], y)
    y = d.text(p['intro'], 64, y, 1672, 27) + 20
    y = d.text(e['panels'][3], 64, y, 1672, 30, klass='heading') + 15
    for i, item in enumerate(e['theory_steps'], 1):
        y = d.text(f'{i:02d}  '+item, 64, y, 1672, 27) + 12
    y += 25

    p = e['parts'][1]
    y = d.heading(2, p['label'], y)
    y = d.text(p['intro'], 64, y, 1672, 27) + 28
    y = d.cards([e['steps'][i] for i in e['kernel_steps']], y, 4, size=22)
    d.line(64, y, 1736, y, red=True)
    y = d.text(e['ledger_title'], 64, y+16, 1672, 28, klass='card-title')
    y = d.text(e['ledger_text'], 64, y+8, 1672, 23) + 38

    p = e['parts'][2]
    y = d.heading(3, p['label'], y)
    y = d.text(p['intro'], 64, y, 1672, 27) + 28
    y = d.cards([e['steps'][i] for i in e['module_steps']], y, 3, size=22)
    left = d.text(e['built_label'], 64, y, 786, 30, klass='card-title') + 14
    right = d.text(e['next_label'], 950, y, 786, 30, klass='red') + 14
    for item in e['built'][1:]:
        left = d.text(item['title'], 64, left, 786, 24, klass='card-title') + 3
        left = d.text(item['text'], 64, left, 786, 22) + 17
    for item in e['next']:
        right = d.text('— '+item, 950, right, 786, 22) + 15
    d.line(899, y, 899, max(left, right), dashed=True)
    y = max(left, right) + 28
    y = d.text(e['panels'][2], 64, y, 1672, 30, klass='heading') + 15
    y = d.text(e['safety_intro'], 64, y, 1672, 27) + 20
    for item in e['safety']:
        y = d.text('— '+item['text'], 64, y, 1672, 27) + 12
    y += 25
    d.line(64, y, 1736, y, red=True)
    y = d.text(e['svg_footer'], 64, y+18, 1672, 26, klass='heading') + 45
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="1800" height="{y}" viewBox="0 0 1800 {y}" role="img" aria-labelledby="title description">\n'
            f'<title id="title">{esc(e["svg_title"])}</title><desc id="description">{esc(e["svg_description"])}</desc>\n'
            '<style>svg{background:#f4f0e7}text{fill:#25231f;font-family:"Courier New",Courier,monospace}.heading{font-family:Georgia,"Times New Roman",serif}.card-title{font-weight:bold}.red{fill:#b52d25}.rule{stroke:#d7d0c3;stroke-width:2}.red-line{stroke:#b52d25;stroke-width:2}</style>\n'
            f'<path fill="#f4f0e7" d="M0 0H1800V{y}H0Z"/>\n'+''.join(d.parts)+'\n</svg>\n')


if __name__ == '__main__':
    copy = json.loads((HERE/'site.json').read_text())
    path = HERE/'images/engine.svg'
    path.write_text(render_svg(copy), encoding='utf-8')
    print(f'Wrote {path}')
