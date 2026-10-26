"""The bench report as one self-contained HTML page (no external files), written by bench/report.py.

Totals per task as a colour scale (darker blue = higher total, failed gates marked), the hardest tasks, how often
each model ranked where, the dimension, cost and failure tag tables, telemetry charts (time, input tokens and peak
context per task, context per model request), and the load tests as line charts per concurrency level with their
tables. Hover a cell or a chart point for its details. Light and dark follow the system setting.
"""
import datetime
import html
import statistics

# Sequential blue ramp, lightest to darkest (validated reference palette); text turns white from step 450
RAMP = ['#cde2fb', '#b7d3f6', '#9ec5f4', '#86b6ef', '#6da7ec', '#5598e7', '#3987e5', '#2a78d6', '#256abf',
        '#1c5cab', '#184f95', '#104281', '#0d366b']
WHITE_TEXT_FROM = 7
SERIES = 8  # categorical slots defined in the CSS, used in fixed order

CSS = """
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10); --neutral: #f0efec; --critical: #d03b3b;
  --s1: #2a78d6; --s2: #eb6834; --s3: #1baf7a; --s4: #eda100; --s5: #e87ba4; --s6: #008300; --s7: #4a3aa7; --s8: #e34948;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10); --neutral: #383835; --critical: #e66767;
    --s1: #3987e5; --s2: #d95926; --s3: #199e70; --s4: #c98500; --s5: #d55181; --s6: #008300; --s7: #9085e9; --s8: #e66767;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10); --neutral: #383835; --critical: #e66767;
  --s1: #3987e5; --s2: #d95926; --s3: #199e70; --s4: #c98500; --s5: #d55181; --s6: #008300; --s7: #9085e9; --s8: #e66767;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--ink); font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1180px; margin: 0 auto; padding: 24px 16px 64px; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 17px; margin: 36px 0 6px; }
h3 { font-size: 14px; margin: 20px 0 6px; }
p.note, .sub { color: var(--ink-2); margin: 0 0 12px; max-width: 80ch; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 12px; overflow-x: auto; }
table { border-collapse: collapse; font-variant-numeric: tabular-nums; }
th, td { padding: 6px 8px; text-align: right; white-space: nowrap; border-bottom: 1px solid var(--grid); }
th { color: var(--ink-2); font-weight: 600; }
th:first-child, td:first-child { text-align: left; }
table.heat td.c { text-align: center; min-width: 50px; padding: 5px 4px; border: 2px solid var(--surface); border-radius: 4px; }
table.heat td.c b { font-size: 15px; }
table.heat td.c small { display: block; font-size: 11px; opacity: .85; }
td.fail { background: var(--neutral); color: var(--critical); }
td.wait { background: var(--neutral); color: var(--ink-2); }
.legend { display: flex; gap: 16px; flex-wrap: wrap; margin: 4px 0 8px; color: var(--ink-2); }
.legend i { display: inline-block; width: 12px; height: 12px; border-radius: 3px; margin-right: 6px; vertical-align: -1px; }
.ramp { display: inline-flex; vertical-align: middle; margin: 0 6px; }
.ramp span { width: 14px; height: 10px; }
.charts { display: grid; grid-template-columns: repeat(auto-fit, minmax(460px, 1fr)); gap: 16px; }
@media (max-width: 520px) { .charts { grid-template-columns: 1fr; } }
svg text { fill: var(--muted); font-size: 13px; }
svg .label { fill: var(--ink-2); font-size: 13px; }
svg .hit { fill: transparent; }
svg .hit:hover + .dot, svg .dot:hover { r: 6; }
.sticky-legend { position: sticky; top: 0; z-index: 1; background: var(--page); padding: 6px 0; }
ul.pending { columns: 2; color: var(--ink-2); }
code { font-size: 12px; }
@media (max-width: 700px) { ul.pending { columns: 1; } }
"""


def esc(x):
    return html.escape(str(x))


def mean(xs):
    return statistics.mean(xs) if xs else None


def num(x, digits=1):
    return '' if x is None else f'{x:.{digits}f}'


