const $ = id => document.getElementById(id);
let profile = 'general', offset = 0, total = 0, sequence = 0, timer;
const limit = 50;
const money = value => new Intl.NumberFormat('en-US', {style:'currency', currency:'USD', maximumFractionDigits:0}).format(value);
function node(tag, text, cls) { const el = document.createElement(tag); if(text !== undefined) el.textContent = text; if(cls) el.className = cls; return el; }
async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed');
  return data;
}
function error(message) { $('error').textContent = message; $('error').hidden = !message; }
function row(item, index) {
  const el = node('article', undefined, 'domain-row');
  el.append(node('span', String(offset + index + 1).padStart(2, '0'), 'number'));
  const info = node('div'), name = node('div', item.domain.slice(0, -4), 'name');
  name.append(node('span', '.com')); info.append(name);
  info.append(node('span', [...new Set(item.observations.map(o => o.source))].join(' · '), 'source'));
  const priced = item.recent_price_usd !== null;
  info.append(node('div', priced ? `${money(item.recent_price_usd)} observed asking price · availability unverified` : 'No recent price evidence · availability unknown', priced ? 'price known' : 'price'));
  el.append(info);
  const details = node('details', undefined, 'score-wrap'), summary = node('summary');
  summary.append(node('span', item.score.toFixed(1), 'score'), node('span', 'view score'));
  details.append(summary);
  const breakdown = node('div', undefined, 'breakdown');
  for (const [key, feature] of Object.entries(item.ranking.features)) {
    const p = node('p');
    p.append(node('b', `${key} · ${feature.value}/100 · weight ${feature.weight}%`), node('br'), node('span', feature.reason));
    breakdown.append(p);
  }
  breakdown.append(node('p', `Model: ${item.ranking.version} / ${item.ranking.profile}`));
  for (const observation of item.observations) {
    const p = node('p', `${observation.source} · ${observation.kind} · observed ${observation.observed_at}${observation.price_usd == null ? '' : ' · ' + money(observation.price_usd)}`);
    if (observation.listing_url) {
      const link = node('a', ' Open source'); link.href = observation.listing_url; link.target = '_blank'; link.rel = 'noopener noreferrer'; p.append(link);
    }
    breakdown.append(p);
  }
  details.append(breakdown); el.append(details);
  const actions = node('div', undefined, 'actions');
  for (const [verdict, label] of [['keep','Keep'],['reject','Pass']]) {
    const button = node('button', label); button.setAttribute('aria-pressed', String(item.review === verdict));
    button.setAttribute('aria-label', `${label} ${item.domain}`);
    button.onclick = async () => {
      actions.querySelectorAll('button').forEach(b => b.disabled = true);
      try {
        await api('/api/review', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({domain:item.domain, review:item.review === verdict ? null : verdict})});
        await load();
      } catch (e) { error(e.message); actions.querySelectorAll('button').forEach(b => b.disabled = false); }
    };
    actions.append(button);
  }
  el.append(actions); return el;
}
async function load() {
  const request = ++sequence;
  const params = new URLSearchParams({profile, offset, limit, q:$('query').value, budget:$('budget').value, days:$('days').value, review:$('review').value});
  error('');
  try {
    const [data, stats] = await Promise.all([api('/api/domains?' + params), api('/api/stats')]);
    if (request !== sequence) return;
    total = data.total;
    if (offset >= total && offset > 0) { offset = Math.max(0, Math.ceil(total / limit) - 1) * limit; return load(); }
    $('results').replaceChildren(...data.items.map(row));
    $('result-count').textContent = `${total.toLocaleString()} names in this view`;
    $('stats').replaceChildren(node('strong', stats.domains.toLocaleString()), node('span', ` indexed · ${stats.kept} kept`));
    $('page').textContent = total ? `${offset + 1}–${Math.min(offset + limit, total)} of ${total.toLocaleString()}` : 'No results';
    $('prev').disabled = offset === 0; $('next').disabled = offset + limit >= total;
    if (!data.items.length) {
      const empty = node('div', undefined, 'empty');
      empty.append(node('strong', stats.domains ? 'No names match these filters.' : 'Your index starts here.'));
      empty.append(node('span', $('budget').value ? 'The demo has no price evidence. Import dated marketplace prices or choose “All names”.' : 'Try another filter, or import a domain list from the terminal.'));
      if (!stats.domains) empty.append(node('code', 'python3 -m gooddomains import data/demo.txt --source demo'));
      $('results').append(empty);
    }
  } catch (e) { if (request === sequence) error(e.message); }
}
async function init() {
  try {
    const profiles = await api('/api/profiles');
    for (const [key, value] of Object.entries(profiles)) {
      const button = node('button', value.label); button.setAttribute('aria-pressed', String(key === profile));
      button.onclick = () => {
        profile = key; offset = 0;
        [...$('profiles').children].forEach(b => b.setAttribute('aria-pressed', String(b === button)));
        $('profile-note').textContent = value.description; load();
      };
      $('profiles').append(button);
    }
    $('profile-note').textContent = profiles[profile].description;
    $('filters').onsubmit = e => e.preventDefault();
    $('query').oninput = () => { ++sequence; clearTimeout(timer); timer = setTimeout(() => { offset = 0; load(); }, 180); };
    for (const id of ['budget','days','review']) $(id).onchange = () => { offset = 0; load(); };
    $('prev').onclick = () => { offset = Math.max(0, offset - limit); load(); };
    $('next').onclick = () => { offset += limit; load(); };
    await load();
  } catch (e) { error(e.message); }
}
init();
