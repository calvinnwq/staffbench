"""Static HTML and SVG charts for a report page.

No scripts: every position is computed here from the summary in stats.summarize().
The scatter labels are placed by a small deterministic search so they never overlap
each other or a dot (see place_labels).
"""
from __future__ import annotations

import html
import math

E = html.escape


class LayoutError(ValueError):
    """A chart that cannot be drawn without overlapping marks."""


def pct(value: float, digits: int = 1) -> str:
    return f"{value * 100:.{digits}f}%"


def span(lo: float, hi: float) -> str:
    return f"{lo * 100:.0f}&ndash;{hi * 100:.0f}"


def rate_axis_max(highs: list[float], headroom: float) -> float:
    """Smallest tenth at or above the highest range end plus headroom, within (0.1, 1.0]."""
    top = math.ceil(round((max(highs) + headroom) * 10, 9)) / 10
    return min(1.0, max(0.1, top))


# ---------- 01 forest plot ----------

def forest(s: dict, catches: dict[str, str]) -> str:
    models = s["models"]
    ax = rate_axis_max([m["ci"][1] for m in models], 0.05)

    def xp(v: float) -> str:
        return f"{v / ax * 100:.2f}%"

    steps = range(0, round(ax * 100) + 1, 10)
    ticks = "".join(f'<span class="tick" style="left:{xp(t / 100)}">{t}%</span>' for t in steps)
    grid = "".join(f'<span class="gl" style="left:{xp(t / 100)}"></span>' for t in steps)
    first = [m for m in models if m["band"] == 1]
    rest = [m for m in models if m["band"] != 1]
    groups = [("Statistically tied for first" if len(first) > 1 else "Clear leader", "grp", "t1", first)]
    if rest:
        label = "Clearly behind the leader" if s["behind_leader"] else "Clearly behind at least one model"
        groups.append((label, "grp grp-2", "t2", rest))
    rows = []
    for label, cls, tier, members in groups:
        rows.append(f'<li class="{cls}">{label}</li>')
        for m in members:
            lo, hi = m["ci"]
            catch = f'<span class="tag">{E(catches[m["name"]])}</span>' if m["name"] in catches else ""
            prov = '<span class="tag tag-prov">provisional</span>' if m["status"] == "provisional" else ""
            rows.append(f'''
      <li class="fr {tier}">
        <span class="fr-rank">{m["rank"]}</span>
        <span class="fr-name"><b>{E(m["name"])}</b>{catch}{prov}</span>
        <span class="fr-track" aria-hidden="true">{grid}
          <span class="ci" style="left:{xp(lo)};width:{xp(hi - lo)}"></span>
          <span class="pt" style="left:{xp(m["rate"])}"></span>
        </span>
        <span class="fr-val"><b>{pct(m["rate"])}</b><small>{span(lo, hi)}</small></span>
      </li>''')
    return f'''
  <figure class="forest" aria-labelledby="forest-cap">
    <div class="fr fr-head" aria-hidden="true"><span class="fr-rank">Rank</span><span class="fr-name">Model</span>
      <span class="fr-track axis">{ticks}</span><span class="fr-val">Pass<small>95% range</small></span></div>
    <ol class="fr-list">{"".join(rows)}</ol>
    <figcaption id="forest-cap"><span class="key"><i class="k-pt"></i>observed pass rate</span>
      <span class="key"><i class="k-ci"></i>plausible range (95%)</span>
      Ranges come from resampling the {len(s["task_ids"])} tasks {s["draws"]:,} times. A model is tied for first when no other model is clearly ahead of it.</figcaption>
  </figure>'''


# ---------- 02 model by task grid ----------

