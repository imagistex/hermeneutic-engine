"""Saved theory readings and comparisons. Read-only, with no reader calls."""
from collections import defaultdict
import json
from pathlib import Path

from notebook_sections import apparatus, esc, rid, thread_file


def field_id(memo, field):
    return f'theory:{memo}:{field}'


def select_theory(project, data, copy, gloss_path=None):
    labels = copy['reading']['ledger']
    lenses = {r['id']: r for r in project.records('lens')}
    activities = {r['id']: r for r in project.records('activity')}
    memos = project.records('memo')
    theories = {key: {'readings': [], 'attempts': []} for key in labels['frameworks']}
    for activity in activities.values():
        lens = lenses.get(activity.get('lens'), {})
        method = lens.get('method', {})
        framework = method.get('framework')
        if (activity.get('type') != 'theory' or method.get('name') != 'theory-lens'
                or method.get('version') != '1' or framework not in theories):
            continue
        reader = {key: lens.get('reader', {}).get(key) for key in ('family', 'model')}
        chosen = [m for m in memos if m.get('memo_type') == 'theory_reading'
                  and m.get('activity') == activity['id'] and m.get('by') == lens['id']
                  and m.get('framework') == framework] if activity.get('status') == 'ok' else []
        theories[framework]['attempts'].append({
            'activity': activity['id'], 'status': activity.get('status'),
            'reader': reader, 'published': len(chosen)})
        for memo in chosen:
            # Only the intended public memo fields and public attribution leave the ledger.
            record = {key: memo[key] for key in ('id', 'framework', 'plain', 'body', 'evidence',
                      'against', 'rival', 'signed', 'quotes_not_found') if key in memo}
            theories[framework]['readings'].append({
                'record': record, 'reader': reader, 'apparatus': apparatus(project, lens)})

    gloss_path = Path(gloss_path) if gloss_path is not None else Path(__file__).with_name('theory-glosses.json')
    glosses = json.loads(gloss_path.read_text(encoding='utf-8')) if gloss_path.exists() else {}
    if not isinstance(glosses, dict):
        raise ValueError('theory-glosses.json must map memo IDs to notes')
    locations = dict(data.get('board_locations', {}))
    for thread, units in data.get('board_threads', {}).items():
        for n, unit in enumerate(units, 1):
            locations[unit['id']] = f'{thread_file(thread)}#u{n:02d}'
    order = {family: n for n, family in enumerate(labels['families'])}
    for group in theories.values():
        group['readings'].sort(key=lambda item: (order.get(item['reader']['family'], len(order)), item['record']['id']))
    readings = [r for group in theories.values() for r in group['readings']]
    for item in readings:
        locations[item['record']['id']] = 'reading.html#' + rid(item['record']['id'])
    sources = {}
    for item in readings:
        memo = item['record']
        if memo['id'] in glosses:
            item['gloss'] = glosses[memo['id']]
        for evidence in memo.get('evidence', []):
            ref = evidence.get('id')
            if 'not_found' not in evidence and not evidence.get('quote'):
                raise ValueError(f'Theory evidence has an empty unflagged quotation: {memo["id"]}')
            record = project.get(ref) if ref else None
            if not record or record.get('kind') not in ('unit', 'memo'):
                if 'not_found' not in evidence:
                    raise ValueError(f'Theory evidence names no source: {ref}')
                continue
            if ref not in locations:
                body = project.unit_text(record) if record['kind'] == 'unit' else record.get('body', '')
                sources[ref] = {'id': ref, 'body': body, 'signed': record.get('signed'),
                                'context': record.get('context', {})}
                locations[ref] = 'theory-sources.html#' + rid(ref)
    data['theory'] = theories
    data['theory_sources'] = sources
    data['theory_locations'] = locations
    return data


def quote_sources(project, data):
    """Audit fields against the actual saved memo, including comparison annotations."""
    sources = {}
    for group in data.get('theory', {}).values():
        for item in group['readings']:
            memo = project.get(item['record']['id'])
            for field in ('plain', 'rival'):
                sources[field_id(memo['id'], field)] = memo.get(field, '')
            for n, sentence in enumerate(memo.get('against', [])):
                sources[field_id(memo['id'], f'against:{n}')] = sentence
            for n, evidence in enumerate(memo.get('evidence', [])):
                sources[field_id(memo['id'], f'evidence:{n}:note')] = evidence.get('note', '')
            for part in item['apparatus']['stack']:
                sources['prompt:' + part['sha256']] = part['text']
    return sources


def exact(value, source, tag='p', klass=''):
    return f'<{tag} class="exact {klass}" data-quote="{esc(source)}">{esc(value)}</{tag}>' if value else ''


