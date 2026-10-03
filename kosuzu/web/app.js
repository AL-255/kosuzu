'use strict';
const $ = id => document.getElementById(id);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = {settings:null, inventory:null, draft:null, currentView:'inventory', stockId:null, stockRequest:null, loading:false, queue:[]};
const names = {inventory:'Inventory', import:'New component', requests:'My requests', queue:'Error queue', settings:'Settings'};

async function api(path, body) {
  const options = {headers:{'X-Kosuzu':'1'}, credentials:'same-origin'};
  if (body !== undefined) { options.method = 'POST'; options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(body); }
  let response;
  try { response = await fetch('/api/' + path, options); }
  catch { throw new Error('Connection unavailable. Local requests remain saved; reconnect and sync requests.'); }
  const value = await response.json();
  if (!response.ok) {
    if (response.status === 401 && path !== 'login') signedOut();
    throw new Error(value.error || `Request failed (${response.status})`);
  }
  return value;
}

function signedOut() {
  $('workspace').hidden = true; $('login-view').hidden = false;
  state.settings = null; state.draft = null; state.inventory = null;
}
function showError(error, id='global-error') { $(id).textContent = error.message || String(error); $(id).hidden = false; }
function notice(message) { $('notice').textContent = message; $('notice').hidden = false; }
function clearMessages() { $('notice').hidden = true; $('global-error').hidden = true; }
function number(value) { return Number(value).toLocaleString(); }
function httpsLink(value) { try { return new URL(value).protocol === 'https:' ? value : ''; } catch { return ''; } }
function githubLink(number) { return `https://github.com/${state.settings.repo}/pull/${number}`; }

async function busy(button, task, pending) {
  const original = button.textContent;
  button.disabled = true; if (pending) button.textContent = pending;
  try { return await task(); }
  finally { button.disabled = false; button.textContent = original; }
}

function showView(view) {
  if (!names[view]) view = 'inventory';
  if (view === 'queue' && state.settings?.mode !== 'server') view = 'inventory';
  state.currentView = view;
  document.querySelectorAll('.view').forEach(section => { section.hidden = section.id !== 'view-' + view; });
  document.querySelectorAll('.nav').forEach(button => button.classList.toggle('active', button.dataset.view === view));
  $('breadcrumb').textContent = 'Workspace / ' + names[view];
  if (location.hash !== '#' + view) history.replaceState(null, '', '#' + view);
  if (view === 'requests') loadRequests().catch(showError);
  if (view === 'queue') loadQueue().catch(showError);
}

function renderSettings() {
  const s = state.settings;
  $('setting-repo').value = s.repo; $('setting-branch').value = s.branch;
  $('database-settings').hidden = s.role !== 'admin';
  $('server-token-label').hidden = s.mode !== 'server';
  $('llm-hosts-label').hidden = s.role !== 'admin';
  $('queue-nav').hidden = s.mode !== 'server';
  $('sync-server').hidden = s.role !== 'admin';
  $('server-token').placeholder = s.server_token_saved ? 'Saved · leave blank to keep' : 'Dedicated server GitHub token';
  $('client-token').placeholder = s.client_token_saved ? 'Saved · leave blank to keep' : 'Dedicated client GitHub token';
  $('llm-key').placeholder = s.llm_key_saved ? 'Saved · leave blank to keep' : 'Your provider API key';
  $('llm-url').value = s.llm.base_url || 'https://api.deepseek.com';
  $('llm-model').value = s.llm.model || 'deepseek-chat';
  $('llm-hosts').value = s.llm_hosts.join(', ');
  $('repo-label').textContent = s.repo || 'Set up your database';
  $('mode-label').textContent = `${s.mode === 'server' ? 'Server workspace' : 'Desktop client'} · ${s.role}`;
  const selected = $('supplier').value;
  $('supplier').innerHTML = s.suppliers.map(supplier => `<option value="${escapeHTML(supplier.key)}">${escapeHTML(supplier.name)}</option>`).join('');
  if (selected) $('supplier').value = selected;
  else $('supplier').value = 'lcsc';
  $('supplier-credentials').innerHTML = s.suppliers.filter(supplier => supplier.credential_fields.length).map(supplier => `<div class="supplier-box"><h3>${escapeHTML(supplier.name)} ${s.supplier_credentials_saved[supplier.key] ? '· credentials saved' : ''}</h3><div class="form-grid">${supplier.credential_fields.map(field => `<label>${escapeHTML(field.replaceAll('_',' '))}<input type="password" autocomplete="new-password" data-supplier="${escapeHTML(supplier.key)}" data-field="${escapeHTML(field)}" placeholder="Leave blank to keep saved credentials"></label>`).join('')}</div></div>`).join('');
}