def matrix(s: dict, roles: list[dict], titles: dict[str, str], root: str) -> str:
    runs, task_count = s["runs"], len(s["task_ids"])
    totals = {t: (k, n) for t, k, n in s["task_totals"]}
    groups = "".join(
        f'<th scope="colgroup" colspan="{len(r["task_ids"])}" class="pg"><a href="{root}roles/#{E(r["id"])}">{E(r["tag"])}</a></th>'
        for r in roles)
    heads = []
    for t in s["task_ids"]:
        k, n = totals[t]
        heads.append(f'<th scope="col" class="tc{" none" if k == 0 else ""}" title="{E(t)}: {E(titles[t])}. '
                     f'All models: {k} of {n} runs passed."><span>{E(t.rsplit("-", 1)[-1])}</span></th>')
    rows = []
    for m in s["models"]:
        cells = []
        for t, k, n in m["cells"]:
            pips = "".join(f'<i class="{"on" if i < k else "off"}"></i>' for i in range(n)) + '<i class="na"></i>' * (runs - n)
            cls = f'c c{min(k, 3)}{" short" if n < runs else ""}{" none" if totals[t][0] == 0 else ""}'
            cells.append(f'<td class="{cls}" title="{E(m["name"])} on {E(t)}: passed {k} of {n} runs. {E(titles[t])}">'
                         f'<span class="pips">{pips}</span><span class="sr">{k} of {n}</span></td>')
        rows.append(f'<tr><th scope="row">{E(m["name"])}</th>{"".join(cells)}'
                    f'<td class="rel"><b>{m["every"]}</b><small>/{task_count}</small></td></tr>')
    foot = "".join(f'<td class="tot{" none" if totals[t][0] == 0 else ""}">{totals[t][0]}<small>/{totals[t][1]}</small></td>'
                   for t in s["task_ids"])
    return f'''
  <div class="matrix-wrap" role="region" aria-label="Pass results by model and task" tabindex="0">
    <table class="matrix">
      <caption class="sr">Full passes out of {runs} runs for every model and task. Rows are ranked by overall pass rate.</caption>
      <thead><tr><td class="corner"></td>{groups}<td class="corner"></td></tr>
      <tr><th scope="col" class="corner-l">Task number</th>{"".join(heads)}<th scope="col" class="relh">Every run</th></tr></thead>
      <tbody>{"".join(rows)}</tbody>
      <tfoot><tr><th scope="row">All models</th>{foot}<td></td></tr></tfoot>
    </table>
  </div>
  <p class="legend-line"><span class="key"><i class="pip on"></i>passed run</span><span class="key"><i class="pip off"></i>failed run</span>
  <span class="key"><i class="pip na"></i>no scored run</span><span class="key"><i class="k-none"></i>no model passed any run</span></p>'''


# ---------- 03 reliability dumbbell ----------

def dumbbell(s: dict) -> str:
    total, runs = len(s["task_ids"]), s["runs"]

    def xp(v: int) -> str:
        return f"{v / total * 100:.2f}%"

    rows = []
    for m in sorted(s["models"], key=lambda x: (-x["every"], -x["any"], x["rank"])):
        a, e = m["any"], m["every"]
        rows.append(f'''
      <li class="db">
        <span class="db-name">{E(m["name"])}</span>
        <span class="db-track" aria-hidden="true">
          <span class="db-line" style="left:{xp(e)};width:{xp(a - e)}"></span>
          <span class="db-e" style="left:{xp(e)}"></span>
          <span class="db-a" style="left:{xp(a)}"></span>
        </span>
        <span class="db-val"><b>{e}</b> every time <small>&middot; {a} at least once</small></span>
      </li>''')
    step = max(1, math.ceil(total / 6))
    marks = sorted(set(range(0, total + 1, step)) | {total})
    ticks = "".join(f'<span class="tick" style="left:{xp(t)}">{t}</span>' for t in marks)
    return f'''
  <figure class="dumbbell" aria-labelledby="db-cap">
    <div class="db db-head" aria-hidden="true"><span class="db-name"></span><span class="db-track axis">{ticks}</span><span class="db-val">of {total} tasks</span></div>
    <ol>{"".join(rows)}</ol>
    <figcaption id="db-cap"><span class="key"><i class="k-e"></i>passed all {runs} runs</span><span class="key"><i class="k-a"></i>passed at least 1 run</span>
    The gap between the two dots is luck. A wide gap means the model can do the task but does not do it reliably.</figcaption>
  </figure>'''