def short(task):
    return task.split('-')[0].upper()


def heat_cell(c):
    """One task cell: the mean total on the colour scale, a failed gate, or a run still to be graded."""
    if c is None:
        return '<td class="c"></td>'
    gates = f"{c['gates']}/{c['n']} passed the gates"
    if c['done']:
        total = mean(c['done'])
        if total == 0 and c['gates'] == 0:
            failed = ', '.join(c['failed']) or 'gate'
            return f'<td class="c fail" title="{esc(gates)}; failed: {esc(failed)}"><b>&#10007; 0</b><small>{esc(failed)}</small></td>'
        step = min(len(RAMP) - 1, round(total / 10 * (len(RAMP) - 1)))
        ink = '#ffffff' if step >= WHITE_TEXT_FROM else '#0b0b0b'
        spread = f' ({min(c["done"]):g}-{max(c["done"]):g})' if len(c['done']) > 1 else ''
        more = f', {c["pending"]} to grade' if c['pending'] else ''
        return (f'<td class="c" style="background:{RAMP[step]};color:{ink}" title="mean {total:.1f}/10{spread}; '
                f'{esc(gates)}{more}"><b>{total:.1f}</b><small>{c["gates"]}/{c["n"]}{" +" + str(c["pending"]) + "?" if c["pending"] else ""}</small></td>')
    return (f'<td class="c wait" title="{esc(gates)}; {c["pending"]} to grade"><b>?</b>'
            f'<small>{c["gates"]}/{c["n"]} &middot; grade</small></td>')


def totals_section(data):
    """The totals per task as a colour scale, plus the hardest tasks across every row."""
    models, tasks, cells = data['models'], data['tasks'], data['cells']
    head = ''.join(f'<th title="{esc(t)}">{esc(short(t))}</th>' for t in tasks)
    rows = []
    for m in models:
        means = [mean(cells[(m, t)]['done']) for t in tasks if (m, t) in cells and cells[(m, t)]['done']]
        pending = sum(cells[(m, t)]['pending'] for t in tasks if (m, t) in cells)
        # Until every run is graded the mean covers only some tasks (often just the failed gates)
        mean_cell = (f'<td class="wait" title="mean of the graded tasks so far: {num(mean(means)) or "none"};'
                     f' {pending} runs still to grade">?</td>' if pending else f'<td>{num(mean(means))}</td>')
        rows.append(f'<tr><td>{esc(m)}</td>' + ''.join(heat_cell(cells.get((m, t))) for t in tasks) + mean_cell + '</tr>')
    ramp = ''.join(f'<span style="background:{c}"></span>' for c in RAMP)
    out = [f'<h2>Totals per task</h2><p class="note">Mean total out of 10 over complete runs, darker is higher:'
           f' 0<span class="ramp">{ramp}</span>10. Small print: runs passing the gates / runs, and how many still'
           f' need a human score (+k?). &#10007; marks a failed gate, ? a run that passed the gates and waits for'
           f' grading. Hover a cell for details.</p>',
           f'<div class="card"><table class="heat"><tr><th>harness target model</th>{head}<th title="mean of tasks">mean</th></tr>'
           + ''.join(rows) + '</table></div>']
    hardest = []
    for t in tasks:
        cs = [cells[(m, t)] for m in models if (m, t) in cells]
        runs = sum(c['n'] for c in cs)
        passed = sum(c['gates'] for c in cs)
        done = [x for c in cs for x in c['done']]
        hardest.append((passed / runs if runs else 1, mean(done) if done else 99, t, passed, runs, done))
    hardest.sort()
    rows = ''.join(f'<tr><td title="{esc(t)}">{esc(short(t))} <span class="sub">{esc(t)}</span></td><td>{p}/{n}</td>'
                   f'<td>{num(mean(d))}</td><td>{len(d)}</td></tr>' for _, _, t, p, n, d in hardest[:6])
    out.append('<h3>Hardest tasks</h3><p class="note">Across every row above: fewest runs passing the gates first,'
               ' then the lowest mean total of the graded runs.</p>'
               f'<div class="card"><table><tr><th>task</th><th>gates passed</th><th>mean total</th>'
               f'<th>graded runs</th></tr>{rows}</table></div>')
    return '\n'.join(out)