def attribution(item, labels):
    reader = item['reader']
    return ' / '.join(str(reader.get(key) or labels['unknown']) for key in ('family', 'model'))


def evidence_html(evidence, n, memo, data, labels):
    ref = evidence.get('id', '')
    failed = 'not_found' in evidence
    out = '<div class="theory-evidence' + (' quote-failure' if failed else '') + '">'
    if failed:
        out += f'<p class="eyebrow">{esc(labels["failed_quote"])}</p>'
        out += f'<blockquote class="exact">{esc(evidence.get("quote", ""))}</blockquote>'
        out += f'<p class="small">{esc(evidence["not_found"])}</p>'
    else:
        out += exact(evidence.get('quote', ''), ref, 'blockquote')
    bears = evidence.get('bears')
    if bears:
        out += f'<p class="eyebrow">{esc(labels["bears"].get(bears, bears))}</p>'
    out += exact(evidence.get('note', ''), field_id(memo['id'], f'evidence:{n}:note'))
    if ref in data['theory_locations']:
        out += f'<p class="small"><a href="{esc(data["theory_locations"][ref])}">{esc(labels["source_link"])}</a></p>'
    out += f'<p class="record-id">{esc(ref)}</p></div>'
    return out


def render_overview(copy, data):
    labels = copy['reading']['ledger']
    out = [f'<section class="theory-overview" id="theory-overview"><h3>{esc(labels["overview_heading"])}</h3><p>{esc(labels["overview_note"])}</p><div class="theory-framework-grid">']
    for framework, group in data['theory'].items():
        out.append(f'<section><h4><a href="#framework-{esc(framework)}">{esc(labels["frameworks"][framework])}</a></h4>')
        for family in labels['families']:
            matches = [item for item in group['readings'] if item['reader']['family'] == family]
            if not matches:
                attempts = [a for a in group['attempts'] if a['reader']['family'] == family]
                state = labels['empty_slot'] if not attempts else labels['failed_call'] if any(a['status'] != 'ok' for a in attempts) else labels['empty_answer']
                out.append(f'<article class="theory-card theory-awaiting"><h5>{esc(labels["families"][family])}</h5><p>{esc(state)}</p></article>')
            for item in matches:
                memo = item['record']
                out.append(f'<article class="theory-card"><h5><a href="#{rid(memo["id"])}">{esc(attribution(item, labels))}</a></h5>')
                out.append(exact(memo.get('plain'), field_id(memo['id'], 'plain')) or f'<p>{esc(labels["no_plain"])}</p>')
                out.append(f'<h6>{esc(labels["rival_label"])}</h6>')
                out.append(exact(memo.get('rival'), field_id(memo['id'], 'rival')) or f'<p>{esc(labels["not_recorded"])}</p>')
                out.append(f'<a class="small" href="#{rid(memo["id"])}">{esc(labels["full_link"])}</a></article>')
        out.append('</section>')
    out.append('</div></section>')
    return ''.join(out)


def render_comparison(copy, data):
    labels = copy['reading']['ledger']
    groups = defaultdict(list)
    for framework, group in data['theory'].items():
        for item in group['readings']:
            for n, evidence in enumerate(item['record'].get('evidence', [])):
                if 'not_found' not in evidence and evidence.get('quote'):
                    groups[evidence['id']].append((framework, item, n, evidence))
    # Four most widely cited records. Never select a failed quotation or invent an interpretation.
    selected = sorted(groups, key=lambda ref: (-len({x[1]['record']['id'] for x in groups[ref]}), ref))[:4]
    out = [f'<section class="theory-comparison" id="theory-comparison"><h3>{esc(labels["comparison_heading"])}</h3><p>{esc(labels["comparison_note"])}</p>']
    if not selected:
        out.append(f'<p class="theory-empty">{esc(labels["comparison_empty"])}</p>')
    for i, ref in enumerate(selected, 1):
        out.append(f'<article class="theory-passage"><h4>{esc(labels["passage_label"])} {i:02d}</h4><p><a href="{esc(data["theory_locations"][ref])}">{esc(labels["source_link"])}</a></p><div class="theory-comparison-grid">')
        for framework, item, n, evidence in groups[ref]:
            out.append(f'<section><p class="eyebrow">{esc(labels["frameworks"][framework])}</p><h5><a href="#{rid(item["record"]["id"])}">{esc(attribution(item, labels))}</a></h5>')
            out.append(evidence_html(evidence, n, item['record'], data, labels))
            out.append('</section>')
        out.append('</div></article>')
    return ''.join(out) + '</section>'