async function boot() {
  try {
    state.settings = await api('settings');
    $('login-view').hidden = true; $('workspace').hidden = false; renderSettings();
    showView(location.hash.slice(1) || 'inventory');
    if (state.settings.client_token_saved && state.settings.repo) await loadInventory();
    else { showView('settings'); notice('Connect your GitHub database and save your client token to get started.'); }
    if (state.settings.mode === 'server') await loadQueue();
  } catch (error) { if (state.settings) showError(error); }
}

async function loadInventory() {
  const result = await api('inventory');
  state.inventory = result.inventory;
  $('offline-label').hidden = !result.stale;
  if (result.stale) notice(`Showing cached inventory from ${new Date(result.time * 1000).toLocaleString()}. ${result.warning}`);
  renderInventory();
}

function renderInventory() {
  if (!state.inventory) return;
  const all = Object.values(state.inventory.components);
  $('stat-types').textContent = number(all.length);
  $('stat-pieces').textContent = number(all.reduce((total, row) => total + row.quantity, 0));
  $('stat-low').textContent = number(all.filter(row => row.quantity < 10).length);
  $('stat-revision').textContent = number(state.inventory.revision);
  const selectedCategory = $('category-filter').value;
  const categories = [...new Set(all.map(row => row.component.category).filter(Boolean))].sort();
  $('category-filter').innerHTML = '<option value="">All categories</option>' + categories.map(c => `<option value="${escapeHTML(c)}">${escapeHTML(c)}</option>`).join('');
  $('category-filter').value = categories.includes(selectedCategory) ? selectedCategory : '';
  const query = $('search').value.trim().toLowerCase();
  const rows = all.filter(row => {
    const c = row.component;
    return (!query || [c.mpn,c.manufacturer,c.description,c.location,c.supplier_code,c.category,c.package,...Object.values(c.attributes)].join(' ').toLowerCase().includes(query)) && (!$('category-filter').value || c.category === $('category-filter').value) && (!$('low-filter').checked || row.quantity < 10);
  }).sort((a,b) => a.component.mpn.localeCompare(b.component.mpn));
  $('inventory-rows').innerHTML = rows.map(row => {
    const c = row.component, image = httpsLink(c.image_url);
    return `<tr><td><div class="component-cell">${image ? `<img class="part-img" src="${escapeHTML(image)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : '<span class="part-img part-placeholder" aria-hidden="true">▧</span>'}<div><strong>${escapeHTML(c.mpn)}</strong><small>${escapeHTML(c.manufacturer)}</small><small>${escapeHTML(c.description)}</small><span class="cell-secondary">${escapeHTML(c.supplier)} · ${escapeHTML(c.supplier_code)}</span></div></div></td><td>${escapeHTML(c.package || '—')}<div class="cell-secondary">${escapeHTML(c.category || 'Uncategorized')}</div></td><td>${escapeHTML(c.location || 'Unassigned')}</td><td><span class="stock-count ${row.quantity < 10 ? 'stock-low' : ''}">${number(row.quantity)}</span><div class="cell-secondary">pieces</div></td><td><button class="secondary" data-adjust="${escapeHTML(c.id)}">Adjust stock</button></td></tr>`;
  }).join('');
  $('inventory-empty').hidden = rows.length > 0;
  if (!rows.length && all.length) { $('inventory-empty').querySelector('h3').textContent = 'No matching components'; $('inventory-empty').querySelector('p').textContent = 'Try another search or clear the filters.'; $('empty-settings').hidden = true; }
  else { $('inventory-empty').querySelector('h3').textContent = 'Your next build starts here'; $('inventory-empty').querySelector('p').textContent = 'Import your first component from its supplier part number.'; $('empty-settings').hidden = false; }
  $('result-count').textContent = `${number(rows.length)} of ${number(all.length)} components`;
}