def simple_table(header, rows):
    """A plain table; the first column is left aligned."""
    head = ''.join(f'<th>{esc(h)}</th>' for h in header)
    body = ''.join('<tr>' + ''.join(f'<td>{esc(c)}</td>' for c in r) + '</tr>' for r in rows)
    return f'<div class="card"><table><tr>{head}</tr>{body}</table></div>'


def mean_range(xs, digits=1):
    """'mean (min-max)' or just the mean when all values are equal."""
    if not xs:
        return ''
    m = statistics.mean(xs)
    return f'{m:.{digits}f}' if min(xs) == max(xs) else f'{m:.{digits}f} ({min(xs):g}-{max(xs):g})'


def model_sections(data):
    """Dimensions, cost and behaviour, failure tags."""
    models = data['models']
    dims = [[m] + [num(mean(v), 2) for v in data['dims'][m].values()] + [data['cost'][m]['gates']] for m in models]
    cost = []
    for m in models:
        c = data['cost'][m]
        cost.append([m, mean_range(c['wall min']), mean_range(c['requests'], 0), mean_range(c['input k'], 0),
                     mean_range(c['output k']), mean_range(c['peak ctx k'], 0), mean_range(c['tool errors']),
                     c['unbacked claims'], num(c['output k per passing run'])])
    tags = [[m, ', '.join(f'{k} {v}' for k, v in data['tags'][m].most_common()) or 'none'] for m in models]
    return '\n'.join([
        '<h2>Dimensions</h2><p class="note">Mean score 1 to 10 over the runs that passed the gates (automatic and'
        ' human values; a human score overrides the suggestion).</p>',
        simple_table(['model'] + list(next(iter(data['dims'].values()), {}).keys()) + ['gates'], dims),
        '<h2>Cost and behaviour</h2><p class="note">Means per run (min-max). Unbacked claims: the final message'
        ' claims a build or test result but no build or test command ran.</p>',
        simple_table(['model', 'wall min', 'requests', 'input k', 'output k', 'peak ctx k', 'tool errors',
                      'unbacked claims', 'output k per passing run'], cost),
        '<h2>Failure tags</h2><p class="note">Human tag where given, else the suggestion (marked *).</p>',
        simple_table(['model', 'tags'], tags)])


def nice_max(v):
    """A round axis maximum at or above v."""
    if v <= 0:
        return 1
    for step in (1, 2, 2.5, 5, 10):
        mag = 10 ** (len(str(int(v))) - 1)
        if step * mag >= v:
            return step * mag
    return v


def spread_labels(labels, gap=15):
    """Direct labels at the line ends, [y, x, name] each, moved apart so none overlap."""
    labels.sort()
    for prev, cur in zip(labels, labels[1:]):
        if cur[0] - prev[0] < gap:
            cur[0] = prev[0] + gap
    return [f'<text class="label" x="{x:.1f}" y="{y:.1f}">{esc(name)}</text>' for y, x, name in labels]


