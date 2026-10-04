"""Public notebook sections. Read-only ledger selection; presentation copy is in site.json."""
from collections import Counter, defaultdict
from html import escape
import hashlib
import re

def esc(value):
    return escape(str(value), quote=True)

def rid(value):
    return value.replace(':', '-')

def thread_file(thread):
    slug=thread if re.fullmatch(r't[0-9]+',thread) else hashlib.sha256(thread.encode('utf-8')).hexdigest()[:16]
    return f'thread-{slug}.html'

def paragraphs(values, fill):
    return ''.join(f'<p>{esc(fill(p))}</p>' for p in values)

def enrich(project, data, copy):
    lenses = {r['id']: r for r in project.records('lens')}
    memos = {r['id']: r for r in project.records('memo')}
    activities = project.records('activity')
    codes = {r['id']: r for r in project.records('code')}
    codings = project.records('coding')
    applied = []
    for reader in data['readers']:
        done = {a['id'] for a in activities if a.get('lens') == reader['lens'] and a.get('status') == 'ok' and a.get('type') == 'read'}
        units = {c['unit'] for c in codings if c.get('by') == reader['lens'] and c.get('activity') in done
                 and codes.get(c.get('code'), {}).get('key') == 'end-leaving-word'}
        applied.append(units)
        data['placeholders']['leaving_' + reader['family']] = f'{len(units):,}'
        reader['apparatus'] = apparatus(project, lenses[reader['lens']])
    data['placeholders']['leaving_all'] = f'{len(set.intersection(*applied)) if applied else 0:,}'
    data['leaving_word'] = {'by_reader': {r['lens']:len(u) for r,u in zip(data['readers'],applied)},
                            'all':len(set.intersection(*applied)) if applied else 0}
    by_activity = defaultdict(list)
    for memo in memos.values():
        by_activity[memo.get('activity')].append(memo)
    rounds = defaultdict(list)
    for activity in activities:
        lens = lenses.get(activity.get('lens'), {})
        method = lens.get('method', {})
        if activity.get('type') != 'board' or method.get('name') != 'board':
            continue
        number = method.get('round')
        if not isinstance(number,int):
            continue
        reader = lens.get('reader', {})
        # Publish only the public-facing fields, never harness environments or call metadata.
        messages = [m for m in by_activity[activity['id']] if m.get('memo_type') in ('board_reply','board_request','board_closing')
                    and m.get('round') == number] if activity.get('status') == 'ok' else []
        rounds[number].append({'activity':activity['id'],'lens':lens['id'],'status':activity.get('status'),
            'own':reader.get('own',0),'family':reader.get('family'),'model':reader.get('model'),
            'signed':activity.get('signed'), 'notebooks':reader.get('notebooks',[]),
            'messages':messages, 'apparatus':apparatus(project,lens)})
    data['board'] = []
    contexts = {}
    for number, answers in sorted(rounds.items()):
        answers.sort(key=lambda a:(a['own'],a['activity']))
        expected = max((len(a['notebooks']) for a in answers),default=0)
        completed = Counter(a['own'] for a in answers if a['status']=='ok')
        data['board'].append({'number':number,'answers':answers,'expected':expected,'completed':len(completed),
                              'complete':expected>0 and set(completed)==set(range(expected)) and all(n==1 for n in completed.values())})
        for answer in answers:
            for m in answer['messages']:
                for ref in m.get('about',[]):
                    record = memos.get(ref)
                    if record and record.get('memo_type')=='field_note':
                        lens=lenses.get(record.get('by'),{})
                        contexts[ref]={'record':record,'reader':lens.get('reader',{})}
    data['board_contexts']=contexts
    data['board_threads']={}
    for round_ in data['board']:
        for answer in round_['answers']:
            for message in answer['messages']:
                thread=message.get('thread')
                if not thread or thread=='board':
                    continue
                candidates=[]
                for ref in message.get('about',[]):
                    if ref in contexts:
                        candidates.append(contexts[ref]['record'].get('about',[]))
                if message['memo_type']=='board_request':
                    candidates.append(message.get('about',[]))
                if not candidates:
                    continue
                if any(ids!=candidates[0] for ids in candidates):
                    raise ValueError(f'Cannot present {thread}: notebook unit orders do not align')
                ids=candidates[0]
                records=[]
                for uid in ids:
                    u=project.get(uid)
                    if not u or u.get('kind')!='unit':
                        raise ValueError(f'Thread {thread} refers to missing unit {uid}')
                    records.append({'id':uid,'body':project.unit_text(u),'context':u.get('context',{})})
                if thread in data['board_threads'] and data['board_threads'][thread]!=records:
                    raise ValueError(f'Thread {thread} changes its units between rounds')
                data['board_threads'][thread]=records
    from board_forum import arrange_board
    arrange_board(data, memos, lenses, activities)
    from theory_readings import select_theory
    select_theory(project, data, copy)
    for excerpt in copy['readers'].get('exchanges',[]):
        record=memos.get(excerpt['memo'])
        if not record or excerpt['quote'] not in record.get('body',''):
            raise ValueError(f'Board excerpt not exact: {excerpt["memo"]}')
        excerpt['signed']=record.get('signed')

    # Saved edit labels offer a separate chronology from signed posts.
    from hermeneutic_engine.views.names import date_of
    days=defaultdict(lambda:Counter(total=0,dated=0))
    for source in project.records('source'):
        ctx=source.get('context',{}); stamp=ctx.get('time','')
        if stamp:
            day=stamp[:10]; days[day]['total']+=1
            days[day]['dated']+=bool(date_of(ctx.get('label') or ''))
    data['edit_names']=[{'day':day,**values} for day,values in sorted(days.items())]
    return data

