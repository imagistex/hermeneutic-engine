"""Thread views of the saved board. No reader calls and no project writes."""
from collections import Counter
import re
from notebook_sections import esc, rid, thread_file, quote, render_apparatus, paragraphs


def conversation_file(thread):
    return 'conversation-board.html' if thread == 'board' else thread_file(thread).replace('thread-', 'conversation-', 1)


def arrange_board(data, memos, lenses, activities):
    """Group whole records, retaining recorded order within each answer.

    A round is a batch of independent answers, not a live chat. Timestamp order
    here does not imply that later callers saw earlier answers in the same round.
    """
    threads = {'board': {'notes': [], 'posts': []}}
    for round_ in data['board']:
        letters = {}
        for answer in round_['answers']:
            for part in answer['apparatus']['stack']:
                if part['name'] == f'board-round-{round_["number"]}':
                    letters[part['sha256']] = part
            for order, memo in enumerate(answer['messages']):
                thread = memo.get('thread') or 'board'
                item = threads.setdefault(thread, {'notes': [], 'posts': []})
                item['posts'].append({'record': memo, 'answer': answer, 'round': round_['number'], 'order': order})
        round_['letters'] = list(letters.values())
    done = {a['id'] for a in activities if a.get('type') == 'read' and a.get('status') == 'ok'}
    notebook_lenses = {lens for r in data['board'] for a in r['answers'] for notebook in a['notebooks'] for lens in notebook}
    for thread, item in threads.items():
        item['posts'].sort(key=lambda p: (p['round'], p['record'].get('at', ''), p['answer']['own'], p['order']))
        units = tuple(u['id'] for u in data['board_threads'].get(thread, []))
        if units:
            for memo in memos.values():
                if (memo.get('memo_type') == 'field_note' and memo.get('by') in notebook_lenses
                        and memo.get('activity') in done and tuple(memo.get('about', [])) == units):
                    reader = lenses[memo['by']].get('reader', {})
                    context = {'record': memo, 'reader': {k: reader.get(k) for k in ('family', 'model')}}
                    item['notes'].append(context)
                    data['board_contexts'][memo['id']] = context
            item['notes'].sort(key=lambda n: (n['record'].get('at', ''), n['record']['id']))
    # Only public attribution fields belong in the export, including older linked notes.
    for context in data['board_contexts'].values():
        context['reader'] = {k: context['reader'].get(k) for k in ('family', 'model')}
    data['conversations'] = dict(sorted(threads.items(), key=lambda pair: (pair[0] != 'board', pair[0])))
    data['board_locations'] = {p['record']['id']: conversation_file(t) + '#' + rid(p['record']['id'])
                               for t, item in threads.items() for p in item['posts']}
    for t, item in threads.items():
        for n in item['notes']:
            data['board_locations'][n['record']['id']] = conversation_file(t) + '#' + rid(n['record']['id'])


def signature(post, labels):
    memo = post['record']
    # Requests carry the answer's signature, not a separate signed field.
    value = post['answer'].get('signed') if memo['memo_type'] == 'board_request' else memo.get('signed')
    return value if value else labels['unsigned']


def excerpt(record):
    body = record.get('body', '')
    # An exact contiguous opening, never an invented paraphrase or repaired quote.
    match = re.search(r'[.!?](?:\s|$)', body)
    end = match.start() + 1 if match else len(body)
    if end > 320:
        end = body.rfind(' ', 0, 300)
        if end < 1:
            end = min(300, len(body))
    return body[:end]


def round_links(data, thread=None):
    return [(r['number'], f'{conversation_file("board")}#round-{r["number"]}') for r in data['board']]