# ---------- 04 speed against score scatter ----------

W, H, PAD_L, PAD_R, PAD_T, PAD_B = 920, 460, 64, 24, 24, 56
FONT = 12.5          # label font size in SVG units (matches .scatter .lb)
DOT_R = 9.0          # dot radius plus its stroke halo
GAP = 2.0            # minimum clearance between a label and anything it must avoid

# Approximate advance widths (per 1000 em) of a bold grotesque; scaled up by SAFETY so the
# estimate errs wide for every font in the --sans stack.
_ADV = dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ",
                [722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833,
                 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611]))
_ADV.update(zip("abcdefghijklmnopqrstuvwxyz",
                [556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889,
                 611, 611, 611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500]))
_ADV.update({c: 556 for c in "0123456789"})
_ADV.update({" ": 278, ".": 278, ",": 278, "-": 333, "(": 333, ")": 333, ":": 333, "'": 238, "&": 722, "/": 278, "+": 584})
SAFETY = 1.08


def text_width(text: str) -> float:
    return sum(_ADV.get(ch, 667) for ch in text) / 1000 * FONT * SAFETY


# Candidate label positions: (anchor, dx, baseline dy, preference cost, needs leader line).
_NEAR = [("start", 12, 4.5, 0), ("end", -12, 4.5, 1), ("start", 10, -9, 2), ("start", 10, 17, 2),
         ("end", -10, -9, 3), ("end", -10, 17, 3), ("middle", 0, -14, 4), ("middle", 0, 24, 4)]
_FAR = [(anchor, dx, dy, 20 + abs(dy) / 4) for dy in (-28, 34, -42, 48, -56, 62)
        for anchor, dx in (("start", 16), ("end", -16))]
CANDIDATES = [(a, dx, dy, c, False) for a, dx, dy, c in _NEAR] + [(a, dx, dy, c, True) for a, dx, dy, c in _FAR]


def _box(x: float, baseline: float, anchor: str, width: float) -> tuple[float, float, float, float]:
    x0 = {"start": x, "end": x - width, "middle": x - width / 2}[anchor]
    return (x0, baseline - 0.76 * FONT, x0 + width, baseline + 0.24 * FONT)


def _boxes_overlap(a, b, pad: float = GAP) -> bool:
    return a[0] < b[2] + pad and b[0] < a[2] + pad and a[1] < b[3] + pad and b[1] < a[3] + pad


def _box_hits_circle(box, cx: float, cy: float, r: float) -> bool:
    nx, ny = min(max(cx, box[0]), box[2]), min(max(cy, box[1]), box[3])
    return (nx - cx) ** 2 + (ny - cy) ** 2 < r * r