function openStock(ident) {
  const row = state.inventory.components[ident];
  state.stockId = ident; state.stockRequest = null;
  $('stock-part').textContent = `${row.component.manufacturer} · ${row.component.mpn}`;
  $('stock-current').textContent = `Currently ${number(row.quantity)} pieces · ${row.component.location || 'No location assigned'}`;
  $('stock-error').textContent = ''; $('stock-action').value = 'add'; $('stock-quantity').value = '1'; $('stock-note').value = '';
  $('stock-dialog').showModal();
}

function transactionNotice(result) {
  const messages = {applied:'Your request is applied to the inventory.', pending:'Request submitted to GitHub. Your server will validate and apply it.', queued:'Request saved locally. It will retry when GitHub is available.', blocked:'Your request needs a correction. Read its error below and ask the server administrator to reject it before submitting a replacement.', rejected:'This request was rejected. Check the server error queue and submit a corrected request.'};
  notice(messages[result.status] || 'Request saved.');
  showView('requests');
}

function renderReview(result) {
  state.draft = result;
  const c = result.component;
  $('review-panel').hidden = false; $('review-step').classList.add('active');
  for (const field of ['manufacturer','mpn','description','category','package','location']) $('part-' + field).value = c[field];
  $('part-attributes').value = JSON.stringify(c.attributes, null, 2);
  $('part-confirmed').checked = false; $('part-note').value = ''; $('part-quantity').value = 1;
  $('original-data').textContent = JSON.stringify(result.original, null, 2);
  $('review-warnings').innerHTML = '<strong>Review notes</strong><ul>' + c.review.warnings.map(w => `<li>${escapeHTML(w)}</li>`).join('') + '</ul>';
  const image = httpsLink(c.image_url);
  $('review-image').hidden = !image;
  if (image) $('review-image').src = image; else $('review-image').removeAttribute('src');
  const source = httpsLink(c.source_url); $('source-link').hidden = !source; if (source) $('source-link').href = source;
  $('review-panel').scrollIntoView({behavior:'smooth', block:'start'});
}

async function loadRequests() {
  const rows = await api('outbox');
  $('request-list').innerHTML = rows.length ? rows.map(row => {
    const e = row.event, r = row.result;
    const c = e.component || state.inventory?.components[e.component_id]?.component;
    return `<article class="request-card"><div><h3>${escapeHTML(c?.mpn || e.component_id)} <span class="status ${escapeHTML(r.status)}">${escapeHTML(r.status)}</span></h3><p>${e.kind === 'create' ? 'New component' : 'Stock adjustment'} · ${e.delta > 0 ? '+' : ''}${number(e.delta)} pieces · ${escapeHTML(row.repo)}</p><p>${escapeHTML(e.note)}</p>${r.error ? `<p class="error">${escapeHTML(r.error)}</p>` : ''}<p class="small">${escapeHTML(e.id)} · ${new Date(row.updated * 1000).toLocaleString()}</p></div><div>${r.url ? `<a href="${escapeHTML(httpsLink(r.url))}" target="_blank" rel="noopener noreferrer">View request #${r.number} ↗</a>` : '<span class="small">Waiting for connection</span>'}</div></article>`;
  }).join('') : '<div class="panel empty"><h3>No requests yet</h3><p>Import a component or adjust stock to send your first request.</p></div>';
}

