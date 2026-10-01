/* VAHAN Scraper dashboard - React (UMD, no build step).
   Talks to the FastAPI backend. API base comes from config.js (the buildless
   equivalent of a .env); "" means same origin. */
const API_BASE = (window.__VAHAN_CONFIG__ && window.__VAHAN_CONFIG__.API_BASE) || "";

const {useState, useEffect, useRef} = React;
const h = React.createElement;

/* ---------------- API ---------------- */
const j = (r) => r.json();
const u = (p) => API_BASE + p;
const API = {
  flows:  ()         => fetch(u('/api/flows')).then(j),
  schedules: ()      => fetch(u('/api/schedules')).then(j),
  states: ()         => fetch(u('/api/states')).then(j),
  rtos:   (codes)    => fetch(u('/api/rtos?states=' + codes.join(','))).then(j),
  runs:   ()         => fetch(u('/api/runs')).then(j),
  run:    (id)       => fetch(u('/api/runs/' + id)).then(j),
  logs:   (id)       => fetch(u('/api/runs/' + id + '/logs?after=0')).then(j),
  start:  async (body) => {
    const r = await fetch(u('/api/runs'), {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    if (!r.ok) throw new Error((await r.text()) || ('HTTP ' + r.status));
    return r.json();
  },
  captcha:(id, text) => fetch(u('/api/runs/' + id + '/captcha'), {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text})}),
  cancel: (id)       => fetch(u('/api/runs/' + id + '/cancel'), {method:'POST'}),
  downloadUrl: (id, which) => u('/api/runs/' + id + '/download?which=' + which),
};

function usePoll(fn, ms, deps) {
  const [data, setData] = useState(null);
  const saved = useRef(fn); saved.current = fn;
  useEffect(() => {
    let alive = true;
    const tick = async () => { try { const d = await saved.current(); if (alive) setData(d); } catch (e) {} };
    tick();
    const t = setInterval(tick, ms);
    return () => { alive = false; clearInterval(t); };
  }, deps);
  return data;
}

/* ---------------- helpers ---------------- */
const clock = (t) => t ? new Date(t * 1000).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'}) : '—';
const pct = (r) => r && r.progress_total ? Math.round(100 * r.progress_done / r.progress_total) : 0;
const flowName = (id) => ({
  flow1_state_monthwise: 'Flow 1 · StateMonthwise',
  flow2_state_fuelwise: 'Flow 2 · StateFuelwise',
  flow3_rto_monthwise: 'Flow 3 · RTO-Monthwise',
  flow4_rto_fuelwise: 'Flow 4 · RTOFuelwise',
}[id] || id);
const dur = (r) => {
  if (!r || !r.started) return '—';
  const end = r.finished || Date.now() / 1000;
  const s = Math.max(0, Math.round(end - r.started));
  return s < 60 ? s + 's' : Math.floor(s / 60) + 'm ' + (s % 60) + 's';
};
const MONTHS = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];