def line_chart(title, unit, series, xs):
    """An SVG line chart over the concurrency levels xs; series = [(name, slot, [(x, y, tip)])]."""
    w, h, left, right, top, bottom = 520, 260, 44, 140, 12, 38
    ys = [y for _, _, pts in series for _, y, _ in pts if y is not None]
    ymax = nice_max(max(ys) * 1.05 if ys else 1)
    px = {x: left + (i + 0.5) * (w - left - right) / len(xs) for i, x in enumerate(xs)}

    def py(y):
        return top + (1 - y / ymax) * (h - top - bottom)

    parts = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="{esc(title)}">']
    labels = []
    for i in range(5):
        y = ymax * i / 4
        parts.append(f'<line x1="{left}" x2="{w - right}" y1="{py(y):.1f}" y2="{py(y):.1f}" '
                     f'stroke="var({"--axis" if i == 0 else "--grid"})" stroke-width="1"/>'
                     f'<text x="{left - 6}" y="{py(y) + 4:.1f}" text-anchor="end">{y:g}</text>')
    for x in xs:
        parts.append(f'<text x="{px[x]:.1f}" y="{h - bottom + 16}" text-anchor="middle">{x}</text>')
    parts.append(f'<text x="{(left + w - right) / 2:.1f}" y="{h - 4}" text-anchor="middle">agents at once</text>')
    for name, slot, pts in series:
        pts = [(x, y, tip) for x, y, tip in pts if y is not None]
        if not pts:
            continue
        color = f'var(--s{slot})'
        path = ' '.join(f'{"M" if i == 0 else "L"}{px[x]:.1f},{py(y):.1f}' for i, (x, y, _) in enumerate(pts))
        parts.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round"/>')
        for x, y, tip in pts:
            parts.append(f'<g><circle class="hit" cx="{px[x]:.1f}" cy="{py(y):.1f}" r="12"><title>{esc(tip)}</title></circle>'
                         f'<circle class="dot" cx="{px[x]:.1f}" cy="{py(y):.1f}" r="4" fill="{color}" '
                         f'stroke="var(--surface)" stroke-width="2" pointer-events="none"/></g>')
        lx, ly, _ = pts[-1]
        labels.append([py(ly) + 4, px[lx] + 10, name])
    parts += spread_labels(labels)
    parts.append('</svg>')
    return f'<div class="card"><h3 style="margin:0 0 4px">{esc(title)}</h3><div class="sub">{esc(unit)}</div>{"".join(parts)}</div>'


def ramp_cell(text, share, title=''):
    """A table cell shaded on the blue ramp by share (0 to 1), blank when share is None."""
    if share is None:
        return f'<td class="c" title="{esc(title)}">{esc(text)}</td>'
    step = min(len(RAMP) - 1, round(share * (len(RAMP) - 1)))
    ink = '#ffffff' if step >= WHITE_TEXT_FROM else '#0b0b0b'
    return f'<td class="c" style="background:{RAMP[step]};color:{ink}" title="{esc(title)}"><b>{esc(text)}</b></td>'


def ranking_section(data):
    """How often each model ranked where, and its rank on every task."""
    width, ranked = data.get('rank_summary') or (0, {})
    if not width:
        return ''
    models, tasks, ranks = data['models'], data['tasks'], data['ranks']
    head = ''.join(f'<th>#{i}</th>' for i in range(1, width + 1))
    rows = []
    for m in models:
        r = ranked[m]
        cells = ''.join(ramp_cell(c or '', c / r['n'] if c and r['n'] else None,
                                  f'{m}: ranked #{i} on {c} of {r["n"]} tasks')
                        for i, c in enumerate(r['counts'], 1))
        rel = r['relative']
        rows.append(f'<tr><td>{esc(m)}</td>{cells}<td>{r["n"]}</td><td>{num(r["mean_rank"])}</td>'
                    + ramp_cell(num(rel, 2), None if rel is None else 1 - rel, '0 is always first, 1 always last')
                    + '</tr>')
    dist = (f'<div class="card"><table class="heat"><tr><th>model</th>{head}<th>ranked</th><th>mean rank</th>'
            f'<th>relative</th></tr>{"".join(rows)}</table></div>')
    thead = ''.join(f'<th title="{esc(t)}">{esc(short(t))}</th>' for t in tasks)
    rows = []
    for m in models:
        cells = []
        for t in tasks:
            rs = ranks.get((m, t)) or []
            if not rs:
                cells.append('<td class="c"></td>')
                continue
            text = ', '.join(str(r) for r, _ in rs)
            share = 1 - statistics.mean((r - 1) / (n - 1) for r, n in rs) if all(n > 1 for _, n in rs) else None
            cells.append(ramp_cell(text, share, f'{m}, {t}: ' + ', '.join(f'#{r} of {n}' for r, n in rs)))
        rows.append(f'<tr><td>{esc(m)}</td>{"".join(cells)}</tr>')
    per_task = f'<div class="card"><table class="heat"><tr><th>model</th>{thead}</tr>{"".join(rows)}</table></div>'
    return ('<h2 id="ranking">Ranking</h2><p class="note">The reviewer ranks every run of a task against the others.'
            ' Top: how many tasks each model placed at each rank (darker is a larger share of its tasks), its mean'
            ' rank, and the relative rank: the mean of (rank - 1) / (runs ranked - 1), so 0 is always first and 1'
            ' always last, comparable across tasks with different numbers of runs. Excluded runs keep their place,'
            ' so a rank can be missing.</p>' + dist +
            '<h3>Rank per task</h3><p class="note">Darker is nearer the top of that task.</p>' + per_task)