async function loadQueue() {
  if (state.settings?.mode !== 'server') return;
  const result = await api('queue'); state.queue = result.errors;
  const open = result.errors.filter(e => e.status === 'open');
  $('queue-count').textContent = open.length; $('queue-count').hidden = !open.length;
  $('queue-warning').hidden = !open.length;
  $('queue-warning').textContent = `${open.length} unresolved ${open.length === 1 ? 'problem needs' : 'problems need'} attention. Open the Error queue to fix failed requests.`;
  $('sync-info').textContent = result.last_sync ? `Last completed sync: ${new Date(result.last_sync.time * 1000).toLocaleString()}` : 'No server sync completed yet. Save the server token and initialize your database.';
  $('error-list').innerHTML = result.errors.length ? result.errors.map(error => `<article class="request-card"><div><h3>${error.number ? 'Request #' + error.number : 'Server synchronization'} <span class="status ${error.status === 'open' ? 'rejected' : 'applied'}">${escapeHTML(error.status)}</span></h3><p class="${error.status === 'open' ? 'error' : ''}">${escapeHTML(error.message)}</p><p class="small">Updated ${new Date(error.updated * 1000).toLocaleString()}</p></div><div class="actions">${error.number ? `<a href="${escapeHTML(githubLink(error.number))}" target="_blank" rel="noopener noreferrer">View on GitHub ↗</a>` : ''}${error.status === 'open' && error.number && state.settings.role === 'admin' ? `<button class="secondary" data-queue-action="retry" data-number="${error.number}">Retry</button><button class="secondary" data-queue-action="reject" data-number="${error.number}">Reject request</button>` : ''}</div></article>`).join('') : '<div class="panel empty"><h3>All clear</h3><p>Unresolvable proposals and synchronization failures will appear here.</p></div>';
}

