/* MS Factory — dashboard.
 * A dedicated, read-only screen. Every figure is derived from live state
 * (S.docs, S.accts, S.L, S.items, S.mv, S.po); nothing is stored or written back.
 */
(function () {
  'use strict';

  const L = (ar, en) => (typeof LANGEN !== 'undefined' && LANGEN ? en : ar);
  const esc = (v) =>
    String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const nf = (v) => Number(v || 0).toLocaleString('en-US', { maximumFractionDigits: 0 });
  const qf = (v) => Number(v || 0).toLocaleString('en-US', { maximumFractionDigits: 3 });
  const mf = (v) =>
    Number(v || 0).toLocaleString('en-US', { maximumFractionDigits: 2 });
  const short = (v) => {
    const n = Math.abs(Number(v) || 0);
    if (n >= 1e6) return (Number(v) / 1e6).toFixed(1) + L(' م', 'M');
    if (n >= 1e3) return (Number(v) / 1e3).toFixed(1) + L(' ألف', 'k');
    return nf(v);
  };
  const monthsAr = ['يناير', 'فبراير', 'مارس', 'أبريل', 'مايو', 'يونيو', 'يوليو', 'أغسطس', 'سبتمبر', 'أكتوبر', 'نوفمبر', 'ديسمبر'];
  const monthsEn = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const monthName = (i) => (typeof LANGEN !== 'undefined' && LANGEN ? monthsEn[i] : monthsAr[i]);

  const hideNumbers = () => {
    try {
      return !!JSON.parse(localStorage.getItem('ms-factory-preview-ui-v1') || '{}').hideNumbers;
    } catch (_) {
      return false;
    }
  };

  const today = () => {
    if (typeof td === 'function') return td();
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  };
  const dayFmt = (v) => (typeof dfm === 'function' ? dfm(v) : v);
  const periodEnd = () => (typeof S !== 'undefined' && S.per && S.per.to) || today();
  const periodStart = () => (typeof S !== 'undefined' && S.per && S.per.from) || '';

  /** Monthly buckets ending with the month of the selected period. */
  function monthBuckets(count) {
    const end = new Date(periodEnd() + 'T12:00:00');
    const out = [];
    for (let i = count - 1; i >= 0; i--) {
      const first = new Date(end.getFullYear(), end.getMonth() - i, 1);
      const last = new Date(first.getFullYear(), first.getMonth() + 1, 0);
      const key = `${first.getFullYear()}-${String(first.getMonth() + 1).padStart(2, '0')}`;
      out.push({
        key,
        label: monthName(first.getMonth()),
        year: first.getFullYear(),
        from: key + '-01',
        to: `${last.getFullYear()}-${String(last.getMonth() + 1).padStart(2, '0')}-${String(last.getDate()).padStart(2, '0')}`,
      });
    }
    return out;
  }

  const docs = () => (typeof S !== 'undefined' && S.docs) || [];
  const accts = () => (typeof S !== 'undefined' && S.accts) || [];
  const ledger = () => (typeof S !== 'undefined' && S.L) || [];
  const movements = () => (typeof S !== 'undefined' && S.mv) || [];
  const workOrders = () => (typeof S !== 'undefined' && S.po) || [];
  const warehouses = () => (typeof S !== 'undefined' && S.wh) || [];
  const items = () => (typeof S !== 'undefined' && S.items) || [];
  const partners = () => (typeof S !== 'undefined' && S.partners) || [];

  /** Sum of a document type's totals inside a date window. */
  function docTotal(type, from, to) {
    return docs()
      .filter((d) => d.t === type && (!from || d.date >= from) && (!to || d.date <= to))
      .reduce((sum, d) => sum + (Number(d.total) || 0), 0);
  }

  function childrenOf(id) {
    return accts().filter((a) => String(a.parentId || '') === String(id));
  }

  /** Balance of an account plus every descendant, within a date window. */
  function subtree(id, from, to, seen) {
    seen = seen || new Set();
    const key = String(id);
    if (seen.has(key)) return 0;
    seen.add(key);
    const own = ledger()
      .filter((l) => String(l.a) === key && (!from || l.d >= from) && (!to || l.d <= to))
      .reduce((s, l) => s + (Number(l.dr) || 0) - (Number(l.cr) || 0), 0);
    const acct = accts().find((a) => String(a.id) === key);
    const sign = acct && 'AX'.includes(acct.t) ? 1 : -1;
    const kids = childrenOf(id).reduce((s, k) => s + subtree(k.id, from, to, seen), 0);
    return own * sign + kids;
  }

  function hasCashAncestor(a) {
    let parent = a.parentId;
    const seen = new Set();
    while (parent) {
      const node = accts().find((x) => String(x.id) === String(parent));
      if (!node || seen.has(String(node.id))) return false;
      if (node.cash) return true;
      seen.add(String(node.id));
      parent = node.parentId;
    }
    return false;
  }

  function cashAt(to) {
    return accts()
      .filter((a) => a.cash && !hasCashAncestor(a))
      .reduce((s, a) => s + subtree(a.id, '', to), 0);
  }

  /* ---------- warehouse classes ---------- */

  const WH_CLASSES = [
    { key: 'finished', label: () => L('منتجات نهائية', 'Finished goods'), re: /منتج|نهائي|تام|finished|final|product/i },
    { key: 'raw', label: () => L('مواد خام', 'Raw materials'), re: /خام|raw|material/i },
    { key: 'supplies', label: () => L('مستلزمات', 'Supplies'), re: /مستلزم|مهمات|suppl|consum/i },
  ];

  /** Classify a warehouse: the stored kind wins, names are only a fallback. */
  function warehouseClass(id) {
    const byId = new Map(warehouses().map((w) => [String(w.id), w]));
    let node = byId.get(String(id));
    const seen = new Set();
    while (node && !seen.has(String(node.id))) {
      seen.add(String(node.id));
      if (WH_CLASSES.some((c) => c.key === node.kind)) return node.kind;
      const name = String(node.name || '');
      for (const cls of WH_CLASSES) if (cls.re.test(name)) return cls.key;
      node = byId.get(String(node.parentId ?? ''));
    }
    return 'other';
  }

  function warehouseLabel(id) {
    const w = warehouses().find((x) => String(x.id) === String(id));
    return w ? w.name : '';
  }

  /* ---------- chart primitives ---------- */

  function legend(series) {
    return (
      '<div class="dash-legend">' +
      series
        .map((s) => `<span><i style="background:${s.color}"></i>${esc(s.label)}</span>`)
        .join('') +
      '</div>'
    );
  }

  /** Grouped columns, chronological left to right. */
  function columnsChart(buckets, series, ariaLabel) {
    const W = 340, H = 168, padL = 6, padR = 6, padT = 12, padB = 26;
    const innerW = W - padL - padR, innerH = H - padT - padB;
    const values = series.flatMap((s) => s.values);
    const max = Math.max(1, ...values.map((v) => Math.abs(v)));
    const gw = innerW / Math.max(1, buckets.length);
    const barW = Math.min(14, Math.max(4, (gw - 8) / series.length));
    const showValues = !hideNumbers();

    let grid = '';
    for (let i = 1; i <= 3; i++) {
      const y = padT + innerH - (innerH * i) / 3;
      grid += `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${W - padR}" y2="${y.toFixed(1)}" class="dash-grid"/>`;
    }

    let bars = '';
    buckets.forEach((b, bi) => {
      const groupX = padL + bi * gw + gw / 2;
      series.forEach((s, si) => {
        const v = Math.abs(Number(s.values[bi]) || 0);
        const h = (v / max) * innerH;
        const x = groupX - (barW * series.length) / 2 + si * barW;
        const y = padT + innerH - h;
        bars +=
          `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${(barW - 1.5).toFixed(1)}" height="${Math.max(0, h).toFixed(1)}" rx="2" fill="${s.color}"><title>${esc(b.label)} ${b.year} · ${esc(s.label)}: ${esc(mf(s.values[bi]))}</title></rect>`;
        if (showValues && v > 0 && si === 0) {
          bars += `<text x="${(groupX).toFixed(1)}" y="${(y - 3).toFixed(1)}" class="dash-val" text-anchor="middle">${esc(short(v))}</text>`;
        }
      });
      bars += `<text x="${groupX.toFixed(1)}" y="${H - 8}" class="dash-tick" text-anchor="middle">${esc(b.label)}</text>`;
    });

    return (
      `<svg class="dash-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(ariaLabel)}">` +
      grid +
      `<line x1="${padL}" y1="${padT + innerH}" x2="${W - padR}" y2="${padT + innerH}" class="dash-axis"/>` +
      bars +
      '</svg>'
    );
  }

  /** Single series area line. */
  function areaChart(buckets, values, ariaLabel, color) {
    const W = 340, H = 150, padL = 8, padR = 8, padT = 14, padB = 26;
    const innerW = W - padL - padR, innerH = H - padT - padB;
    const nums = values.map((v) => Number(v) || 0);
    const max = Math.max(1, ...nums);
    const min = Math.min(0, ...nums);
    const span = max - min || 1;
    const stepX = buckets.length > 1 ? innerW / (buckets.length - 1) : 0;
    const point = (v, i) => [
      padL + i * stepX,
      padT + innerH - ((Number(v) || 0) - min) / span * innerH,
    ];

    const pts = nums.map((v, i) => point(v, i));
    const line = pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
    const area = `${line} L${pts[pts.length - 1][0].toFixed(1)},${(padT + innerH).toFixed(1)} L${pts[0][0].toFixed(1)},${(padT + innerH).toFixed(1)} Z`;

    let grid = '';
    for (let i = 0; i <= 2; i++) {
      const y = padT + (innerH * i) / 2;
      grid += `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${W - padR}" y2="${y.toFixed(1)}" class="dash-grid"/>`;
    }

    const dots = pts
      .map(
        ([x, y], i) =>
          `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${i === pts.length - 1 ? 3.6 : 2.2}" fill="${color}"><title>${esc(buckets[i].label)} ${buckets[i].year}: ${esc(mf(nums[i]))}</title></circle>`,
      )
      .join('');
    const ticks = buckets
      .map((b, i) => {
        const [x] = point(nums[i], i);
        return `<text x="${x.toFixed(1)}" y="${H - 8}" class="dash-tick" text-anchor="middle">${esc(b.label)}</text>`;
      })
      .join('');

    return (
      `<svg class="dash-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(ariaLabel)}">` +
      grid +
      `<path d="${area}" fill="${color}" opacity=".14"/>` +
      `<path d="${line}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round"/>` +
      dots +
      ticks +
      '</svg>'
    );
  }

  /** Multi-series trend lines. A trend over time reads better as lines than
      grouped columns, which collide once three series share six months. */
  function linesChart(buckets, series, ariaLabel) {
    const W = 340, H = 168, padL = 8, padR = 8, padT = 16, padB = 26;
    const innerW = W - padL - padR, innerH = H - padT - padB;
    const all = series.flatMap((s) => s.values.map((v) => Number(v) || 0));
    const max = Math.max(1, ...all);
    const stepX = buckets.length > 1 ? innerW / (buckets.length - 1) : 0;
    const xAt = (i) => padL + i * stepX;
    const yAt = (v) => padT + innerH - ((Number(v) || 0) / max) * innerH;

    let grid = '';
    for (let i = 0; i <= 3; i++) {
      const y = padT + (innerH * i) / 3;
      grid += `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${W - padR}" y2="${y.toFixed(1)}" class="dash-grid"/>`;
    }

    let paths = '';
    series.forEach((s) => {
      const pts = s.values.map((v, i) => [xAt(i), yAt(v)]);
      const d = pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
      const last = pts[pts.length - 1];
      paths +=
        `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>` +
        pts
          .map(
            ([x, y], i) =>
              `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${i === pts.length - 1 ? 3.4 : 2.1}" fill="${s.color}"><title>${esc(buckets[i].label)} ${buckets[i].year} · ${esc(s.label)}: ${esc(mf(s.values[i]))}</title></circle>`,
          )
          .join('');
      if (last) paths += '';
    });

    const ticks = buckets
      .map((b, i) => `<text x="${xAt(i).toFixed(1)}" y="${H - 8}" class="dash-tick" text-anchor="middle">${esc(b.label)}</text>`)
      .join('');

    return (
      `<svg class="dash-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(ariaLabel)}">` +
      grid +
      paths +
      ticks +
      '</svg>'
    );
  }

  /** Donut for parts of one whole. Percentages are printed inside the ring so
      the split is readable without hovering. */
  function donutChart(rows, total, ariaLabel, centerLabel, format) {
    const fmt = format || mf;
    const list = rows.filter((r) => Math.abs(Number(r.value) || 0) > 0.005);
    if (!list.length) return `<p class="dash-empty">${esc(L('لا توجد بيانات في الفترة المحددة', 'No data for the selected period'))}</p>`;
    const sum = list.reduce((a, r) => a + Math.abs(Number(r.value) || 0), 0) || 1;
    const R = 54, r0 = 34, cx = 66, cy = 66;
    const palette = ['var(--chart-1)', 'var(--chart-2)', 'var(--chart-3)', 'var(--chart-4)', 'var(--chart-5)', 'var(--chart-6)'];
    const pt = (r, a) => [cx + r * Math.cos(a), cy + r * Math.sin(a)];
    let acc = -Math.PI / 2;
    let arcs = '';
    list.forEach((row, i) => {
      const frac = Math.abs(Number(row.value) || 0) / sum;
      const a0 = acc, a1 = acc + frac * Math.PI * 2;
      acc = a1;
      const large = frac > 0.5 ? 1 : 0;
      const [x0, y0] = pt(R, a0), [x1, y1] = pt(R, a1);
      const [x2, y2] = pt(r0, a1), [x3, y3] = pt(r0, a0);
      const color = palette[i % palette.length];
      const pct = Math.round(frac * 100);
      arcs +=
        `<path d="M${x0.toFixed(2)},${y0.toFixed(2)} A${R},${R} 0 ${large} 1 ${x1.toFixed(2)},${y1.toFixed(2)} ` +
        `L${x2.toFixed(2)},${y2.toFixed(2)} A${r0},${r0} 0 ${large} 0 ${x3.toFixed(2)},${y3.toFixed(2)} Z" ` +
        `fill="${color}"><title>${esc(row.label)}: ${esc(fmt(row.value))} (${pct}%)</title></path>`;
      if (pct >= 8) {
        const mid = (a0 + a1) / 2, [tx, ty] = pt((R + r0) / 2, mid);
        arcs += `<text x="${tx.toFixed(1)}" y="${ty.toFixed(1)}" class="dash-donut-pct" text-anchor="middle" dominant-baseline="central">${pct}%</text>`;
      }
    });
    const legendRows =
      '<ul class="dash-donut-legend">' +
      list
        .map((row, i) => {
          const pct = Math.round((Math.abs(Number(row.value) || 0) / sum) * 100);
          const color = palette[i % palette.length];
          return (
            `<li><i style="background:${color}"></i>` +
            `<span class="dash-donut-name">${esc(row.label)}</span>` +
            `<b dir="ltr">${esc(hideNumbers() ? '—' : fmt(row.value))} <small>${pct}%</small></b></li>`
          );
        })
        .join('') +
      '</ul>';
    return (
      '<div class="dash-donut">' +
      `<svg class="dash-donut-svg" viewBox="0 0 132 132" role="img" aria-label="${esc(ariaLabel)}">` +
      arcs +
      `<text x="${cx}" y="${cy - 5}" class="dash-donut-center" text-anchor="middle" dominant-baseline="central">${esc(hideNumbers() ? '—' : short(total || sum))}</text>` +
      `<text x="${cx}" y="${cy + 14}" class="dash-donut-sub" text-anchor="middle" dominant-baseline="central">${esc(centerLabel || '')}</text>` +
      '</svg>' +
      legendRows +
      '</div>'
    );
  }

  /** Single-series columns for ordered age bands. Each bar carries its own
      colour so overdue severity is visible before reading any number. */
  function agingChart(rows, total, ariaLabel) {
    const W = 340, H = 158, padL = 6, padR = 6, padT = 20, padB = 28;
    const innerW = W - padL - padR, innerH = H - padT - padB;
    const max = Math.max(1, ...rows.map((r) => Math.abs(Number(r.value) || 0)));
    const gw = innerW / Math.max(1, rows.length);
    // Same slim bar profile as the sales chart so the two read alike.
    const barW = Math.min(14, Math.max(6, gw * 0.3));
    const tones = ['var(--sev-1)', 'var(--sev-2)', 'var(--sev-3)', 'var(--sev-4)', 'var(--sev-5)'];

    let grid = '';
    for (let i = 1; i <= 3; i++) {
      const y = padT + innerH - (innerH * i) / 3;
      grid += `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${W - padR}" y2="${y.toFixed(1)}" class="dash-grid"/>`;
    }

    let bars = '';
    rows.forEach((row, i) => {
      const v = Math.abs(Number(row.value) || 0);
      const h = (v / max) * innerH;
      const groupX = padL + i * gw + gw / 2;
      const x = groupX - barW / 2;
      const y = padT + innerH - h;
      const color = tones[i % tones.length];
      bars +=
        `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${Math.max(0, h).toFixed(1)}" rx="3" fill="${color}">` +
        `<title>${esc(row.label)}: ${esc(mf(row.value))}${total ? ` (${Math.round((v / total) * 100)}%)` : ''}</title></rect>`;
      if (v > 0.005) {
        bars += `<text x="${groupX.toFixed(1)}" y="${(y - 4).toFixed(1)}" class="dash-val" text-anchor="middle">${esc(hideNumbers() ? '—' : short(v))}</text>`;
      }
      bars += `<text x="${groupX.toFixed(1)}" y="${H - 9}" class="dash-tick" text-anchor="middle">${esc(row.short || row.label)}</text>`;
    });

    return (
      `<svg class="dash-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(ariaLabel)}">` +
      grid +
      `<line x1="${padL}" y1="${padT + innerH}" x2="${W - padR}" y2="${padT + innerH}" class="dash-axis"/>` +
      bars +
      '</svg>'
    );
  }

  /** Horizontal bars as HTML so Arabic labels wrap and read right to left. */
  function hbars(rows, total, format) {
    if (!rows.length) return `<p class="dash-empty">${esc(L('لا توجد بيانات في الفترة المحددة', 'No data for the selected period'))}</p>`;
    const fmt = format || mf;
    const max = Math.max(1, ...rows.map((r) => Math.abs(r.value)));
    return (
      '<ul class="dash-hbars">' +
      rows
        .map((r) => {
          const pct = (Math.abs(r.value) / max) * 100;
          const share = total ? Math.round((Math.abs(r.value) / total) * 100) : 0;
          return (
            `<li><div class="dash-hbar-head"><span>${esc(r.label)}</span>` +
            `<b dir="ltr">${esc(hideNumbers() ? '—' : fmt(r.value))}${total ? ` <small>${share}%</small>` : ''}</b></div>` +
            `<span class="dash-track"><i style="width:${pct.toFixed(1)}%"></i></span></li>`
          );
        })
        .join('') +
      '</ul>'
    );
  }

  /* ---------- data assembly ---------- */

  function salesPurchasesSeries() {
    const buckets = monthBuckets(6);
    const manufacturing = buckets.map((b) =>
      movements()
        .filter((m) => m.kind === 'production_receipt' && m.d >= b.from && m.d <= b.to)
        .reduce((s, m) => s + Math.abs(Number(m.val) || 0), 0),
    );
    return {
      buckets,
      series: [
        {
          label: L('المبيعات', 'Sales'),
          color: 'var(--chart-1)',
          values: buckets.map((b) => docTotal('sale', b.from, b.to) - docTotal('sret', b.from, b.to)),
        },
        {
          label: L('المشتريات', 'Purchases'),
          color: 'var(--chart-4)',
          values: buckets.map((b) => docTotal('pur', b.from, b.to) - docTotal('pret', b.from, b.to)),
        },
        { label: L('التصنيع', 'Manufacturing'), color: 'var(--chart-2)', values: manufacturing },
      ],
    };
  }

  /** Purchases in the period, split by the warehouse class of each line. */
  function purchasesByWarehouseClass() {
    const from = periodStart(), to = periodEnd();
    const totals = new Map(WH_CLASSES.map((c) => [c.key, 0]));
    totals.set('other', 0);
    const perWarehouse = new Map();

    docs()
      .filter((d) => d.t === 'pur' && (!from || d.date >= from) && (!to || d.date <= to))
      .forEach((d) =>
        (d.lines || []).forEach((l) => {
          const gross = (Number(l.qty) || 0) * (Number(l.price) || 0);
          if (!gross) return;
          const wh = l.wh || d.warehouseId || 0;
          const cls = warehouseClass(wh);
          totals.set(cls, (totals.get(cls) || 0) + gross);
          perWarehouse.set(String(wh), (perWarehouse.get(String(wh)) || 0) + gross);
        }),
      );

    const groups = [...totals.entries()]
      .filter(([, value]) => Math.abs(value) > 0.005)
      .map(([key, value]) => ({
        label: key === 'other' ? L('مخازن أخرى', 'Other warehouses') : WH_CLASSES.find((c) => c.key === key).label(),
        value,
      }))
      .sort((a, b) => b.value - a.value);
    const total = groups.reduce((s, g) => s + g.value, 0);

    const detail = [...perWarehouse.entries()]
      .map(([id, value]) => ({ label: warehouseLabel(id) || L('بدون مخزن', 'No warehouse'), value }))
      .filter((r) => r.value > 0.005)
      .sort((a, b) => b.value - a.value);

    return { groups, total, detail };
  }

  function cashSeries() {
    const buckets = monthBuckets(6);
    return { buckets, values: buckets.map((b) => cashAt(b.to)) };
  }

  function expenseRows() {
    const from = periodStart(), to = periodEnd();
    const expenseIds = new Set(accts().filter((a) => a.t === 'X').map((a) => String(a.id)));
    // Roots only: a child's total is already inside its parent's subtree sum.
    let nodes = accts().filter((a) => a.t === 'X' && !expenseIds.has(String(a.parentId || '')));
    // A lone root would render one 100% bar, so open it up to its children.
    const rootsWithValue = nodes.filter((a) => Math.abs(subtree(a.id, from, to)) > 0.005);
    if (rootsWithValue.length === 1 && childrenOf(rootsWithValue[0].id).length) {
      nodes = childrenOf(rootsWithValue[0].id);
    }
    const rows = nodes
      .map((a) => ({ label: a.name, value: subtree(a.id, from, to) }))
      .filter((r) => r.value > 0.005)
      .sort((a, b) => b.value - a.value);
    const total = rows.reduce((s, r) => s + r.value, 0);
    const top = rows.slice(0, 5);
    const rest = rows.slice(5).reduce((s, r) => s + r.value, 0);
    if (rest > 0.005) top.push({ label: L('مصروفات أخرى', 'Other expenses'), value: rest });
    return { rows: top, total };
  }

  function topItemRows() {
    const from = periodStart(), to = periodEnd();
    const byItem = new Map();
    docs()
      .filter((d) => d.t === 'sale' && (!from || d.date >= from) && (!to || d.date <= to))
      .forEach((d) =>
        (d.lines || []).forEach((l) => {
          const name = l.name || L('صنف غير محدد', 'Unnamed item');
          const qty = Number(l.qty) || 0;
          if (!qty) return;
          const row = byItem.get(name) || { label: name, value: 0 };
          row.value += qty;
          byItem.set(name, row);
        }),
      );
    return [...byItem.values()].sort((a, b) => b.value - a.value).slice(0, 5);
  }

  /* ---------- KPIs and alerts ---------- */

  function receivables() {
    return partners()
      .filter((p) => p.c)
      .reduce((n, p) => n + Math.max(0, typeof pbal === 'function' ? pbal(p.id) : 0), 0);
  }

  function payables() {
    return partners()
      .filter((p) => p.s)
      .reduce((n, p) => n + Math.max(0, typeof pbal === 'function' ? -pbal(p.id) : 0), 0);
  }

  function remainingOf(d) {
    return typeof remOf === 'function' ? Math.max(0, remOf(d)) : 0;
  }

  function overdueDocs(kind) {
    const now = today();
    return docs().filter((d) => d.t === kind && remainingOf(d) > 0.005 && d.due && d.due < now);
  }

  function lowStockItems() {
    return items().filter((i) => {
      const qty = Object.values(i.st || {}).reduce((s, v) => s + (Number(v) || 0), 0);
      const min = Number(i.min) || 0;
      return min > 0 && qty <= min;
    });
  }

  function kpi(title, value, note, opts) {
    const o = opts || {};
    const tone = o.tone ? ` dash-kpi-${o.tone}` : '';
    const hidden = hideNumbers() ? '—' : value;
    const inner =
      `<span class="dash-kpi-title">${esc(title)}</span>` +
      `<strong class="dash-kpi-value" dir="ltr">${esc(hidden)}${o.suffix ? ` <small>${esc(o.suffix)}</small>` : ''}</strong>` +
      (note ? `<small class="dash-kpi-note">${esc(note)}</small>` : '');
    if (o.screen) {
      return `<button type="button" class="dash-kpi${tone}" data-dash-go="${esc(o.screen)}">${inner}</button>`;
    }
    return `<div class="dash-kpi${tone}">${inner}</div>`;
  }

  /** Preset ranges offered above the dashboard, keyed to a day count. */
  const PRESETS = [
    { id: 'month', ar: 'هذا الشهر', en: 'This month' },
    { id: 'q', ar: 'هذا الربع', en: 'This quarter' },
    { id: 'year', ar: 'هذه السنة', en: 'This year' },
    { id: 'all', ar: 'كل الفترة', en: 'All time' },
  ];

  const isoDay = (d) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

  function presetRange(id) {
    const now = new Date(today() + 'T12:00:00');
    if (id === 'month') return { from: isoDay(new Date(now.getFullYear(), now.getMonth(), 1)), to: isoDay(now) };
    if (id === 'q') {
      const q = Math.floor(now.getMonth() / 3) * 3;
      return { from: isoDay(new Date(now.getFullYear(), q, 1)), to: isoDay(now) };
    }
    if (id === 'year') return { from: isoDay(new Date(now.getFullYear(), 0, 1)), to: isoDay(now) };
    return { from: '', to: isoDay(now) };
  }

  /** Period controls. Any change writes S.per and re-renders the screen. */
  function periodBar() {
    const from = periodStart(),
      to = periodEnd();
    const activePreset = PRESETS.find((p) => {
      const r = presetRange(p.id);
      return r.from === from && r.to === to;
    });
    const chip = (p) =>
      `<button type="button" class="dash-period-chip${activePreset && activePreset.id === p.id ? ' is-on' : ''}" ` +
      `data-period-preset="${p.id}">${esc(L(p.ar, p.en))}</button>`;
    return (
      '<div class="dash-period-bar">' +
      '<div class="dash-period-presets">' +
      PRESETS.map(chip).join('') +
      '</div>' +
      '<label class="dash-period-field"><span>' +
      esc(L('من', 'From')) +
      '</span><input type="date" data-period-from value="' +
      esc(from) +
      '"></label>' +
      '<label class="dash-period-field"><span>' +
      esc(L('إلى', 'To')) +
      '</span><input type="date" data-period-to value="' +
      esc(to) +
      '"></label>' +
      '<button type="button" class="dash-period-reset" data-period-reset>' +
      esc(L('إعادة ضبط', 'Reset')) +
      '</button>' +
      '</div>'
    );
  }

  /** Delegated so the controls keep working after every re-render. */
  function wirePeriodBar() {
    const root = document.getElementById('dash-period-root') || document;
    if (root.__dashPeriodWired) return;
    root.__dashPeriodWired = true;
    root.addEventListener('change', (e) => {
      const fromEl = e.target.closest('[data-period-from]');
      const toEl = e.target.closest('[data-period-to]');
      if (!fromEl && !toEl) return;
      const bar = (fromEl || toEl).closest('.dash-period-bar');
      const next = {
        from: bar.querySelector('[data-period-from]').value,
        to: bar.querySelector('[data-period-to]').value,
      };
      if (next.from && next.to && next.from > next.to) {
        const swap = next.from;
        next.from = next.to;
        next.to = swap;
      }
      setPeriod(next);
    });
    root.addEventListener('click', (e) => {
      const preset = e.target.closest('[data-period-preset]');
      if (preset) {
        setPeriod(presetRange(preset.dataset.periodPreset));
        return;
      }
      if (e.target.closest('[data-period-reset]')) setPeriod(presetRange('year'));
    });
  }

  function setPeriod(next) {
    if (typeof S === 'undefined' || !S) return;
    S.per = { from: next.from || '', to: next.to || today() };
    if (typeof render === 'function') render();
  }

  function kpiGrid() {
    const from = periodStart(), to = periodEnd();
    const sales = docTotal('sale', from, to) - docTotal('sret', from, to);
    const purchases = docTotal('pur', from, to) - docTotal('pret', from, to);
    const manufactured = movements()
      .filter((m) => m.kind === 'production_receipt' && (!from || m.d >= from) && (!to || m.d <= to))
      .reduce((s, m) => s + Math.abs(Number(m.val) || 0), 0);
    const cash = cashAt(to);
    const rec = receivables();
    const pay = payables();
    const salesInv = docs().filter((d) => d.t === 'sale' && (!from || d.date >= from) && (!to || d.date <= to));
    const avgSale = salesInv.length ? sales / salesInv.length : 0;

    return (
      '<section class="dash-kpis" id="factory-dash-kpis">' +
      kpi(L('المبيعات', 'Sales'), mf(sales), L('صافي بعد المرتجعات', 'Net of returns'), { suffix: 'ج.م', screen: 'sale' }) +
      kpi(L('المشتريات', 'Purchases'), mf(purchases), L('صافي بعد المرتجعات', 'Net of returns'), { suffix: 'ج.م', screen: 'pur' }) +
      kpi(L('قيمة التصنيع', 'Manufacturing value'), mf(manufactured), L('تكلفة المستلم من الإنتاج', 'Cost received from production'), { suffix: 'ج.م', screen: 'prod' }) +
      kpi(L('النقدية والبنوك', 'Cash and banks'), mf(cash), L('الرصيد الحالي', 'Current balance'), { suffix: 'ج.م', screen: 'bank' }) +
      kpi(L('مستحق لنا', 'Receivable'), mf(rec), L('أرصدة العملاء', 'Customer balances'), { suffix: 'ج.م', screen: 'cust', tone: rec > 0 ? 'warn' : '' }) +
      kpi(L('مستحق علينا', 'Payable'), mf(pay), L('أرصدة الموردين', 'Supplier balances'), { suffix: 'ج.م', screen: 'supp', tone: pay > 0 ? 'warn' : '' }) +
      kpi(L('متوسط الفاتورة', 'Average invoice'), mf(avgSale), L('لكل فاتورة بيع', 'Per sales invoice'), { suffix: 'ج.م' }) +
      kpi(L('عدد فواتير البيع', 'Sales invoices'), nf(salesInv.length), L('في الفترة المحددة', 'In the selected period'), { screen: 'sale' }) +
      '</section>'
    );
  }

  function alertCard(title, count, body, tone, alertKey) {
    return (
      `<button type="button" class="dash-alert${tone ? ` dash-alert-${tone}` : ''}" data-alert="${esc(alertKey)}" aria-haspopup="dialog">` +
      `<div class="dash-alert-head"><b>${esc(title)}</b><span>${esc(nf(count))}</span></div>` +
      `<p>${body}</p>` +
      `<span class="dash-alert-more">${esc(L('عرض التفاصيل', 'View details'))} ${L('‹', '›')}</span>` +
      `</button>`
    );
  }

  function alertsPanel() {
    const overdueSales = overdueDocs('sale');
    const overduePurchases = overdueDocs('pur');
    const low = lowStockItems();
    const openOrders = workOrders().filter((o) => Number(o.st) !== 2);
    const cards = [];

    if (overdueSales.length) {
      const sum = overdueSales.reduce((s, d) => s + remainingOf(d), 0);
      cards.push(
        alertCard(
          L('فواتير بيع متأخرة', 'Overdue sales invoices'),
          overdueSales.length,
          esc(L(`إجمالي المتبقي ${mf(sum)} ج.م`, `Outstanding ${mf(sum)} EGP`)),
          'warn',
          'overdue-sales',
        ),
      );
    }
    if (overduePurchases.length) {
      const sum = overduePurchases.reduce((s, d) => s + remainingOf(d), 0);
      cards.push(
        alertCard(
          L('فواتير شراء متأخرة', 'Overdue purchase invoices'),
          overduePurchases.length,
          esc(L(`إجمالي المتبقي ${mf(sum)} ج.م`, `Outstanding ${mf(sum)} EGP`)),
          'warn',
          'overdue-purchases',
        ),
      );
    }
    if (low.length) {
      cards.push(
        alertCard(
          L('أصناف عند حد الطلب', 'Items at reorder point'),
          low.length,
          esc(low.slice(0, 3).map((i) => i.name).join(' · ')),
          'warn',
          'low-stock',
        ),
      );
    }
    if (openOrders.length) {
      cards.push(
        alertCard(
          L('أوامر إنتاج مفتوحة', 'Open work orders'),
          openOrders.length,
          esc(openOrders.slice(0, 3).map((o) => o.no).join(' · ')),
          '',
          'open-orders',
        ),
      );
    }

    if (!cards.length) {
      return panel(
        L('تنبيهات', 'Alerts'),
        L('لا شيء يحتاج انتباهك الآن', 'Nothing needs attention'),
        `<p class="dash-empty">${esc(L('لا توجد فواتير متأخرة ولا أصناف تحت حد الطلب.', 'No overdue invoices and no items below their reorder point.'))}</p>`,
      );
    }
    return panel(
      L('تنبيهات تحتاج إجراء', 'Action needed'),
      L('مبنية على تاريخ اليوم', 'Based on today'),
      `<div class="dash-alerts">${cards.join('')}</div>`,
    );
  }

  /* Alert details: every card opens the underlying records, because a count
     alone never tells the user which invoice or item to act on. */
  function alertRows(key) {
    const name = (d) => (typeof pn === 'function' ? pn(d.pid) : '');
    if (key === 'overdue-sales' || key === 'overdue-purchases') {
      const kind = key === 'overdue-sales' ? 'sale' : 'pur';
      return overdueDocs(kind)
        .sort((a, b) => String(a.due).localeCompare(String(b.due)))
        .map((d) => ({
          title: d.no || '',
          meta: [name(d), L('استحقاق', 'Due') + ' ' + factoryDateFormat(d.due || d.date)]
            .filter(Boolean)
            .join(' · '),
          value: remainingOf(d),
          open: () => openRef('doc', d.id),
        }));
    }
    if (key === 'low-stock') {
      return lowStockItems().map((i) => {
        const qty = Object.values(i.st || {}).reduce((a, b) => a + (Number(b) || 0), 0);
        return {
          title: i.name || '',
          meta: L('الحد الأدنى', 'Reorder point') + ' ' + nf(i.min),
          value: qty,
          valueLabel: L('المتاح', 'On hand'),
          qty: true,
          open: () => {
            if (typeof go === 'function') go('items');
          },
        };
      });
    }
    if (key === 'open-orders') {
      return workOrders()
        .filter((o) => Number(o.st) !== 2)
        .map((o) => ({
          title: o.no || '',
          meta:
            Number(o.st) === 1 ? L('قيد التنفيذ', 'In progress') : L('جديدة', 'New'),
          value: Number(o.total) || 0,
          open: () => {
            if (typeof openRef === 'function') openRef('po', o.id);
          },
        }));
    }
    return [];
  }

  const ALERT_TITLES = {
    'overdue-sales': L('فواتير بيع متأخرة', 'Overdue sales invoices'),
    'overdue-purchases': L('فواتير شراء متأخرة', 'Overdue purchase invoices'),
    'low-stock': L('أصناف عند حد الطلب', 'Items at reorder point'),
    'open-orders': L('أوامر إنتاج مفتوحة', 'Open work orders'),
  };

  function closeAlertSheet() {
    const sheet = document.getElementById('factory-alert-sheet');
    if (sheet) sheet.remove();
    document.body.classList.remove('factory-sheet-open');
  }

  function openAlertSheet(key) {
    closeAlertSheet();
    const rows = alertRows(key);
    const money = key === 'overdue-sales' || key === 'overdue-purchases' || key === 'open-orders';
    const wrap = document.createElement('div');
    wrap.id = 'factory-alert-sheet';
    wrap.className = 'factory-sheet';
    wrap.setAttribute('role', 'dialog');
    wrap.setAttribute('aria-modal', 'true');
    wrap.setAttribute('aria-label', ALERT_TITLES[key] || '');
    wrap.innerHTML =
      '<div class="factory-sheet-panel">' +
      '<header class="factory-sheet-head">' +
      `<div><b>${esc(ALERT_TITLES[key] || '')}</b><span>${esc(
        rows.length ? L(`${nf(rows.length)} سجل`, `${nf(rows.length)} records`) : L('لا توجد سجلات', 'No records'),
      )}</span></div>` +
      `<button type="button" class="factory-sheet-close" aria-label="${esc(L('إغلاق', 'Close'))}">✕</button>` +
      '</header>' +
      '<div class="factory-sheet-body">' +
      (rows.length
        ? '<ul class="factory-sheet-list">' +
          rows
            .map(
              (r, i) =>
                `<li><button type="button" data-row="${i}">` +
                `<span class="factory-sheet-title">${esc(r.title)}</span>` +
                `<span class="factory-sheet-meta">${esc(r.meta)}</span>` +
                `<b dir="ltr">${esc(
                  hideNumbers() ? '—' : r.qty ? nf(r.value) : money ? mf(r.value) + ' ج.م' : nf(r.value),
                )}</b>` +
                '</button></li>',
            )
            .join('') +
          '</ul>'
        : `<p class="dash-empty">${esc(L('لا توجد سجلات مطابقة الآن.', 'No matching records right now.'))}</p>`) +
      '</div></div>';
    document.body.appendChild(wrap);
    document.body.classList.add('factory-sheet-open');
    wrap.addEventListener('click', (e) => {
      if (e.target === wrap || e.target.closest('.factory-sheet-close')) {
        closeAlertSheet();
        return;
      }
      const row = e.target.closest('[data-row]');
      if (row) {
        const target = rows[Number(row.dataset.row)];
        closeAlertSheet();
        if (target) target.open();
      }
    });
  }

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeAlertSheet();
  });

  function agingPanel() {
    // Receivables split into age bands, measured from each invoice's due date.
    const bands = [
      { key: 'current', label: L('غير مستحقة بعد', 'Not yet due'), short: L('لم تستحق', 'Current'), test: (d) => d <= 0 },
      { key: '1-30', label: L('1 إلى 30 يوم', '1 to 30 days'), short: '1-30', test: (d) => d > 0 && d <= 30 },
      { key: '31-60', label: L('31 إلى 60 يوم', '31 to 60 days'), short: '31-60', test: (d) => d > 30 && d <= 60 },
      { key: '61-90', label: L('61 إلى 90 يوم', '61 to 90 days'), short: '61-90', test: (d) => d > 60 && d <= 90 },
      { key: '90+', label: L('أكثر من 90 يوم', 'Over 90 days'), short: L('+90', '+90'), test: (d) => d > 90 },
    ];
    const now = new Date(today() + 'T12:00:00');
    const totals = new Map(bands.map((b) => [b.key, 0]));
    docs()
      .filter((d) => d.t === 'sale')
      .forEach((d) => {
        const remaining = remainingOf(d);
        if (remaining <= 0.005) return;
        const due = d.due || d.date;
        const days = Math.floor((now - new Date(due + 'T12:00:00')) / 86400000);
        const band = bands.find((b) => b.test(days)) || bands[bands.length - 1];
        totals.set(band.key, (totals.get(band.key) || 0) + remaining);
      });
    const rows = bands.map((b) => ({ label: b.label, short: b.short, value: totals.get(b.key) || 0 }));
    const total = rows.reduce((s, r) => s + r.value, 0);
    return panel(
      L('أعمار الذمم المدينة', 'Receivables aging'),
      L('من تاريخ الاستحقاق', 'From the due date'),
      total > 0.005
        ? agingChart(rows, total, L('أعمدة أعمار الذمم المدينة', 'Receivables aging columns'))
        : `<p class="dash-empty">${esc(L('لا توجد أرصدة عملاء مفتوحة.', 'No open customer balances.'))}</p>`,
    );
  }

  function fact(label, value, tone) {
    return `<li><span>${esc(label)}</span><b dir="ltr"${tone ? ` class="dash-${tone}"` : ''}>${esc(value)}</b></li>`;
  }

  function productionPanel() {
    const from = periodStart(), to = periodEnd();
    const orders = workOrders();
    const count = (st) => orders.filter((o) => Number(o.st) === st).length;
    const inWindow = (m) => (!from || m.d >= from) && (!to || m.d <= to);
    const sum = (kind) =>
      movements()
        .filter((m) => m.kind === kind && inWindow(m))
        .reduce((s, m) => s + Math.abs(Number(m.val) || 0), 0);
    const consumed = sum('production_issue');
    const produced = sum('production_receipt');
    const margin = produced - consumed;
    const money = (v) => (hideNumbers() ? '—' : mf(v) + ' ج.م');

    return panel(
      L('الإنتاج', 'Production'),
      L('أوامر وخامات الفترة', 'Orders and materials for the period'),
      '<ul class="dash-facts">' +
        fact(L('أوامر مكتملة', 'Completed orders'), nf(count(2))) +
        fact(L('قيد التنفيذ', 'In progress'), nf(count(1))) +
        fact(L('جديدة', 'New'), nf(count(0))) +
        fact(L('خامات مستهلكة', 'Materials consumed'), money(consumed)) +
        fact(L('منتجات مستلمة', 'Goods received'), money(produced)) +
        fact(L('فرق التكلفة', 'Cost difference'), money(margin), margin >= 0 ? 'ok' : 'warn') +
        '</ul>',
    );
  }

  function recentPanel() {
    const rows = [
      ...docs().map((d) => ({
        ts: d.ts || 0,
        date: d.date,
        type: (typeof TN !== 'undefined' && TN[d.t]) || L('فاتورة', 'Invoice'),
        ref: d.no,
        party: typeof pn === 'function' ? pn(d.pid) : '',
        amount: Number(d.total) || 0,
        source: 'doc',
        id: d.id,
      })),
      ...((typeof S !== 'undefined' && S.vch) || []).map((v) => ({
        ts: v.ts || 0,
        date: v.date,
        type: v.title || (v.dir === 'in' ? L('سند قبض', 'Receipt') : L('سند دفع', 'Payment')),
        ref: v.no,
        party: v.who || '',
        amount: Number(v.total) || 0,
        source: 'vch',
        id: v.id,
      })),
    ]
      .sort((a, b) => b.ts - a.ts)
      .slice(0, 8);

    if (!rows.length) {
      return panel(
        L('أحدث الحركات', 'Latest activity'),
        L('الأحدث أولاً', 'Newest first'),
        `<p class="dash-empty">${esc(L('لا توجد حركات مسجلة بعد.', 'No activity recorded yet.'))}</p>`,
      );
    }

    const body =
      '<div class="factory-table-scroll"><table class="l dash-table"><thead><tr>' +
      `<th>${esc(L('التاريخ', 'Date'))}</th><th>${esc(L('النوع', 'Type'))}</th><th>${esc(L('المرجع', 'Reference'))}</th><th>${esc(L('الطرف', 'Party'))}</th><th>${esc(L('المبلغ', 'Amount'))}</th>` +
      '</tr></thead><tbody>' +
      rows
        .map(
          (r) =>
            `<tr role="button" tabindex="0" data-open-source="${esc(r.source)}" data-open-id="${esc(r.id)}"><td>${esc(dayFmt(r.date))}</td><td>${esc(r.type)}</td><td dir="ltr">${esc(r.ref || '')}</td><td>${esc(r.party || '—')}</td><td dir="ltr" class="dash-amount">${hideNumbers() ? '—' : mf(r.amount) + ' ج.م'}</td></tr>`,
        )
        .join('') +
      '</tbody></table></div>';

    return panel(L('أحدث الحركات', 'Latest activity'), L('الأحدث أولاً', 'Newest first'), body);
  }

  function panel(title, subtitle, body) {
    return (
      '<section class="card dash-card">' +
      `<div class="factory-panel-head"><b>${esc(title)}</b><span>${esc(subtitle)}</span></div>` +
      body +
      '</section>'
    );
  }

  /* ---------- screen ---------- */

  function dashboardHtml() {
    if (!accts().length && !docs().length) {
      const header =
        typeof ph === 'function'
          ? ph(L('لوحة المعلومات', 'Dashboard'))
          : '<div class="ph"><h2>لوحة المعلومات</h2></div>';
      return (
        header +
        `<section class="card dash-card"><p class="dash-empty">${esc(
          L('لا توجد بيانات بعد. أضف حساباتك وفواتيرك لتظهر المؤشرات هنا.', 'No data yet. Add your accounts and invoices to populate the dashboard.'),
        )}</p></section>`
      );
    }

    const sp = salesPurchasesSeries();
    const cash = cashSeries();
    const exp = expenseRows();
    const tops = topItemRows();
    const byClass = purchasesByWarehouseClass();

    const salesTotal = sp.series[0].values.reduce((a, b) => a + b, 0);
    const purchaseTotal = sp.series[1].values.reduce((a, b) => a + b, 0);
    const mfgTotal = sp.series[2].values.reduce((a, b) => a + b, 0);
    const bestIndex = sp.series[0].values.indexOf(Math.max(...sp.series[0].values));
    const bestMonth = sp.buckets[bestIndex];

    const spCaption =
      salesTotal || purchaseTotal || mfgTotal
        ? L(
            `آخر 6 أشهر: مبيعات ${mf(salesTotal)} ج.م، مشتريات ${mf(purchaseTotal)} ج.م، تصنيع ${mf(mfgTotal)} ج.م. أعلى شهر مبيعات: ${bestMonth.label} ${bestMonth.year}.`,
            `Last 6 months: sales ${mf(salesTotal)} EGP, purchases ${mf(purchaseTotal)} EGP, manufacturing ${mf(mfgTotal)} EGP. Best sales month: ${bestMonth.label} ${bestMonth.year}.`,
          )
        : L('لا توجد فواتير أو أوامر إنتاج في آخر 6 أشهر.', 'No invoices or work orders in the last 6 months.');

    const cashNow = cash.values[cash.values.length - 1] || 0;
    const cashBefore = cash.values[0] || 0;
    const cashDelta = cashNow - cashBefore;
    const cashCaption = L(
      `النقدية والبنوك الآن ${mf(cashNow)} ج.م، ${cashDelta >= 0 ? 'بزيادة' : 'بانخفاض'} ${mf(Math.abs(cashDelta))} ج.م عن بداية الفترة المعروضة.`,
      `Cash and banks now ${mf(cashNow)} EGP, ${cashDelta >= 0 ? 'up' : 'down'} ${mf(Math.abs(cashDelta))} EGP from the start of the shown period.`,
    );

    const header =
      typeof ph === 'function'
        ? ph(
            L('لوحة المعلومات', 'Dashboard'),
            `<span class="mu">${esc(L('الفترة', 'Period'))}: ${esc(dayFmt(periodStart() || L('البداية', 'start')))} → ${esc(dayFmt(periodEnd()))}</span>`,
          )
        : '';

    // Each block is a movable unit so the layout can be reordered from Settings.
    const blocks = dashBlocks(sp, cash, exp, tops, byClass, spCaption, cashCaption);
    const order = dashOrder(blocks);
    const visible = order.filter((key) => blocks[key] && dashLayout()[key] !== 'hidden');

    return (
      header +
      periodBar() +
      kpiGrid() +
      '<div class="dash-grid">' +
      visible.map((key) => blocks[key]).join('') +
      '</div>'
    );
  }

  /* ---------- layout preferences ---------- */

  const DASH_BLOCK_TITLES = {
    sales: ['المبيعات والمشتريات والتصنيع', 'Sales, purchases and manufacturing'],
    warehouses: ['المشتريات حسب نوع المخزن', 'Purchases by warehouse type'],
    cash: ['حركة النقدية والبنوك', 'Cash and bank movement'],
    expenses: ['أين تذهب المصروفات', 'Where expenses go'],
    items: ['الأصناف الأكثر بيعًا', 'Best-selling items'],
    aging: ['أعمار الأرصدة', 'Balance aging'],
    production: ['الإنتاج', 'Production'],
    alerts: ['تنبيهات', 'Alerts'],
    recent: ['أحدث الحركات', 'Latest transactions'],
  };

  function dashLayout() {
    try {
      return JSON.parse(localStorage.getItem('ms-factory-preview-ui-v1') || '{}').dashLayout || {};
    } catch (_) {
      return {};
    }
  }

  function dashOrder(blocks) {
    const keys = Object.keys(blocks || DASH_BLOCK_TITLES);
    let saved = [];
    try {
      saved = JSON.parse(localStorage.getItem('ms-factory-preview-ui-v1') || '{}').dashOrder || [];
    } catch (_) {}
    const kept = Array.isArray(saved) ? saved.filter((key) => keys.includes(key)) : [];
    return [...kept, ...keys.filter((key) => !kept.includes(key))];
  }

  function dashBlocks(sp, cash, exp, tops, byClass, spCaption, cashCaption) {
    return {
      sales: panel(
        L('المبيعات والمشتريات والتصنيع', 'Sales, purchases and manufacturing'),
        L('آخر 6 أشهر · الأقدم على اليسار', 'Last 6 months · oldest on the left'),
        legend(sp.series) +
          columnsChart(sp.buckets, sp.series, L('أعمدة المبيعات والمشتريات والتصنيع', 'Sales, purchases and manufacturing columns')) +
          `<p class="dash-insight">${esc(spCaption)}</p>`,
      ),
      warehouses: panel(
        L('المشتريات حسب نوع المخزن', 'Purchases by warehouse type'),
        L('منتجات نهائية · مواد خام · مستلزمات', 'Finished goods · raw materials · supplies'),
        byClass.total > 0.005
          ? donutChart(byClass.groups, byClass.total, L('توزيع المشتريات حسب نوع المخزن', 'Purchases split by warehouse type'), L('مشتريات')) +
            `<details class="dash-details"><summary>${esc(L('تفصيل كل مخزن', 'Breakdown per warehouse'))}</summary>${hbars(byClass.detail, byClass.total)}</details>`
          : `<p class="dash-empty">${esc(L('لا توجد فواتير شراء في الفترة المحددة.', 'No purchase invoices in the selected period.'))}</p>`,
      ),
      cash: panel(
        L('حركة النقدية والبنوك', 'Cash and bank movement'),
        L('رصيد آخر كل شهر', 'Balance at each month end'),
        areaChart(cash.buckets, cash.values, L('خط رصيد النقدية', 'Cash balance line'), 'var(--pr)') +
          `<p class="dash-insight">${esc(cashCaption)}</p>`,
      ),
      expenses: panel(
        L('أين تذهب المصروفات', 'Where expenses go'),
        L('أكبر 5 حسابات مصروفات للفترة', 'Top 5 expense accounts for the period'),
        donutChart(exp.rows, exp.total, L('توزيع المصروفات', 'Expense split'), L('مصروفات')),
      ),
      items: panel(
        L('الأصناف الأكثر بيعًا', 'Best-selling items'),
        L('بالكمية للفترة المحددة', 'By quantity for the selected period'),
        donutChart(tops, 0, L('توزيع الكميات المبيعة على الأصناف', 'Sold quantity split by item'), L('كمية'), qf),
      ),
      aging: agingPanel(),
      production: productionPanel(),
      alerts: alertsPanel(),
      recent: recentPanel(),
    };
  }

  /** Layout controls for the Settings screen. */
  window.factoryDashLayout = {
    titles: DASH_BLOCK_TITLES,
    order: () => dashOrder(DASH_BLOCK_TITLES),
    layout: dashLayout,
    setOrder(keys) {
      const prefs = JSON.parse(localStorage.getItem('ms-factory-preview-ui-v1') || '{}');
      prefs.dashOrder = keys.filter((key) => key in DASH_BLOCK_TITLES);
      localStorage.setItem('ms-factory-preview-ui-v1', JSON.stringify(prefs));
    },
    setVisible(key, visible) {
      const prefs = JSON.parse(localStorage.getItem('ms-factory-preview-ui-v1') || '{}');
      prefs.dashLayout = prefs.dashLayout || {};
      prefs.dashLayout[key] = visible ? 'shown' : 'hidden';
      localStorage.setItem('ms-factory-preview-ui-v1', JSON.stringify(prefs));
    },
  };

  function install() {
    if (typeof SCR === 'undefined' || typeof T === 'undefined') return false;
    if (typeof SCR.dash === 'function') return true;

    SCR.dash = dashboardHtml;
    wirePeriodBar();

    // The dashboard sits first in the sidebar, right above the summary.
    const label = L('لوحة المعلومات', 'Dashboard');
    if (!T.some((t) => t[0] === 'dash')) T.unshift(['dash', typeof ico === 'function' ? ico('dashboard') : '', label]);
    if (typeof N !== 'undefined') N.dash = label;
    if (typeof CLOUD_SCREEN_PERMISSIONS !== 'undefined') CLOUD_SCREEN_PERMISSIONS.dash = null;

    document.addEventListener('click', (e) => {
      const alertBtn = e.target.closest('[data-alert]');
      if (alertBtn) {
        e.preventDefault();
        openAlertSheet(alertBtn.dataset.alert);
        return;
      }
      const target = e.target.closest('[data-dash-go]');
      if (target) {
        e.preventDefault();
        if (typeof go === 'function') go(target.dataset.dashGo);
        return;
      }
      const row = e.target.closest?.('.dash-table tr[data-open-source]');
      if (row && typeof openRef === 'function') openRef(row.dataset.openSource, row.dataset.openId);
    });

    document.addEventListener('keydown', (e) => {
      const row = e.target.closest?.('.dash-table tr[data-open-source]');
      if (row && (e.key === 'Enter' || e.key === ' ')) {
        e.preventDefault();
        if (typeof openRef === 'function') openRef(row.dataset.openSource, row.dataset.openId);
      }
    });

    return true;
  }

  /* The summary screen shows the same alerts as a compact strip, so it reuses
     these helpers instead of duplicating the overdue/low-stock rules. */
  window.factoryAlertCards = function () {
    const list = [];
    const sales = overdueDocs('sale');
    const purchases = overdueDocs('pur');
    const low = lowStockItems();
    const orders = workOrders().filter((o) => Number(o.st) !== 2);
    if (sales.length) {
      const sum = sales.reduce((a, d) => a + remainingOf(d), 0);
      list.push(alertCard(L('فواتير بيع متأخرة', 'Overdue sales invoices'), sales.length,
        esc(L(`إجمالي المتبقي ${mf(sum)} ج.م`, `Outstanding ${mf(sum)} EGP`)), 'warn', 'overdue-sales'));
    }
    if (purchases.length) {
      const sum = purchases.reduce((a, d) => a + remainingOf(d), 0);
      list.push(alertCard(L('فواتير شراء متأخرة', 'Overdue purchase invoices'), purchases.length,
        esc(L(`إجمالي المتبقي ${mf(sum)} ج.م`, `Outstanding ${mf(sum)} EGP`)), 'warn', 'overdue-purchases'));
    }
    if (low.length) {
      list.push(alertCard(L('أصناف عند حد الطلب', 'Items at reorder point'), low.length,
        esc(low.slice(0, 3).map((i) => i.name).join(' · ')), 'warn', 'low-stock'));
    }
    if (orders.length) {
      list.push(alertCard(L('أوامر إنتاج مفتوحة', 'Open work orders'), orders.length,
        esc(orders.slice(0, 3).map((o) => o.no).join(' · ')), '', 'open-orders'));
    }
    return list;
  };
  window.factoryOpenAlert = openAlertSheet;

  // app.js and preview-ui.js both run before this file, so the registry is ready.
  function boot() {
    if (!install()) {
      window.addEventListener('factory-database-ready', install, { once: true });
      return;
    }
    // dashboard.js loads after preview-ui.js, so the summary is repainted here
    // to pick up the alert strip on first load.
    if (typeof CUR !== 'undefined' && CUR && CUR.kind === 'screen' && typeof render === 'function') {
      if (CUR.sid === 'dash' || CUR.sid === 'home') render();
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