def render_index(copy, data, fill):
    b = copy['board']
    out = [f'<section class="sec forum-index" id="board"><p class="eyebrow">{esc(b["index_kicker"])}</p><h2>{esc(b["heading"])}</h2>',
           paragraphs(b['forum_intro'], fill),
           f'<nav class="forum-nav"><a href="#threads">{esc(b["threads_label"])}</a><a href="#round-record">{esc(b["archive_label"])}</a><a href="board-records.json">{esc(b["download"])}</a></nav>']
    out.append('<div class="board-status">')
    for r in data['board']:
        out.append(f'<p><a href="{conversation_file("board")}#round-{r["number"]}">{esc(b["round_label"])} {r["number"]}</a> · {r["completed"]} {esc(b["of"])} {r["expected"]} {esc(b["answered"])}. {esc(b["complete"] if r["complete"] else b["incomplete"])}</p>')
    out.append(f'</div><nav class="exchange-routes"><h3>{esc(b["exchange_heading"])}</h3><ul>')
    for route in b.get('exchange_routes', []):
        if route['memo'] in data['board_locations']:
            out.append(f'<li><a href="{data["board_locations"][route["memo"]]}">{esc(route["label"])}</a></li>')
    out.append(f'</ul></nav><h3 id="threads">{esc(b["threads_label"])}</h3><ol class="thread-index">')
    for thread, item in data['conversations'].items():
        posts = item['posts']; counts = Counter(p['record']['memo_type'] for p in posts)
        names = list(dict.fromkeys(signature(p, b) for p in posts))
        rounds = sorted({p['round'] for p in posts})
        heading = b['whole'] if thread == 'board' else f'{b["thread"]} {thread}'
        out.append(f'<li><article><h4><a href="{conversation_file(thread)}">{esc(heading)}</a></h4>')
        if thread == 'board':
            out.append(f'<p class="small">{esc(b["whole_description"])}</p>')
        chosen = next((p for p in posts if p['record']['memo_type'] == 'board_reply'), posts[0] if posts else None)
        if chosen:
            memo = chosen['record']
            out.append(f'<blockquote class="thread-excerpt"><div class="exact" data-quote="{esc(memo["id"])}">{esc(excerpt(memo))}</div><footer class="signature">{esc(signature(chosen,b))}</footer></blockquote>')
        out.append(f'<p class="thread-counts">{counts["board_reply"]} {esc(b["replies"])} · {counts["board_request"]} {esc(b["requests"])} · {counts["board_closing"]} {esc(b["closings"])} · {len(item["notes"])} {esc(b["notes_label"])}</p>')
        out.append(f'<p class="small">{esc(b["rounds_label"])}: {", ".join(map(str,rounds))}</p>')
        if thread == 'board':
            names = [b['researchers']] + names
        out.append(f'<p class="thread-people"><span>{esc(b["posted_by"])}:</span> '+ ' · '.join(f'<span class="exact">{esc(name)}</span>' for name in names) + '</p></article></li>')
    out.append('</ol></section>')
    return ''.join(out)


def letter_segments(number, text, b):
    """Split only on explicit author boundaries. Joining the parts restores every byte."""
    boundaries = []
    if number == 2:
        markers = ['From the model researcher:\n', 'From the other researcher:\n', '\n---\n']
        positions = [text.find(marker) for marker in markers]
        if positions == sorted(positions) and min(positions) >= 0:
            boundaries = [(0,b['researchers'],b['researcher_role']), (positions[0],b['model_researcher_signature'],b['model_researcher_role']),
                          (positions[1],b['human_researcher_signature'],b['human_researcher_role']), (positions[2],b['researchers'],b['researcher_role'])]
    elif number == 3:
        separators = list(re.finditer(r'\n---\n', text))
        if len(separators) == 4 and 'From la Claude:' in text:
            boundaries = [(0,b['model_researcher_signature'],b['model_researcher_role']),
                          (separators[0].start(),b['human_researcher_signature'],b['human_researcher_role']),
                          (separators[1].start(),b['model_researcher_signature'],b['model_researcher_role']),
                          (separators[2].start(),b['round_three_signature'],b['researcher_role']),
                          (separators[3].start(),b['model_researcher_signature'],b['model_researcher_role'])]
    if not boundaries:
        boundaries = [(0,b['researchers'],b['researcher_role'])]
    return [(name, role, text[start:boundaries[i+1][0] if i+1<len(boundaries) else len(text)])
            for i,(start,name,role) in enumerate(boundaries)]


def render_letters(round_, b, expanded):
    number = round_['number']
    out = [f'<div class="round-letter" id="letter-{number}">']
    if not expanded:
        out.append(f'<details><summary>{esc(b["letter_label"])} · {esc(b["round_label"])} {number}</summary>')
    else:
        out.append(f'<h4>{esc(b["letter_label"])}</h4>')
    if not round_['letters']:
        out.append(f'<p>{esc(b["letter_missing"])}</p>')
    for part in round_['letters']:
        for i,(name,role,body) in enumerate(letter_segments(number,part['text'],b)):
            anchor = f'letter-{number}-{part["sha256"][:8]}-{i+1}'
            out.append(f'<article class="forum-post researcher-post" id="{anchor}"><header><h4 class="post-signature">{esc(name)}</h4><p class="post-meta">{esc(role)} · {esc(b["round_label"])} {number}</p><p class="address">{esc(b["to"])}: {esc(b["all_readers"])}</p></header>')
            out.append(f'<div class="exact post-body" data-quote="prompt:{part["sha256"]}">{esc(body)}</div></article>')
        out.append(f'<p class="record-id">{esc(b["saved_letter"])}: {part["sha256"]}</p>')
    if not expanded:
        out.append('</details>')
        out.append(f'<p class="small">{esc(b["letter_context"])} <a href="{conversation_file("board")}#letter-{number}">{esc(b["letter_open"])}</a></p>')
    out.append('</div>')
    return ''.join(out)


