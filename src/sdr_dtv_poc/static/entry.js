// SPDX-License-Identifier: GPL-3.0-or-later
// Temporary integration entry; #18/#27-#29 own the product UI.
let sessionId = null;
let token = null;
let timer = null;
const status = document.querySelector('#status');
async function request(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-CSRF-Token': token},
    body: JSON.stringify(body)
  });
  const value = await response.json();
  status.textContent = JSON.stringify(value, null, 2);
  if (!response.ok) throw new Error(value.code);
  return value;
}
async function poll() {
  try {
    const session = await request(`/api/sessions/${sessionId}`);
    if (['completed', 'failed', 'interrupted'].includes(session.state)) {
      document.querySelector('#stop').disabled = true;
      return;
    }
    timer = setTimeout(poll, 500);
  } catch (error) { status.textContent = String(error); }
}
function handle(action) {
  return () => action().catch(error => { status.textContent = String(error); });
}
document.querySelector('#diagnose').onclick = handle(() => request('/api/diagnostics'));
document.querySelector('#start').onclick = handle(async () => {
  clearTimeout(timer);
  if (!token) token = (await request('/api/bootstrap')).csrf_token;
  const session = await request('/api/sessions', {request_id: crypto.randomUUID()});
  sessionId = session.id;
  document.querySelector('#stop').disabled = false;
  await poll();
});
document.querySelector('#stop').onclick = handle(() => request(`/api/sessions/${sessionId}/stop`, {}));
window.addEventListener('pagehide', () => clearTimeout(timer));
