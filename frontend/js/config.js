// 설정·공유 상태. ?backend= / localStorage / 같은출처 순으로 해결
// (WebView·PWA·로컬파일 모두 동작, 설정은 기기에 저장)
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function resolveBackendUrl() {
  const q = new URLSearchParams(location.search).get('backend');
  const saved = localStorage.getItem('BACKEND_URL') || '';
  const input = (document.getElementById('backend-url') || {}).value || '';
  const fallback = "https://zany-invention-p7pj9rqwxvg9f99x-8000.app.github.dev";
  return (input.trim() || q || saved || '').trim() || (location.origin.startsWith('http') ? location.origin : fallback);
}
function getBackendUrl() {
  const u = resolveBackendUrl().replace(/\/$/, '');
  try { localStorage.setItem('BACKEND_URL', u); } catch (e) {}
  return u;
}
(function initBackendInput() {
  const el = document.getElementById('backend-url');
  if (el) el.value = new URLSearchParams(location.search).get('backend') || localStorage.getItem('BACKEND_URL') || '';
  const k = document.getElementById('api-key');
  if (k) k.value = localStorage.getItem('API_KEY') || '';
})();
function apiHeaders() {
  const h = { 'Content-Type': 'application/json' };
  const k = ((document.getElementById('api-key') || {}).value || localStorage.getItem('API_KEY') || '').trim();
  if (k) { h['X-API-Key'] = k; try { localStorage.setItem('API_KEY', k); } catch (e) {} }
  return h;
}
function apiKeyParam() {
  try { return ((document.getElementById('api-key') || {}).value || localStorage.getItem('API_KEY') || '').trim(); }
  catch (e) { return ''; }
}

let lastJob = null;
let lastMotion = null;

function setProgress(pct, msg) {
  const bar = document.getElementById('progress');
  const lab = document.getElementById('loading');
  if (bar) { bar.style.display = 'block'; bar.value = pct || 0; }
  if (lab && msg) { lab.style.display = 'block'; lab.textContent = msg; }
}