def render_readings(framework, copy, data):
    labels = copy['reading']['ledger']; group = data['theory'][framework]
    out = [f'<section class="theory-framework" id="framework-{esc(framework)}"><h3>{esc(labels["frameworks"][framework])}</h3>']
    if not group['readings']:
        out.append(f'<p class="theory-empty">{esc(labels["empty_framework"])}</p>')
    for attempt in group['attempts']:
        if attempt['status'] != 'ok' or not attempt['published']:
            state = labels['failed_call'] if attempt['status'] != 'ok' else labels['empty_answer']
            out.append(f'<p class="theory-call-status">{esc(attribution(attempt, labels))}: {esc(state)} <span class="record-id">{esc(attempt["activity"])}</span></p>')
    for item in group['readings']:
        memo = item['record']
        out.append(f'<article class="theory-ledger-reading" id="{rid(memo["id"])}"><h4>{esc(attribution(item, labels))}</h4><p class="signature exact">{esc(memo.get("signed") or labels["unsigned"])}</p>')
        out.append(f'<h5>{esc(labels["plain_label"])}</h5>')
        out.append(exact(memo.get('plain'), field_id(memo['id'], 'plain'), klass='theory-plain') or f'<p>{esc(labels["no_plain"])}</p>')
        out.append('<div class="theory-reading-layout"><div>')
        out.append(f'<h5>{esc(labels["body_label"])}</h5>')
        out.append(exact(memo.get('body'), memo['id'], 'div', 'theory-body'))
        if memo.get('quotes_not_found'):
            out.append(f'<aside class="quote-failure"><h5>{esc(labels["failed_prose"])}</h5><ul>')
            out.extend(f'<li class="exact">{esc(q)}</li>' for q in memo['quotes_not_found'])
            out.append('</ul></aside>')
        out.append('</div>')
        if item.get('gloss'):
            gloss = item['gloss']
            out.append(f'<aside class="theory-gloss"><h5>{esc(labels["gloss_label"])}</h5><p class="exact">{esc(gloss.get("gloss", ""))}</p><dl>')
            for term in gloss.get('terms', []):
                out.append(f'<dt>{esc(term["term"])}</dt><dd>{esc(term["means"])}</dd>')
            out.append('</dl></aside>')
        out.append(f'</div><h5>{esc(labels["evidence_label"])}</h5>')
        if not memo.get('evidence'):
            out.append(f'<p>{esc(labels["no_evidence"])}</p>')
        for n, evidence in enumerate(memo.get('evidence', [])):
            out.append(evidence_html(evidence, n, memo, data, labels))
        out.append(f'<div class="theory-tests"><section><h5>{esc(labels["against_label"])}</h5><ul>')
        out.extend(exact(sentence, field_id(memo['id'], f'against:{n}'), 'li') for n, sentence in enumerate(memo.get('against', [])))
        out.append('</ul>')
        if not memo.get('against'):
            out.append(f'<p>{esc(labels["not_recorded"])}</p>')
        out.append(f'</section><section><h5>{esc(labels["rival_label"])}</h5>')
        out.append(exact(memo.get('rival'), field_id(memo['id'], 'rival')) or f'<p>{esc(labels["not_recorded"])}</p>')
        out.append(f'</section></div><details class="theory-letter"><summary>{esc(labels["letter_label"])}</summary><p>{esc(labels["letter_note"])}</p>')
        for part in item['apparatus']['stack']:
            out.append(f'<p class="record-id">{esc(part["name"])} · SHA-256 {esc(part["sha256"])}</p>')
            out.append(exact(part['text'], 'prompt:' + part['sha256'], 'pre'))
        if not item['apparatus']['stack']:
            out.append(f'<p>{esc(labels["no_letter"])}</p>')
        out.append(f'</details><p class="record-id">{esc(memo["id"])}</p><p class="small"><a href="#theory-overview">{esc(labels["back_comparison"])}</a></p></article>')
    return ''.join(out) + '</section>'


def render_sources(copy, data):
    labels = copy['reading']['ledger']
    out = [f'<section class="sec"><h2>{esc(labels["sources_heading"])}</h2><p><a href="reading.html">{esc(labels["back_readings"])}</a></p>']
    for ref, source in data['theory_sources'].items():
        out.append(f'<article class="board-message" id="{rid(ref)}"><h3 class="record-id">{esc(ref)}</h3>')
        out.append(exact(source['body'], ref, 'blockquote'))
        if source.get('signed'):
            out.append(f'<p class="signature exact">{esc(source["signed"])}</p>')
        context = source.get('context', {})
        for key in ('page', 'introduced_by'):
            if context.get(key):
                out.append(f'<p class="record-id">{esc(context[key])}</p>')
        out.append('</article>')
    return ''.join(out) + '</section>'