def model_style(i):
    """Colour slot and dash pattern of the i-th model: the colours repeat after SERIES models, dashed then."""
    return i % SERIES + 1, '' if i < SERIES else ' stroke-dasharray="5 3"'


def model_legend(models):
    """The legend of the telemetry charts: one swatch per model row."""
    items = []
    for i, m in enumerate(models):
        slot, dash = model_style(i)
        swatch = (f'<svg width="22" height="10" style="margin-right:6px;vertical-align:0"><line x1="0" x2="22" y1="5" y2="5"'
                  f' stroke="var(--s{slot})" stroke-width="4"{dash}/></svg>')
        items.append(f'<span>{swatch}{esc(m)}</span>')
    return f'<div class="legend">{"".join(items)}</div>'


def bar_chart(title, unit, tasks, models, values, digits=0):
    """Grouped bars: one group per task, one bar per model with its mean, hover for mean (min-max) and runs."""
    bar, gap, left, top, bottom, h = 9, 14, 52, 12, 40, 260
    group = bar * len(models) + gap
    w = left + group * len(tasks) + 10
    ys = [mean(v) for v in values.values() if v]
    ymax = nice_max(max(ys) * 1.05 if ys else 1)

    def py(y):
        return top + (1 - y / ymax) * (h - top - bottom)

    parts = [f'<svg viewBox="0 0 {w} {h}" width="{w}" role="img" aria-label="{esc(title)}">']
    for i in range(5):
        y = ymax * i / 4
        parts.append(f'<line x1="{left}" x2="{w - 10}" y1="{py(y):.1f}" y2="{py(y):.1f}" '
                     f'stroke="var({"--axis" if i == 0 else "--grid"})" stroke-width="1"/>'
                     f'<text x="{left - 6}" y="{py(y) + 4:.1f}" text-anchor="end">{y:g}</text>')
    for ti, t in enumerate(tasks):
        x0 = left + ti * group + gap / 2
        parts.append(f'<text x="{x0 + bar * len(models) / 2:.1f}" y="{h - bottom + 16}" text-anchor="middle">'
                     f'<title>{esc(t)}</title>{esc(short(t))}</text>')
        for mi, m in enumerate(models):
            v = values.get((m, t))
            if not v:
                continue
            y = mean(v)
            slot, _ = model_style(mi)
            tip = f'{m}, {t}: {mean_range(v, digits)} over {len(v)} run{"s" if len(v) != 1 else ""}'
            # Past SERIES models the colours repeat, lighter
            faded = ' opacity=".55"' if mi >= SERIES else ''
            parts.append(f'<rect x="{x0 + mi * bar:.1f}" y="{py(y):.1f}" width="{bar - 1}" height="{max(py(0) - py(y), 0.5):.1f}"'
                         f' fill="var(--s{slot})"{faded}><title>{esc(tip)}</title></rect>')
    parts.append('</svg>')
    return (f'<div class="card"><h3 style="margin:0 0 4px">{esc(title)}</h3><div class="sub">{esc(unit)}</div>'
            f'{model_legend(models)}{"".join(parts)}</div>')