/* ---------------- multi-select dropdown (states / months / RTOs) ---------------- */
function useOutsideClose(ref, onClose) {
  useEffect(() => {
    function onDoc(e) { if (ref.current && !ref.current.contains(e.target)) onClose(); }
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [onClose]);
}

function MultiSelect({options, selected, onChange, placeholder, loading, groupBy}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const boxRef = useRef(null);
  useOutsideClose(boxRef, () => setOpen(false));

  const filtered = q
    ? options.filter(o => o.label.toLowerCase().includes(q.toLowerCase()) || o.value.toLowerCase().includes(q.toLowerCase()))
    : options;

  function toggle(v) {
    const set = new Set(selected);
    set.has(v) ? set.delete(v) : set.add(v);
    onChange([...set]);
  }

  const summary = selected.length === 0 ? placeholder
    : selected.length <= 3 ? selected.join(', ')
    : selected.length + ' selected';

  let rows;
  if (loading) rows = h('div', {className: 'mselEmpty'}, 'Loading…');
  else if (!filtered.length) rows = h('div', {className: 'mselEmpty'}, 'No matches');
  else if (groupBy) {
    rows = [];
    let lastGroup = null;
    for (const o of filtered) {
      if (o.group !== lastGroup) { rows.push(h('div', {key: 'g' + o.group, className: 'mselGroup'}, o.group)); lastGroup = o.group; }
      rows.push(h('label', {key: o.value, className: 'mselOpt'},
        h('input', {type: 'checkbox', checked: selected.includes(o.value), onChange: () => toggle(o.value)}),
        h('span', null, o.label)));
    }
  } else {
    rows = filtered.map(o => h('label', {key: o.value, className: 'mselOpt'},
      h('input', {type: 'checkbox', checked: selected.includes(o.value), onChange: () => toggle(o.value)}),
      h('span', null, o.label)));
  }

  return h('div', {className: 'msel', ref: boxRef},
    h('button', {type: 'button', className: 'input mselBtn', onClick: () => setOpen(o => !o)},
      h('span', {className: selected.length ? '' : 'ph'}, summary),
      h('span', {className: 'chev'}, '▾')),
    open && h('div', {className: 'mselPop'},
      h('input', {className: 'mselSearch', placeholder: 'Search…', value: q, onChange: e => setQ(e.target.value), autoFocus: true}),
      h('div', {className: 'mselActions'},
        h('button', {type: 'button', className: 'lnk', onClick: () => onChange(options.map(o => o.value))}, 'Select all'),
        h('button', {type: 'button', className: 'lnk', onClick: () => onChange([])}, 'Clear')),
      h('div', {className: 'mselList'}, rows)));
}

function RtoPicker({stateCodes, selected, onChange}) {
  const [data, setData] = useState({});
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');
  const key = stateCodes.slice().sort().join(',');

  useEffect(() => {
    if (!stateCodes.length || stateCodes.length > 5) { setData({}); return; }
    let alive = true;
    setLoading(true); setErr('');
    API.rtos(stateCodes)
      .then(d => { if (alive) { setData(d); setLoading(false); } })
      .catch(e => { if (alive) { setErr(String(e.message || e)); setLoading(false); } });
    return () => { alive = false; };
  }, [key]);

  if (!stateCodes.length || stateCodes.length > 5) return null; // caller falls back to free text
  if (err) return h('p', {className: 'hint'}, 'Could not load RTOs from the portal: ' + err);

  const options = [];
  for (const code of stateCodes) {
    (data[code] || []).forEach(r => options.push({value: r.code, label: r.name, group: code}));
  }
  return h(MultiSelect, {
    options, selected, onChange, loading, groupBy: true,
    placeholder: 'blank = all RTOs in selected state(s)',
  });
}

/* ---------------- components ---------------- */
function Pill({state}) {
  return h('span', {className: 'pill ' + state},
    h('span', {className: 'dot'}), state.replace('_', ' '));
}

function Bar({value, animate, mini}) {
  return h('div', {className: 'bar' + (mini ? ' mini' : '') + (animate ? ' animate' : '')},
    h('i', {style: {width: value + '%'}}));
}

function Header() {
  return h('header', {className: 'top'},
    h('div', {className: 'mark'},
      h('svg', {width: 20, height: 20, viewBox: '0 0 24 24', fill: 'none'},
        h('path', {d: 'M3 13l2-5a3 3 0 012.8-2h8.4A3 3 0 0119 8l2 5M5 13h14v4a1 1 0 01-1 1h-1a1 1 0 01-1-1v-1H8v1a1 1 0 01-1 1H6a1 1 0 01-1-1v-4z', stroke: '#fff', strokeWidth: 1.7, strokeLinecap: 'round', strokeLinejoin: 'round'}),
        h('circle', {cx: 8, cy: 16, r: 1.3, fill: '#fff'}),
        h('circle', {cx: 16, cy: 16, r: 1.3, fill: '#fff'}))),
    h('div', null,
      h('h1', null, 'VAHAN Registration Scraper'),
      h('p', null, 'Process Definition Document automation · Flow 1 live')),
    h('div', {className: 'live'}, h('span', {className: 'dot'}), 'Auto-refreshing'));
}

function StartCard({onStarted}) {
  const flows = usePoll(API.flows, 60000, []) || [];
  const statesList = usePoll(API.states, 3600000, []) || [];
  const [flow, setFlow] = useState('');
  const [statesSel, setStatesSel] = useState([]);
  const [monthsSel, setMonthsSel] = useState([]);
  const [rtosPicked, setRtosPicked] = useState([]);
  const [rtosText, setRtosText] = useState('');
  const [maxRtos, setMaxRtos] = useState('');
  const [skipIndia, setSkipIndia] = useState(false);
  const [skipRecon, setSkipRecon] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const isFlow1 = flow === 'flow1_state_monthwise';
  const hasMonths = flow === 'flow2_state_fuelwise' || flow === 'flow4_rto_fuelwise';
  const hasRtoLimits = flow === 'flow3_rto_monthwise' || flow === 'flow4_rto_fuelwise';
  const isFlow4 = flow === 'flow4_rto_fuelwise';
  const rtoPickerActive = hasRtoLimits && statesSel.length >= 1 && statesSel.length <= 5;

  useEffect(() => { if (!flow && flows.length) setFlow(flows[0].id); }, [flows.length]);
  useEffect(() => { setRtosPicked([]); }, [statesSel.join(',')]); // stale picks from a different state selection

  const stateOptions = statesList.map(s => ({value: s.code, label: s.name + ' (' + s.code + ')'}));
  const monthOptions = MONTHS.map(m => ({value: m, label: m}));

  async function go() {
    setErr(''); setBusy(true);
    const params = {};
    if (statesSel.length) params.states = statesSel;
    if (hasMonths && monthsSel.length) params.months = monthsSel;
    if (hasRtoLimits) {
      if (rtoPickerActive) {
        if (rtosPicked.length) params.rtos = rtosPicked;
      } else {
        const r = rtosText.trim();
        if (r) params.rtos = r.split(',').map(x => x.trim()).filter(Boolean);
      }
      if (maxRtos.trim()) params.max_rtos_per_state = parseInt(maxRtos.trim(), 10) || undefined;
    }
    if (skipIndia && isFlow1) params.skip_all_india = true;
    if (skipRecon) params.skip_reconciliation = true;
    try {
      const run = await API.start({flow, params});
      onStarted(run.id);
      setStatesSel([]); setMonthsSel([]); setRtosPicked([]); setRtosText('');
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  return h('div', {className: 'card'},
    h('div', {className: 'hd'}, h('h2', null, 'Start a run')),
    h('div', {className: 'bd'},
      h('label', {className: 'fld'},
        h('span', null, 'Flow'),
        h('select', {className: 'input', value: flow, onChange: e => setFlow(e.target.value)},
          flows.map(f => h('option', {key: f.id, value: f.id}, f.name)))),
      h('label', {className: 'fld'},
        h('span', null, 'States'),
        h(MultiSelect, {
          options: stateOptions, selected: statesSel, onChange: setStatesSel,
          placeholder: 'blank = all 36 States/UTs',
        }),
        h('p', {className: 'hint'}, isFlow4
          ? 'Blank = all 36 states, every RTO, every month (~15,000+ files). This runs for many hours — pick specific states / RTOs / months for a quick test.'
          : hasMonths
          ? 'Blank = all 36 states. Each state runs once per month, so a full run is 36 × months.'
          : hasRtoLimits
          ? 'Blank = all 36 states, every RTO in each (~1,700 total). This runs for hours — use Max RTOs/state for a quick test.'
          : 'Leave blank to run all 36 States/UTs + the All-India reconciliation file (~4–5 min). Or pick specific states for a quick test.')),
      hasMonths && h('label', {className: 'fld'},
        h('span', null, 'Months'),
        h(MultiSelect, {
          options: monthOptions, selected: monthsSel, onChange: setMonthsSel,
          placeholder: 'blank = Jan…current month',
        }),
        h('p', {className: 'hint'}, `Reconciliation vs Flow ${isFlow4 ? '3' : '1'} only runs when Months covers Jan…current.`)),
      hasRtoLimits && h('label', {className: 'fld'},
        h('span', null, 'RTO codes'),
        rtoPickerActive
          ? h(RtoPicker, {stateCodes: statesSel, selected: rtosPicked, onChange: setRtosPicked})
          : h('input', {
              className: 'input', value: rtosText, placeholder: 'blank = all RTOs in each state   ·   or  GA3,GA4',
              onChange: e => setRtosText(e.target.value),
            }),
        h('p', {className: 'hint'}, rtoPickerActive
          ? `Showing real RTOs for ${statesSel.join(', ')}, fetched live from the portal.`
          : 'Pick 1–5 specific states above to browse their real RTOs here — otherwise type codes directly.')),
      hasRtoLimits && h('label', {className: 'fld'},
        h('span', null, 'Max RTOs per state'),
        h('input', {
          className: 'input', value: maxRtos, placeholder: 'blank = no cap   ·   e.g. 2 for a quick smoke test',
          onChange: e => setMaxRtos(e.target.value),
        }),
        h('p', {className: 'hint'}, `Reconciliation vs Flow ${isFlow4 ? '3' : '1'} only runs with no RTO limit.`)),
      h('div', {className: 'checks'},
        isFlow1 && h('label', {className: 'chk'},
          h('input', {type: 'checkbox', checked: skipIndia, onChange: e => setSkipIndia(e.target.checked)}),
          'Skip All-India reference file'),
        h('label', {className: 'chk'},
          h('input', {type: 'checkbox', checked: skipRecon, onChange: e => setSkipRecon(e.target.checked)}),
          isFlow1 ? 'Skip MB / BMW reconciliation' : `Skip reconciliation vs Flow ${isFlow4 ? '3' : '1'}`)),
      h('button', {className: 'btn primary', onClick: go, disabled: busy || !flow},
        busy ? h(React.Fragment, null, h('span', {className: 'spin'}), 'Starting…') : 'Start run'),
      err && h('div', {className: 'formErr'}, err)));
}

function RunRow({run, sel, onSelect}) {
  return h('button', {className: 'run' + (sel ? ' sel' : ''), onClick: () => onSelect(run.id)},
    h('div', {className: 'r1'},
      h('span', {className: 'name'}, flowName(run.flow)),
      h('span', {style: {marginLeft: 'auto'}}, h(Pill, {state: run.state}))),
    h('div', {style: {marginTop: 8}}, h(Bar, {mini: true, value: pct(run), animate: run.state === 'running'})),
    h('div', {className: 'r2'},
      h('span', {className: 'id'}, '#' + run.id),
      h('span', null, run.progress_done + ' / ' + (run.progress_total || '?'))));
}

function RunsCard({runs, sel, onSelect}) {
  return h('div', {className: 'card'},
    h('div', {className: 'hd'},
      h('h2', null, 'Runs'),
      runs.length ? h('span', {className: 'count'}, runs.length) : null),
    h('div', {className: 'bd'},
      h('div', {className: 'runs'},
        runs.map(r => h(RunRow, {key: r.id, run: r, sel: r.id === sel, onSelect})))));
}

function ScheduleRow({flowId, sched}) {
  const name = flowName(flowId);
  if (!sched || !sched.configured) {
    return h('div', {className: 'schedRow off'},
      h('span', {className: 'name'}, name),
      h('span', {className: 'schedState'}, 'Not scheduled'));
  }
  const running = sched.status === 'Running';
  return h('div', {className: 'schedRow'},
    h('div', {className: 'r1'},
      h('span', {className: 'name'}, name),
      h('span', {className: 'schedState ' + (running ? 'live' : '')}, sched.status || '—')),
    h('div', {className: 'schedMeta'},
      h('span', null, sched.schedule_type, ' · ', sched.start_time,
        sched.occurrences > 1 ? ' · ' + sched.occurrences + 'x/month' : ''),
      h('span', null, 'Next: ', h('b', null, sched.next_run_time || '—'))),
    sched.last_run_time && h('div', {className: 'schedMeta dim'},
      'Last ran: ', sched.last_run_time,
      sched.last_result != null ? (sched.last_result === '0' ? ' (ok)' : ' (result ' + sched.last_result + ')') : ''));
}

function ScheduleCard() {
  const data = usePoll(API.schedules, 30000, []);
  const flowIds = ['flow1_state_monthwise', 'flow2_state_fuelwise', 'flow3_rto_monthwise', 'flow4_rto_fuelwise'];
  return h('div', {className: 'card'},
    h('div', {className: 'hd'}, h('h2', null, 'Scheduled runs')),
    h('div', {className: 'bd'},
      h('div', {className: 'schedList'},
        flowIds.map(id => h(ScheduleRow, {key: id, flowId: id, sched: data && data[id]})))));
}

function Captcha({run}) {
  const [val, setVal] = useState('');
  const [sent, setSent] = useState(false);
  const cap = run.captcha;
  useEffect(() => { setVal(cap ? (cap.ocr_guess || '') : ''); setSent(false); }, [cap && cap.attempt]);
  if (!cap) return null;

  async function submit() {
    setSent(true);
    await API.captcha(run.id, val);
  }
  return h('div', {className: 'captcha'},
    h('div', {className: 'ct'}, '🔒 Captcha needed · attempt ' + cap.attempt),
    h('div', {className: 'imgwrap'},
      h('img', {src: 'data:image/png;base64,' + cap.image_b64, alt: 'captcha'})),
    h('div', {className: 'ocr'}, 'Auto-read guessed: ', h('code', null, cap.ocr_guess || '(nothing)')),
    h('div', {className: 'row'},
      h('input', {
        className: 'input', autoFocus: true, value: val, spellCheck: false,
        onChange: e => setVal(e.target.value),
        onKeyDown: e => { if (e.key === 'Enter') submit(); }
      }),
      h('button', {className: 'btn', onClick: submit, disabled: sent},
        sent ? 'Sent' : 'Submit')));
}

function Stats({run}) {
  const res = run.result || {};
  const rok = res.reconciliation_ok;
  // Prefer the explicit label from the backend; fall back for older runs.
  const label = res.reconciliation_label
    || (rok === true ? 'pass' : rok === false ? 'mismatch' : 'n/a');
  const cls = label === 'pass' ? 'ok' : label === 'mismatch' ? 'err' : 'sky';
  const pretty = label.charAt(0).toUpperCase() + label.slice(1);
  return h('div', {className: 'stats'},
    h('div', {className: 'stat'}, h('div', {className: 'l'}, 'Succeeded'),
      h('div', {className: 'v ok'}, res.succeeded != null ? res.succeeded : '—')),
    h('div', {className: 'stat'}, h('div', {className: 'l'}, 'Failed'),
      h('div', {className: 'v ' + ((res.failed && res.failed.length) ? 'err' : '')}, res.failed ? res.failed.length : '—')),
    h('div', {className: 'stat'}, h('div', {className: 'l'}, 'Duration'),
      h('div', {className: 'v'}, dur(run))),
    h('div', {className: 'stat', title: res.partial_run ? 'Only some states were run — the MB/BMW check needs all 36' : ''},
      h('div', {className: 'l'}, 'Reconciliation'),
      h('div', {className: 'v ' + cls, style: {fontSize: pretty.length > 6 ? '13px' : undefined}}, pretty)));
}

function Downloads({run}) {
  const res = run.result || {};
  if (!res.zip) return null;
  const Icon = () => h('svg', {width: 15, height: 15, viewBox: '0 0 24 24', fill: 'none'},
    h('path', {d: 'M12 3v12m0 0l-4-4m4 4l4-4M5 21h14', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round'}));
  return h('div', {className: 'card'},
    h('div', {className: 'hd'}, h('h2', null, 'Output')),
    h('div', {className: 'bd'},
      h('div', {className: 'dl'},
        h('a', {href: API.downloadUrl(run.id, 'zip')}, h(Icon), 'Download ZIP'),
        h('a', {href: API.downloadUrl(run.id, 'summary')}, h(Icon), 'Summary .xlsx')),
      h('div', {className: 'meta'},
        res.all_india_file ? h('div', null, 'All-India reference file kept aside (not zipped).') : null,
        h('div', null, 'Folder: ', h('code', null, res.output_dir)))));
}

function LogView({runId}) {
  const data = usePoll(() => API.logs(runId), 2000, [runId]);
  const boxRef = useRef(null);
  const lines = (data && data.lines) || [];
  useEffect(() => { const b = boxRef.current; if (b) b.scrollTop = b.scrollHeight; }, [lines.length]);
  return h('div', {className: 'card'},
    h('div', {className: 'hd'}, h('h2', null, 'Live log'),
      lines.length ? h('span', {className: 'count'}, lines.length) : null),
    h('div', {className: 'bd'},
      h('div', {className: 'logs', ref: boxRef},
        lines.slice(-500).map((l, i) =>
          h('div', {key: i, className: l.level},
            h('span', {className: 'ts'}, '[' + clock(l.ts) + '] '),
            l.msg)))));
}

function DetailPane({id}) {
  const run = usePoll(() => id ? API.run(id) : Promise.resolve(null), 1500, [id]);

  if (!id) return h('div', {className: 'detail'},
    h('div', {className: 'emptyCard'},
      h('div', {className: 'empty'},
        h('div', {className: 'mark'}, '📊'),
        h('h3', null, 'Select a run'),
        h('p', null, 'Start a run on the left, or pick one from the list to see live progress, captcha prompts and downloads.'))));

  if (!run) return h('div', {className: 'detail'},
    h('div', {className: 'emptyCard'}, h('div', {className: 'empty'},
      h('span', {className: 'spin', style: {borderColor: 'rgba(14,165,233,.35)', borderTopColor: '#0ea5e9'}}))));

  const active = ['queued', 'running', 'captcha_wait'].includes(run.state);
  return h('div', {className: 'detail'},
    h('div', {className: 'card'},
      h('div', {className: 'dhead'},
        h('div', {className: 'top'},
          h('span', {className: 'flow'}, flowName(run.flow)),
          h(Pill, {state: run.state}),
          h('span', {className: 'time'}, clock(run.started) + ' → ' + clock(run.finished))),
        h('div', {className: 'runid', style: {marginTop: 4}}, '#' + run.id),
        h('div', {className: 'prog'}, h(Bar, {value: pct(run), animate: run.state === 'running'})),
        h('div', {className: 'progmeta'},
          h('span', null, h('b', null, run.progress_done + ' / ' + (run.progress_total || '?')),
            run.current_item ? '  ·  ' + run.current_item : ''),
          h('span', null, pct(run) + '%')),
        active && h('div', {className: 'actions'},
          h('button', {className: 'btn ghost', onClick: () => API.cancel(run.id)}, 'Cancel run')),
        run.error && h('div', {className: 'errbox'}, run.error))),
    run.captcha && h(Captcha, {run}),
    run.result && Object.keys(run.result).length ? h(Stats, {run}) : null,
    h(Downloads, {run}),
    h(LogView, {runId: run.id}));
}

function App() {
  const [sel, setSel] = useState(null);
  const runs = usePoll(API.runs, 2000, []) || [];
  useEffect(() => { if (!sel && runs.length) setSel(runs[0].id); }, [runs.length]);
  return h('div', {className: 'app'},
    h(Header),
    h('div', {className: 'body'},
      h('div', {className: 'col'},
        h(StartCard, {onStarted: setSel}),
        h(ScheduleCard),
        h(RunsCard, {runs, sel, onSelect: setSel})),
      h(DetailPane, {id: sel})));
}

ReactDOM.createRoot(document.getElementById('root')).render(h(App));