def render_post(post, copy, data):
    b=copy['board']; memo=post['record']; answer=post['answer']; number=post['round']
    typ=memo['memo_type']; label=b[{'board_reply':'reply','board_request':'request_post','board_closing':'closing'}[typ]]
    out=[f'<article class="forum-post" id="{rid(memo["id"])}"><header><h3 class="post-signature">{esc(signature(post,b))}</h3>',
         f'<p class="post-meta">{esc(answer["family"])} / {esc(answer["model"])} · {esc(b["round_label"])} {number} · {esc(label)}</p>',
         f'<p class="address">{esc(b["to"])}: {esc(memo.get("to") or (b["researchers"] if typ=="board_request" else b["none"]))}</p></header>']
    if typ == 'board_request':
        out.append(f'<p class="small">{esc(b["request_signature_note"])}</p>')
    out.append(quote(memo))
    if memo.get('quotes_not_found'):
        out.append(f'<aside class="quote-failure"><p>{esc(b["not_found"])}</p><ul>'+''.join(f'<li class="exact">{esc(q)}</li>' for q in memo['quotes_not_found'])+'</ul></aside>')
    links = [f'<a href="#{rid(memo["id"])}">{esc(b["permalink"])}</a>']
    if memo.get('thread') in data['board_threads']:
        links.append(f'<a href="{thread_file(memo["thread"])}">{esc(b["statements"])}</a>')
    # Keep cross-thread exchanges one click away without guessing who an addressee was.
    refs = list(dict.fromkeys(memo.get('answers',[]) + memo.get('about',[])))
    available = [ref for ref in refs if ref in data['board_locations']]
    if available:
        out.append(f'<details class="context-links"><summary>{esc(b["context"])}</summary><ul>'+''.join(f'<li><a href="{data["board_locations"][ref]}">{esc(ref)}</a></li>' for ref in available)+'</ul></details>')
    out.append('<p class="post-links">'+' · '.join(links)+'</p>')
    out.append(f'<p class="record-id">{esc(memo["id"])}</p></article>')
    return ''.join(out)


def render_conversation(thread, item, copy, data):
    b=copy['board']; heading=b['whole'] if thread=='board' else f'{b["thread"]} {thread}'
    out=[f'<section class="sec conversation"><p class="eyebrow">{esc(b["heading"])}</p><h2>{esc(heading)}</h2>',
         f'<nav class="forum-nav"><a href="board.html#threads">{esc(b["thread_back"])}</a>']
    if thread in data['board_threads']:
        out.append(f'<a href="{thread_file(thread)}">{esc(b["statements"])}</a>')
    out.append(f'</nav><p class="small">{esc(b["order_note"])}</p><nav class="forum-nav">')
    if item['notes']:
        out.append(f'<a href="#field-notes">{esc(b["opening_notes"])}</a>')
    for r in data['board']:
        out.append(f'<a href="#round-{r["number"]}">{esc(b["round_label"])} {r["number"]}</a>')
    out.append('</nav>')
    if thread == 'board':
        out.append(f'<p class="small">{esc(b["whole_description"])}</p>')
    if item['notes']:
        out.append(f'<section id="field-notes"><h3>{esc(b["opening_notes"])}</h3>')
        for context in item['notes']:
            memo=context['record']; reader=context['reader']
            out.append(f'<article class="forum-post" id="{rid(memo["id"])}"><header><h4 class="post-signature">{esc(memo.get("signed") or b["unsigned"])}</h4><p class="post-meta">{esc(reader["family"])} / {esc(reader["model"])} · {esc(b["field_note_label"])}</p></header>{quote(memo)}<p class="record-id">{esc(memo["id"])}</p></article>')
        out.append('</section>')
    for r in data['board']:
        out.append(f'<section class="conversation-round" id="round-{r["number"]}"><h3>{esc(b["round_label"])} {r["number"]}</h3>')
        out.append(render_letters(r,b,thread=='board'))
        posts=[p for p in item['posts'] if p['round']==r['number']]
        for post in posts:
            out.append(render_post(post,copy,data))
        if not posts:
            out.append(f'<p class="small">{esc(b["no_thread_posts"])}</p>')
        out.append('</section>')
    out.append(f'<details class="conversation-apparatus"><summary>{esc(b["apparatus"])}</summary>')
    for r in data['board']:
        out.append(f'<h3>{esc(b["round_label"])} {r["number"]}</h3>')
        for a in r['answers']:
            out.append(f'<p class="signature">{esc(a.get("signed") or b["unsigned"])}</p>')
            out.append(render_apparatus(a['apparatus'], b['apparatus']))
    out.append(f'</details><p><a href="board.html#threads">{esc(b["thread_back"])}</a></p></section>')
    return ''.join(out)