def _segment_hits_box(p, q, box, pad: float = 1.0) -> bool:
    steps = max(1, int(math.hypot(q[0] - p[0], q[1] - p[1]) // 2))
    for i in range(steps + 1):
        x = p[0] + (q[0] - p[0]) * i / steps
        y = p[1] + (q[1] - p[1]) * i / steps
        if box[0] - pad <= x <= box[2] + pad and box[1] - pad <= y <= box[3] + pad:
            return True
    return False


def place_labels(points: list[dict], bounds: tuple[float, float, float, float],
                 lines: list[tuple[tuple[float, float], tuple[float, float]]] = ()) -> list[dict]:
    """Choose a label position for every point.

    points: dicts with x, y, label and optional whisker (y_top, y_bottom).
    bounds: (x0, y0, x1, y1) that every label box must stay inside.
    lines: segments a label should prefer not to cross (the frontier line).
    Hard rules: a label never overlaps another label or any dot and stays inside bounds.
    Soft costs: preferred side, crossing whiskers or lines, distance (far spots get a leader line).
    Several deterministic orders are tried; the cheapest complete layout wins.
    Returns, in input order, dicts with x, y, anchor, box, leader (bool) and ok (False only when
    no position satisfied the hard rules).
    """
    widths = [text_width(p["label"]) for p in points]

    def spot_cost(i, cand, placed):
        anchor, dx, dy, pref, far = cand
        p = points[i]
        box = _box(p["x"] + dx, p["y"] + dy, anchor, widths[i])
        hard = 0
        if box[0] < bounds[0] or box[1] < bounds[1] or box[2] > bounds[2] or box[3] > bounds[3]:
            hard += 1
        hard += sum(1 for q in points if _box_hits_circle(box, q["x"], q["y"], DOT_R + GAP))
        hard += sum(1 for other in placed if _boxes_overlap(box, other))
        soft = pref
        for q in points:
            top, bottom = q.get("whisker", (q["y"], q["y"]))
            if box[0] - 1 < q["x"] < box[2] + 1 and top < box[3] and bottom > box[1]:
                soft += 3
        soft += sum(6 for a, b in lines if _segment_hits_box(a, b, box))
        return hard, soft, box

    def layout(order):
        placed, chosen, total, failures = [], {}, 0.0, 0
        for i in order:
            best = None
            for cand in CANDIDATES:
                hard, soft, box = spot_cost(i, cand, placed)
                key = (hard, soft)
                if best is None or key < best[0]:
                    best = (key, cand, box)
            (hard, soft), cand, box = best
            failures += hard > 0
            total += soft
            placed.append(box)
            chosen[i] = (cand, box, hard == 0)
        return (failures, total), chosen

    n = len(points)
    crowd = [sum(1 for q in points if abs(q["x"] - p["x"]) < 140 and abs(q["y"] - p["y"]) < 40) for p in points]
    orders = [list(range(n)),
              sorted(range(n), key=lambda i: (points[i]["x"], i)),
              sorted(range(n), key=lambda i: (-points[i]["x"], i)),
              sorted(range(n), key=lambda i: (points[i]["y"], i)),
              sorted(range(n), key=lambda i: (-crowd[i], i))]
    best = None
    for order in orders:
        score, chosen = layout(order)
        if best is None or score < best[0]:
            best = (score, chosen)
    out = []
    for i, p in enumerate(points):
        (anchor, dx, dy, _, far), box, ok = best[1][i]
        out.append({"x": p["x"] + dx, "y": p["y"] + dy, "anchor": anchor, "box": box, "leader": far, "ok": ok})
    return out


def _doubling_ticks(lo: float, hi: float) -> list[float]:
    low, high = lo / 1.15, hi * 1.15
    exp = math.floor(math.log10(low))
    start = max(c * 10 ** exp for c in (1, 2, 2.5, 5) if c * 10 ** exp <= low)
    ticks = [start]
    while ticks[-1] < high:
        ticks.append(ticks[-1] * 2)
    return ticks


def scatter(s: dict) -> str:
    timed = [m for m in s["models"] if m["latency_s"] is not None]
    if not timed:
        return '<p class="sec-empty">No model in these results has timing data.</p>'
    ticks = _doubling_ticks(min(m["latency_s"] for m in timed), max(m["latency_s"] for m in timed))
    xmin, xmax = math.log10(ticks[0]), math.log10(ticks[-1])
    ymax = rate_axis_max([m["ci"][1] for m in timed], 0.0)

    def sx(sec: float) -> float:
        return PAD_L + (math.log10(sec) - xmin) / (xmax - xmin) * (W - PAD_L - PAD_R)

    def sy(v: float) -> float:
        return PAD_T + (1 - v / ymax) * (H - PAD_T - PAD_B)

    front = s["frontier"]
    on = {m["id"] for m in front}
    names = [m["name"] for m in front]
    desc = (f"{names[0]} alone forms the speed frontier." if len(names) == 1
            else f"{join_and(names)} form the speed frontier.")
    svg = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-labelledby="sc-t sc-d" class="scatter">'
           '<title id="sc-t">Pass rate against median time per task</title>'
           f'<desc id="sc-d">Each dot is a model. Up is better, left is faster. {E(desc)}</desc>']
    for v in range(0, round(ymax * 100) + 1, 10):
        y = sy(v / 100)
        svg.append(f'<line class="g" x1="{PAD_L}" x2="{W - PAD_R}" y1="{y:.1f}" y2="{y:.1f}"/>'
                   f'<text class="tk" x="{PAD_L - 10}" y="{y + 4:.1f}" text-anchor="end">{v}%</text>')
    for t in ticks:
        x = sx(t)
        svg.append(f'<line class="g" x1="{x:.1f}" x2="{x:.1f}" y1="{PAD_T}" y2="{H - PAD_B}"/>'
                   f'<text class="tk" x="{x:.1f}" y="{H - PAD_B + 22}" text-anchor="middle">{t:g}s</text>')
    svg.append(f'<text class="ax" x="{(PAD_L + W - PAD_R) / 2:g}" y="{H - 8}" text-anchor="middle">median time per task (log scale) &#8594; slower</text>')
    path = [(sx(m["latency_s"]), sy(m["rate"])) for m in front]
    svg.append('<polyline class="fr-line" points="' + " ".join(f"{x:.1f},{y:.1f}" for x, y in path) + '"/>')
    points = [{"x": sx(m["latency_s"]), "y": sy(m["rate"]), "label": m["name"],
               "whisker": (sy(m["ci"][1]), sy(m["ci"][0]))} for m in timed]
    bounds = (PAD_L + 4, 4, W - 4, H - PAD_B - 2)
    labels = place_labels(points, bounds, list(zip(path, path[1:])))
    crowded = [m["name"] for m, lb in zip(timed, labels) if not lb["ok"]]
    if crowded:
        raise LayoutError(f"scatter labels cannot be placed without overlap: {crowded}")
    for m, p, lb in zip(timed, points, labels):
        x, y = p["x"], p["y"]
        leader = ""
        if lb["leader"]:
            box = lb["box"]
            nx, ny = min(max(x, box[0]), box[2]), min(max(y, box[1]), box[3])
            d = math.hypot(nx - x, ny - y)
            ux, uy = (nx - x) / d, (ny - y) / d
            leader = (f'<line class="ld" x1="{x + ux * DOT_R:.1f}" y1="{y + uy * DOT_R:.1f}" '
                      f'x2="{nx - ux * 2:.1f}" y2="{ny - uy * 2:.1f}"/>')
        svg.append(f'<g class="pt-g {"on" if m["id"] in on else "off"}"><title>{E(m["name"])}: {pct(m["rate"])} pass rate, '
                   f'{m["latency_s"]:.1f}s median per task</title>'
                   f'<line class="wh" x1="{x:.1f}" x2="{x:.1f}" y1="{p["whisker"][0]:.1f}" y2="{p["whisker"][1]:.1f}"/>'
                   f'{leader}<circle cx="{x:.1f}" cy="{y:.1f}" r="7"/>'
                   f'<text class="lb" x="{lb["x"]:.1f}" y="{lb["y"]:.1f}" text-anchor="{lb["anchor"]}">{E(m["name"])}</text></g>')
    svg.append(f'<text class="ax" transform="translate(16 {(PAD_T + H - PAD_B) / 2:g}) rotate(-90)" text-anchor="middle">full pass rate</text></svg>')
    return f'''
  <figure class="scatter-fig" aria-labelledby="sc-cap">
    <div class="scatter-wrap" role="region" aria-label="Speed against score chart" tabindex="0">{"".join(svg)}</div>
    <figcaption id="sc-cap"><span class="key"><i class="k-on"></i>on the frontier</span><span class="key"><i class="k-off"></i>beaten by a faster, higher model</span>
    <span class="key"><i class="k-wh"></i>95% range</span>Time is each model's median per task.</figcaption>
  </figure>'''


def join_and(items: list[str]) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]