def context_chart(task, models, curves):
    """Context size per model request for one task: one line per run, coloured by model."""
    w, h, left, right, top, bottom = 520, 220, 52, 16, 12, 38
    runs = [(mi, m, c) for mi, m in enumerate(models) for c in curves.get((m, task), [])]
    xmax = max((len(c) for _, _, c in runs), default=1)
    ymax = nice_max(max((max(c) for _, _, c in runs if c), default=1) * 1.05)

    def px(i):
        return left + (i / max(xmax - 1, 1)) * (w - left - right)

    def py(y):
        return top + (1 - y / ymax) * (h - top - bottom)

    parts = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="context size, {esc(task)}">']
    for i in range(5):
        y = ymax * i / 4
        parts.append(f'<line x1="{left}" x2="{w - right}" y1="{py(y):.1f}" y2="{py(y):.1f}" '
                     f'stroke="var({"--axis" if i == 0 else "--grid"})" stroke-width="1"/>'
                     f'<text x="{left - 6}" y="{py(y) + 4:.1f}" text-anchor="end">{y:g}</text>')
    for x in sorted({1, xmax} | ({round(xmax / 2)} if xmax > 4 else set())):
        parts.append(f'<text x="{px(x - 1):.1f}" y="{h - bottom + 16}" text-anchor="middle">{x}</text>')
    parts.append(f'<text x="{(left + w - right) / 2:.1f}" y="{h - 4}" text-anchor="middle">model request</text>')
    for mi, m, c in runs:
        slot, dash = model_style(mi)
        path = ' '.join(f'{"M" if i == 0 else "L"}{px(i):.1f},{py(y):.1f}' for i, y in enumerate(c))
        tip = f'{m}: {len(c)} requests, peak {max(c):.0f}k, last {c[-1]:.0f}k'
        parts.append(f'<path d="{path}" fill="none" stroke="var(--s{slot})" stroke-width="1.6" stroke-linejoin="round"'
                     f'{dash} opacity=".85"><title>{esc(tip)}</title></path>')
    parts.append('</svg>')
    return (f'<div class="card"><h3 style="margin:0 0 4px" title="{esc(task)}">{esc(short(task))} '
            f'<span class="sub">{esc(task)}</span></h3><div class="sub">context tokens (k) per model request,'
            f' one line per run</div>{"".join(parts)}</div>')


def telemetry_section(data):
    """Charts from the transcripts: wall time, input tokens and peak context per task, and context growth per run."""
    models, tasks, tel = data['models'], data['tasks'], data.get('telemetry') or {}
    if not tel:
        return ''

    def values(key):
        return {k: v[key] for k, v in tel.items() if v[key]}

    curves = {k: v['context_k'] for k, v in tel.items() if v['context_k']}
    context = ''.join(context_chart(t, models, curves) for t in tasks if any((m, t) in curves for m in models))
    return '\n'.join([
        '<h2 id="telemetry">Telemetry</h2><p class="note">From the agents\' transcripts, all runs in the model tables'
        ' (failed gates included, excluded runs left out). Bars are the mean per model and task; hover a bar for'
        ' mean (min-max) and the number of runs. Input tokens count every request\'s prompt, so they grow with'
        ' both the number of requests and the context size.</p>',
        bar_chart('Time per task', 'wall minutes per run', tasks, models, values('wall'), 1),
        bar_chart('Input tokens per task', 'thousand tokens per run, summed over its model requests', tasks, models,
                  values('input_k')),
        bar_chart('Peak context per task', 'thousand tokens, the largest prompt of the run', tasks, models,
                  values('peak_ctx_k')),
        '<h3>Context during the tasks</h3><p class="note">The prompt size of each model request in order, one line per'
        ' run. A line that climbs steadily is an agent that keeps reading files into its context; a drop is'
        ' the harness compacting it.</p>'
        # The legend stays in view while scrolling through the per task charts
        + (f'<div class="sticky-legend">{model_legend(models)}</div><div class="charts">{context}</div>' if context else
                                              '<p class="note">No per request data in these transcripts.</p>')])


