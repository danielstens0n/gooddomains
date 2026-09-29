const $ = id => document.getElementById(id);
let offset = 0, total = 0, sequence = 0, timer;
const limit = 50;
const filterDefaults = {'min-length':'1', 'max-length':'63', 'name-type':'', sort:'shortest', review:'', budget:'', days:'7'};
const typeLabels = {two_words:'Two-word compound', invented:'Made-up word', single_word:'Single real word', affixed:'Word + affix', unclassified:'Unclassified'};
const money = (value, currency='USD') => new Intl.NumberFormat('en-US', {style:'currency', currency, maximumFractionDigits:2}).format(value);
function node(tag, text, cls) { const el = document.createElement(tag); if(text !== undefined) el.textContent = text; if(cls) el.className = cls; return el; }
async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed');
  return data;
}
function error(message) { $('error').textContent = message; $('error').hidden = !message; }
function row(item) {
  const el = node('article', undefined, 'domain-row');
  if (item.review) el.classList.add('reviewed');
  const info = node('div'), name = node('div', item.domain.slice(0, -4), 'name');
  name.append(node('span', '.com')); info.append(name);
  const check = item.latest_check;
  let evidence = `${item.length} letters · Checked ${new Date(check.checked_at).toLocaleDateString()} · ${check.provider}`;
  info.append(node('div', evidence, 'price'));
  info.append(node('div', check.price == null ? 'Registration price not quoted' : `${money(check.price, check.currency)} / ${check.term_months} months`, 'price known'));
  el.append(info);
  const type = node('div', typeLabels[item.name_type] || 'Unclassified', 'name-type');
  el.append(type);
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
  const params = new URLSearchParams({offset, limit, q:$('query').value, budget:$('budget').value, days:$('days').value, review:$('review').value, min_length:$('min-length').value || '1', max_length:$('max-length').value || '63', name_type:$('name-type').value, sort:$('sort').value});
  error('');
  try {
    const [data, stats] = await Promise.all([api('/api/discover?' + params), api('/api/stats')]);
    if (request !== sequence) return;
    total = data.total;
    if (offset >= total && offset > 0) { offset = Math.max(0, Math.ceil(total / limit) - 1) * limit; return load(); }
    $('results').replaceChildren(...data.items.map(row));
    $('result-count').textContent = `${total.toLocaleString()} available names`;
    $('page').textContent = total ? `${offset + 1}–${Math.min(offset + limit, total)} of ${total.toLocaleString()}` : 'No results';
    $('prev').disabled = offset === 0; $('next').disabled = offset + limit >= total;
    if (!data.items.length) {
      const empty = node('div', undefined, 'empty');
      empty.append(node('strong', stats.domains ? 'No matches' : 'No names yet'));
      empty.append(node('span', 'No recently confirmed available names match. Adjust the filters or refresh registrar checks.'));

      $('results').append(empty);
    }
  } catch (e) { if (request === sequence) error(e.message); }
}
async function init() {
  try {
    $('filters').onsubmit = e => e.preventDefault();
    $('reset-filters').onclick = () => {
      for (const [id, value] of Object.entries(filterDefaults)) $(id).value = value;
      $('query').value = ''; offset = 0; load();
    };
    $('query').oninput = () => { ++sequence; clearTimeout(timer); timer = setTimeout(() => { offset = 0; load(); }, 180); };
    for (const id of Object.keys(filterDefaults)) $(id).onchange = () => { offset = 0; load(); };
    $('prev').onclick = () => { offset = Math.max(0, offset - limit); load(); };
    $('next').onclick = () => { offset += limit; load(); };
    await load();
  } catch (e) { error(e.message); }
}
init();