$('login-form').addEventListener('submit', async event => {
  event.preventDefault(); $('login-error').textContent = '';
  try { await busy(event.submitter, async () => { await api('login', {key:$('access-key').value}); $('access-key').value = ''; await boot(); }, 'Signing in…'); }
  catch (error) { showError(error,'login-error'); }
});
document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => { clearMessages(); showView(button.dataset.view); }));
$('new-component').addEventListener('click', () => { clearMessages(); showView('import'); });
$('empty-settings').addEventListener('click', () => showView(state.settings?.client_token_saved ? 'import' : 'settings'));
$('logout').addEventListener('click', async () => { try { await api('logout',{}); signedOut(); } catch(error) { showError(error); } });
const mobileLogout = document.createElement('button');
mobileLogout.className = 'text-button mobile-logout'; mobileLogout.textContent = 'Sign out';
mobileLogout.addEventListener('click', () => $('logout').click());
document.querySelector('.topbar').append(mobileLogout);
window.addEventListener('hashchange', () => { if (state.settings) showView(location.hash.slice(1)); });
$('search').addEventListener('input', renderInventory); $('category-filter').addEventListener('change', renderInventory); $('low-filter').addEventListener('change',renderInventory);
$('refresh').addEventListener('click', async event => { clearMessages(); try { await busy(event.currentTarget, loadInventory, 'Refreshing…'); } catch (error) { showError(error); } });
$('inventory-rows').addEventListener('click', event => { const button = event.target.closest('[data-adjust]'); if (button) openStock(button.dataset.adjust); });
$('inventory-rows').addEventListener('error', event => { if (event.target.tagName === 'IMG') event.target.hidden = true; }, true);
$('close-stock').addEventListener('click', () => $('stock-dialog').close());
['stock-action','stock-quantity','stock-note'].forEach(id => $(id).addEventListener('input', () => { state.stockRequest = null; }));
$('stock-form').addEventListener('submit', async event => {
  event.preventDefault(); $('stock-error').textContent = '';
  if (!state.stockRequest) state.stockRequest = crypto.randomUUID().replaceAll('-','');
  try { await busy(event.submitter, async () => { const result = await api('adjust', {request_id:state.stockRequest, component_id:state.stockId, delta:Number($('stock-quantity').value) * ($('stock-action').value === 'remove' ? -1 : 1), note:$('stock-note').value}); $('stock-dialog').close(); transactionNotice(result); }); }
  catch (error) { showError(error, 'stock-error'); }
});
$('import-form').addEventListener('submit', async event => {
  event.preventDefault(); clearMessages();
  try { await busy(event.submitter, async () => { const result = await api('import', {supplier:$('supplier').value, code:$('supplier-code').value.trim(), product_url:$('product-url').value.trim()}); renderReview(result); }, 'Retrieving & reviewing…'); }
  catch(error) { showError(error); }
});
$('discard-review').addEventListener('click', () => { state.draft = null; $('review-panel').hidden = true; $('review-step').classList.remove('active'); });
$('review-form').addEventListener('submit', async event => {
  event.preventDefault(); clearMessages();
  try { await busy(event.submitter, async () => {
    const edits = Object.fromEntries(['manufacturer','mpn','description','category','package','location'].map(k => [k,$('part-' + k).value]));
    try { edits.attributes = JSON.parse($('part-attributes').value); } catch { throw new Error('Electrical attributes must be a valid JSON object.'); }
    const result = await api('create', {draft_id:state.draft.draft_id, confirmed:$('part-confirmed').checked, quantity:Number($('part-quantity').value), edits, note:$('part-note').value});
    state.draft = null; $('review-panel').hidden = true; $('review-step').classList.remove('active'); transactionNotice(result);
  }, 'Submitting…'); } catch(error) { showError(error); }
});
$('settings-form').addEventListener('submit', async event => {
  event.preventDefault(); clearMessages(); $('settings-saved').textContent = '';
  try { await busy(event.submitter, async () => {
    const data = {llm:{base_url:$('llm-url').value.trim(), model:$('llm-model').value.trim()}};
    if ($('llm-key').value) data.llm.api_key = $('llm-key').value.trim();
    if ($('client-token').value) data.client_token = $('client-token').value.trim();
    if (state.settings.role === 'admin') { data.repo = $('setting-repo').value.trim(); data.branch = $('setting-branch').value.trim(); data.llm_hosts = $('llm-hosts').value.split(',').map(v => v.trim()).filter(Boolean); if ($('server-token').value) data.server_token = $('server-token').value.trim(); }
    const suppliers = {};
    document.querySelectorAll('[data-supplier]').forEach(input => { if (input.value) { suppliers[input.dataset.supplier] ||= {}; suppliers[input.dataset.supplier][input.dataset.field] = input.value.trim(); } });
    if (Object.keys(suppliers).length) data.suppliers = suppliers;
    state.settings = await api('settings', data);
    ['client-token','server-token','llm-key'].forEach(id => { $(id).value = ''; }); renderSettings(); $('settings-saved').textContent = 'Saved.';
    notice('Connections saved. Initialize a new database, or refresh Inventory to connect.');
  }, 'Saving…'); } catch(error) { showError(error); }
});
$('initialize').addEventListener('click', async event => { clearMessages(); try { await busy(event.currentTarget, async () => { const result = await api('initialize',{}); notice(result.created ? 'Database initialized. You can now import components.' : 'The inventory database already exists.'); if (state.settings.client_token_saved) await loadInventory(); }, 'Initializing…'); } catch(error) { showError(error); } });
$('retry-outbox').addEventListener('click', async event => { clearMessages(); try { await busy(event.currentTarget, async () => { await api('flush',{}); await loadRequests(); if (state.settings.client_token_saved) await loadInventory(); }, 'Synchronizing…'); } catch(error) { showError(error); } });
$('sync-server').addEventListener('click', async event => { clearMessages(); try { await busy(event.currentTarget, async () => { await api('sync',{}); await loadQueue(); if (state.settings.client_token_saved) await loadInventory(); }, 'Synchronizing…'); } catch(error) { showError(error); await loadQueue().catch(() => {}); } });
$('error-list').addEventListener('click', async event => {
  const button = event.target.closest('[data-queue-action]'); if (!button) return;
  if (button.dataset.queueAction === 'reject' && !confirm(`Reject request #${button.dataset.number}? Its quantity change will not be applied. Submit a corrected request afterwards.`)) return;
  clearMessages(); try { await busy(button, async () => { await api('queue',{number:Number(button.dataset.number),action:button.dataset.queueAction}); await loadQueue(); }); } catch(error) { showError(error); }
});

setInterval(async () => {
  if (!state.settings || document.hidden || state.loading) return;
  state.loading = true;
  try {
    if (state.settings.mode === 'server') await loadQueue();
    if (state.currentView === 'requests') await loadRequests();
    if (state.currentView === 'inventory' && state.settings.client_token_saved && state.settings.repo) await loadInventory();
  } catch { /* Explicit actions show failures; background refresh keeps last visible state. */ }
  finally { state.loading = false; }
}, 30000);
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
boot();