def apparatus(project,lens):
    stack=[]
    for part in lens.get('stack',[]):
        raw=project.part_path(part['sha256']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=part['sha256']:
            raise ValueError('Prompt blob hash mismatch')
        stack.append({'name':part['name'],'sha256':part['sha256'],'text':raw.decode('utf-8')})
    return {'lens':lens['id'],'priors':lens.get('priors',[]),'stack':stack}

def render_apparatus(info, labels):
    body='<ul>'+''.join(f'<li>{esc(p)}</li>' for p in info['priors'])+'</ul>'
    for part in info['stack']:
        body+=f'<details class="prompt"><summary>{esc(part["name"])}</summary><p class="record-id">{esc(part["sha256"])}</p><pre class="exact">{esc(part["text"])}</pre></details>'
    return f'<details class="apparatus"><summary>{esc(labels)}</summary><p class="record-id">{esc(info["lens"])}</p>{body}</details>'

def plate(copy, key):
    p=copy['plates'][key]
    return (f'<figure class="plate"><img src="images/{esc(p["file"])}" width="1200" height="800" loading="lazy" decoding="async" alt="{esc(p["alt"])}">'
            f'<figcaption>{esc(p["caption"])} <a href="images/prompts/{esc(p["prompt"])}">{esc(p["prompt_label"])}</a></figcaption></figure>')

def render_theory(copy,data,fill,site):
    from theory_readings import render_overview, render_comparison, render_readings
    b=copy['reading']; out=[f'<section class="sec theory" id="reading"><h2>{esc(b["heading"])}</h2><p>{esc(b["intro"])}</p>']
    labels = b['ledger']
    out.append(f'<nav class="forum-nav"><a href="#theory-overview">{esc(labels["overview_heading"])}</a><a href="#theory-comparison">{esc(labels["comparison_heading"])}</a></nav>')
    for i,m in enumerate(b['memos'],1):
        if i == 2:
            out.append(render_overview(copy, data))
            out.append(render_comparison(copy, data))
        if m['id'] in ('foucault', 'lacan'):
            framework = {'foucault': 'foucauldian', 'lacan': 'lacanian'}[m['id']]
            out.append(render_readings(framework, copy, data))
            out.append(f'<details class="theory-draft"><summary>{esc(b["ledger"]["draft_label"])}</summary>')
        if m.get('status') != 'held' and (not m.get('paragraphs') or not m.get('rival')):
            raise ValueError(f'Theory memo {m["id"]} needs both its reading and its rival before publication')
        out.append(f'<article class="theory-memo" id="memo-{esc(m["id"])}"><p class="eyebrow">{i:02d} / {esc(m["subtitle"])}</p><h3>{esc(m["title"])}</h3>')
        if m.get('status') == 'draft':
            out.append(f'<p class="draft-notice">{esc(b["draft_notice"])}</p>')
        out.append('<div class="paired">')
        for key,label in [('paragraphs','claim_label'),('rival','rival_label')]:
            body=memo_paragraphs(m.get(key,[]),m,fill) if m.get('status')!='held' else f'<p class="held">{esc(m.get("held_reason") or b["held"])}</p>'
            out.append(f'<section><h4>{esc(b[label])}</h4>{body}</section>')
        out.append(f'</div><details><summary>{esc(b["evidence_label"])}</summary><p>{esc(b["evidence_note"])}</p>')
        for e in m.get('evidence',[]) if m.get('status')!='held' else []:
            label=b.get(e.get('position','')+'_label','')
            if label: out.append(f'<p class="eyebrow">{esc(label)}</p>')
            out.append(f'<blockquote><p class="exact" data-quote="{esc(e["unit"])}">{esc(e["quote"])}</p></blockquote><p class="record-id">{esc(e["unit"])}</p>')
            if e.get('note'): out.append(f'<p>{esc(fill(e["note"]))}</p>')
        out.append('</details></article>')
        if m['id'] in ('foucault', 'lacan'):
            out.append('</details>')
    out.append(f'<p class="small">{esc(b["note"])}</p></section>')
    return ''.join(out)


def memo_paragraphs(values,memo,fill):
    if memo.get('format') != 'source-markdown':
        return paragraphs(values,fill)
    # Only source emphasis becomes markup. No rewriting, interpolation or smart punctuation.
    out=[]
    for value in values:
        text=esc(value)
        text=re.sub(r'\*\*([^*]+)\*\*',r'<strong>\1</strong>',text)
        text=re.sub(r'\*([^*]+)\*',r'<em>\1</em>',text)
        out.append(f'<p>{text}</p>')
    return ''.join(out)

def render_method(copy,data,fill,site):
    from engine_visual import render_engine
    b=copy['method']
    out=f'<section class="sec" id="method"><h2>{esc(b["heading"])}</h2>{paragraphs(b["paragraphs"][:1],fill)}'
    out+=render_engine(copy, embedded=True)+paragraphs(b['paragraphs'][1:3],fill)
    out+=plate(copy,b['plate'])+paragraphs(b['paragraphs'][3:],fill)
    return out+f'<p><a href="{esc(b["link"])}">{esc(b["link_label"])}</a></p></section>'

def render_culture(copy,data,fill,site):
    b=copy['culture']; out=f'<section class="sec" id="culture"><h2>{esc(b["heading"])}</h2>{paragraphs(b["paragraphs"],fill)}'
    for m in b['moments']:
        t=data['terms'][m['term']]; first=t['first']; eid=next(e['id'] for f in data['words'] for e in f['entries'] if e['term']==m['term'])
        out+=f'<article class="moment"><p class="eyebrow">{esc(m["term"])}</p><h3>{esc(m["title"])}</h3><p>{esc(m["text"])}</p>'
        out+=f'<p class="small">{esc(b["trace_label"])}: {esc(first["time"])} · {esc(first.get("signature",first.get("sig","")))}</p>'
        out+=f'<p class="trace-counts">{t["posts"]:,} {esc(b["posts_label"])} · {t["signatures_total"]:,} {esc(b["signatures_label"])} · {t["adoption"]["1h"]} {esc(b["first_hour_label"])}</p>'
        out+=f'<p><a href="words.html#{esc(eid)}">{esc(b["word_link"])}</a></p></article>'
    out+=f'<h3>{esc(b["names_heading"])}</h3><p>{esc(b["names_note"])}</p><div class="fig-scroll"><table class="name-history"><thead><tr><th>{esc(b["day_label"])}</th><th>{esc(b["edits_label"])}</th><th>{esc(b["dated_label"])}</th></tr></thead><tbody>'
    # Group the earlier sparse traffic; keep the arrival and later days visible.
    earlier=[r for r in data['edit_names'] if r['day']<'2026-06-16']
    shown=([{'day':earlier[0]['day']+' – '+earlier[-1]['day'],'total':sum(r['total'] for r in earlier),'dated':sum(r['dated'] for r in earlier)}] if earlier else [])+[r for r in data['edit_names'] if r['day']>='2026-06-16']
    for r in shown:
        out+=f'<tr><th>{esc(r["day"])}</th><td>{r["total"]:,}</td><td>{r["dated"]:,} / {r["total"]:,}</td></tr>'
    out+='</tbody></table></div>'+plate(copy,b['plate'])
    out+=f'<h3>{esc(b["rival_heading"])}</h3><ul class="plain">'+''.join(f'<li>{esc(p)}</li>' for p in b['rivals'])+'</ul>'
    return out+f'<p class="small">{esc(b["note"])}</p></section>'

def quote(m):
    return f'<blockquote class="message"><div class="exact" data-quote="{esc(m["id"])}">{esc(m.get("body",""))}</div></blockquote>'

def render_round_archive(copy,data,fill,site):
    b=copy['board']; out=[f'<section class="sec board" id="board"><h2>{esc(b["archive_label"])}</h2>{paragraphs(b["paragraphs"],fill)}']
    out.append(f'<p class="small"><a href="board-records.json">{esc(b["download"])}</a></p>')
    out.append('<nav class="round-nav" id="rounds">'+''.join(f'<a href="#round-{r["number"]}">{esc(b["round_label"])} {r["number"]}</a>' for r in data['board'])+'</nav>')
    if not data['board']: out.append(f'<p>{esc(b["empty"])}</p>')
    all_messages={m['id'] for r in data['board'] for a in r['answers'] for m in a['messages']}
    for r in data['board']:
        out.append(f'<section class="board-round" id="round-{r["number"]}"><p class="eyebrow">{esc(b["round_label"])} {r["number"]}</p><h3>{r["completed"]} {esc(b["of"])} {r["expected"]} {esc(b["answered"])}</h3><p>{esc(b["complete"] if r["complete"] else b["incomplete"])}</p>')
        for a in r['answers']:
            counts=Counter(m['memo_type'] for m in a['messages'])
            out.append(f'<details class="board-reader" id="{rid(a["activity"])}"><summary><span>{esc(a["family"])} / {esc(a["model"])}</span><span class="summary-count">{counts["board_reply"]} {esc(b["replies"])} · {counts["board_request"]} {esc(b["requests"])}</span></summary>')
            out.append(f'<p class="signature">{esc(b["signature"])}: <span>{esc(a["signed"] or b["unsigned"])}</span></p>')
            out.append(render_apparatus(a['apparatus'],b['apparatus']))
            if a['status']!='ok':
                out.append(f'<p>{esc(b["failed"])}</p>')
            elif not a['messages']:
                out.append(f'<p>{esc(b["empty_answer"])}</p>')
            for m in a['messages']:
                typ=m['memo_type']; label=b[{'board_reply':'reply','board_request':'request','board_closing':'closing'}[typ]]
                where=b['whole'] if m.get('thread')=='board' else m.get('thread','')
                out.append(f'<article class="board-message" id="{rid(m["id"])}"><h4>{esc(label)}{(" · "+esc(where)) if where else ""}</h4>')
                if m.get('to'): out.append(f'<p class="address">{esc(b["to"])}: {esc(m["to"])}</p>')
                out.append(quote(m))
                if m.get('quotes_not_found'):
                    out.append(f'<aside class="quote-failure"><p>{esc(b["not_found"])}</p><ul>'+''.join(f'<li class="exact">{esc(q)}</li>' for q in m['quotes_not_found'])+'</ul></aside>')
                if m.get('thread') in data['board_threads']:
                    out.append(f'<p class="small"><a href="{thread_file(m["thread"])}">{esc(b["statements"])}</a></p>')
                refs=[ref for ref in m.get('about',[]) if ref in data['board_contexts']]+[ref for ref in m.get('answers',[]) if ref in all_messages]
                if refs:
                    out.append(f'<details class="context-links"><summary>{esc(b["context"])}</summary><ul>'+''.join(f'<li><a href="#{rid(ref)}">{esc(ref)}</a></li>' for ref in refs)+'</ul></details>')
                out.append(f'<p class="record-id">{esc(m["id"])}</p></article>')
            out.append('</details>')
        out.append(f'<p class="small"><a href="#rounds">{esc(b["back"])}</a></p></section>')
    out.append(f'<details class="board-notes"><summary>{esc(b["read_notes"])}</summary>')
    for ref,item in sorted(data['board_contexts'].items()):
        m=item['record']; reader=item['reader']
        out.append(f'<article class="board-message" id="{rid(ref)}"><p class="eyebrow">{esc(reader.get("family"))} / {esc(reader.get("model"))}</p><p class="signature">{esc(m.get("signed") or b["unsigned"])}</p>{quote(m)}<p class="record-id">{esc(ref)}</p></article>')
    out.append('</details></section>')
    return ''.join(out)


def render_board(copy,data,fill,site):
    from board_forum import render_index
    return render_index(copy,data,fill) + render_round_archive(copy,data,fill,site).replace('id="board"','id="round-record"',1)


def render_thread(thread, records, copy):
    b=copy['board']
    out=f'<section class="sec" id="thread"><p class="eyebrow">{esc(b["thread"])} {esc(thread)}</p><h2>{esc(b["statements"])}</h2><p>{esc(b["thread_note"])}</p><p class="note small">{esc(b["thread_context"])}</p><p><a href="what-happened.html">{esc(copy["what_happened"]["heading"])}</a> · <a href="board.html">{esc(b["thread_back"])}</a></p>'
    from board_forum import conversation_file
    out+=f'<p><a href="{conversation_file(thread)}">{esc(b["conversation"])}</a></p>'
    for n,unit in enumerate(records,1):
        ctx=unit['context']
        out+=f'<article class="board-message" id="u{n:02d}"><h3>u{n:02d}</h3><p class="small">{esc((ctx.get("first_seen") or {}).get("time", ""))}</p>'
        out+=f'<blockquote class="message"><div class="exact" data-quote="{esc(unit["id"])}">{esc(unit["body"])}</div></blockquote>'
        out+=f'<p class="record-id">{esc(b["page_label"])}: {esc(ctx.get("page", ""))}<br>{esc(b["saved_as"])}: {esc(ctx.get("introduced_by", ""))}<br>{esc(unit["id"])}</p></article>'
    return out+f'<p><a href="board.html">{esc(b["thread_back"])}</a></p></section>'