def load_section(loads):
    """The load tests: charts comparing every group per concurrency level, then each group's table."""
    if not loads:
        return ''
    xs = sorted({r['agents'] for lg in loads for r in lg['levels']})
    names = []
    for i, lg in enumerate(loads):
        model = lg['label'].split()[2] if len(lg['label'].split()) > 2 else lg['label']
        names.append((f'{model}', i % SERIES + 1, lg))
    legend = ''.join(f'<span><i style="background:var(--s{slot})"></i>{esc(lg["label"])} &middot; {esc(lg["group"])}'
                     f' &middot; {esc(lg["task"])}</span>' for _, slot, lg in names)

    def series(metric):
        out = []
        for name, slot, lg in names:
            pts = []
            for r in lg['levels']:
                y, tip = metric(r)
                pts.append((r['agents'], y, f'{name}, {r["agents"]} agents: {tip}'))
            out.append((name, slot, pts))
        return out

    def aggregate(r):
        if r['agg_finished'] is not None:
            return r['agg_finished'], (f'{r["agg_finished"]:.0f} tok/s without {r["timed_out"]} timed-out run(s)'
                                       f' ({r["agg"]:.0f} with them)')
        return r['agg'], f'{r["agg"]:.0f} tok/s' if r['agg'] is not None else 'no data'

    def per_agent(r):
        m = mean(r['tps'])
        return m, f'{m:.1f} tok/s per agent' if m is not None else 'no data'

    def per_request(r):
        m = mean(r['s_req'])
        return m, f'{m:.1f} s per model request' if m is not None else 'no data'

    charts = ''.join([
        line_chart('Aggregate output', 'tokens per second, all agents together, over the level\'s wall clock',
                   series(aggregate), xs),
        line_chart('Output per agent', 'tokens per second per agent, model time only', series(per_agent), xs),
        line_chart('Time per model request', 'seconds, mean over the agents', series(per_request), xs)])
    tables = []
    for _, slot, lg in names:
        rows = [[r['agents'], f"{r['gates']}/{r['n']}", mean_range(r['auto']), mean_range(r['wall']),
                 mean_range(r['s_req']), mean_range(r['tps']), num(r['agg'], 0), num(r['agg_finished'], 0),
                 r['unbacked'], r['timed_out']] for r in lg['levels']]
        tables.append(f'<h3><i style="display:inline-block;width:12px;height:12px;border-radius:3px;'
                      f'background:var(--s{slot});margin-right:6px"></i>{esc(lg["group"])}: {esc(lg["label"])},'
                      f' {esc(lg["task"])}</h3>'
                      + simple_table(['agents', 'gates', 'auto /10', 'wall min', 's per request', 'out tok/s per agent',
                                      'aggregate out tok/s', 'without timed out', 'unbacked claims', 'timed out'], rows))
    return ('<h2 id="load">Load tests</h2><p class="note">How the setup copes with several agents at once (the work is graded'
            ' automatically only). A run that timed out stretches its level\'s wall clock, so the aggregate chart'
            ' leaves such runs out; the tables show both. Groups may differ in hardware as well as model: check'
            ' each model\'s server report in <code>results/</code>.</p>'
            f'<div class="legend">{legend}</div><div class="charts">{charts}</div>' + ''.join(tables))


def render(data, loads, pending, n_runs, overrides=()):
    """The whole page."""
    now = datetime.datetime.now()
    sections = []
    if data['models']:
        sections += [totals_section(data), ranking_section(data), model_sections(data), telemetry_section(data)]
    sections.append(load_section(loads))
    if overrides:
        items = ''.join(f'<li><code>{esc(d)}</code>: {esc(why)}</li>' for d, why in overrides)
        sections.append('<h2>Gate overrides</h2><p class="note">Runs that failed a gate the reviewer judged wrong for'
                        f' them; scored as passed.</p><ul>{items}</ul>')
    if pending:
        items = ''.join(f'<li><code>{esc(d)}</code>: {esc(", ".join(p))}</li>' for d, p in pending)
        sections.append(f'<h2>Needs a human score</h2><p class="note"><code>bench/review.py next</code> (or'
                        f' <code>--blind</code>) walks through these.</p><ul class="pending">{items}</ul>')
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>Bench report</title>'
            f'<style>{CSS}</style></head><body><main><h1>Bench report</h1>'
            f'<p class="sub">Generated {now:%Y-%m-%d %H:%M}. {n_runs} runs in the model tables, {len(pending)} still'
            f' need a human score.</p>' + '\n'.join(sections) + '</main></body></html>\n')
