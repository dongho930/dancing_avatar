// UI 흐름: 잡 생성 → 폴링 → 재생/생성영상 표시.
let selectedPoint = null;
let selectedPreviewId = null;

async function requestPreview() {
  const url = document.getElementById('youtube-url').value;
  if (!url) return alert('유튜브 URL을 입력하세요.');
  const BACKEND_URL = getBackendUrl();
  setProgress(5, '첫 화면 준비 중...');
  document.getElementById('loading').style.display = 'block';
  try {
    const { preview_id } = await createPreview(BACKEND_URL, url);
    selectedPreviewId = preview_id;
    selectedPoint = null;
    for (let i = 0; i < 180; i++) {
      await sleep(1000);
      const st = await fetchPreviewStatus(BACKEND_URL, preview_id);
      setProgress(st.progress || 0, `미리보기 준비 중 (${st.status}) ${st.message || ''}`);
      if (st.status === 'done') {
        showPreviewCandidates(BACKEND_URL, preview_id, st.candidates || []);
        document.getElementById('loading').style.display = 'none';
        document.getElementById('progress').style.display = 'none';
        return;
      }
      if (st.status === 'error') throw new Error(st.error || 'preview failed');
    }
    throw new Error('preview timeout');
  } catch (err) {
    document.getElementById('loading').style.display = 'none';
    alert(err.message === 'need-api-key' ? 'API 키가 필요합니다.' : '미리보기를 가져오지 못했습니다.');
    console.error('[DEBUG] preview 실패:', err);
  }
}
function showPreviewCandidates(base, previewId, candidates) {
  const box = document.getElementById('preview-box');
  const wrap = document.getElementById('preview-wrap');
  const img = document.getElementById('preview-img');
  wrap.querySelectorAll('.pv-sel,.pv-svg').forEach((el) => el.remove());
  img.src = previewFrameUrl(base, previewId);
  document.getElementById('preview-status').textContent =
    candidates.length ? `(${candidates.length}명 검출)` : '(인물 없음)';
  const svgNS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(svgNS, 'svg');
  svg.setAttribute('class', 'pv-svg');
  svg.setAttribute('viewBox', '0 0 100 100');
  svg.setAttribute('preserveAspectRatio', 'none');
  svg.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;';
  const rectPoly = (b) => [[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]];
  candidates.forEach((c) => {
    const pts = (c.poly && c.poly.length >= 3) ? c.poly : rectPoly(c.box);
    const pg = document.createElementNS(svgNS, 'polygon');
    pg.setAttribute('class', 'pv-sel');
    pg.setAttribute('points', pts.map((p) => `${p[0] * 100},${p[1] * 100}`).join(' '));
    pg.setAttribute('fill', 'rgba(77,163,255,.10)');
    pg.setAttribute('stroke', '#4da3ff');
    pg.setAttribute('stroke-width', '2');
    pg.setAttribute('vector-effect', 'non-scaling-stroke');
    pg.style.cursor = 'pointer';
    const title = document.createElementNS(svgNS, 'title');
    title.textContent = `#${c.index + 1} (score ${c.score})`;
    pg.appendChild(title);
    pg.onclick = () => {
      selectedPoint = c.center;
      wrap.querySelectorAll('.pv-sel').forEach((el) => {
        el.setAttribute('stroke', '#4da3ff');
        el.setAttribute('fill', 'rgba(77,163,255,.10)');
      });
      pg.setAttribute('stroke', '#ffeb3b');
      pg.setAttribute('fill', 'rgba(255,235,59,.18)');
      document.getElementById('preview-status').textContent = `#${c.index + 1} 선택됨`;
    };
    svg.appendChild(pg);
  });
  wrap.appendChild(svg);
  box.style.display = 'block';
}
async function processYoutube() {
  const url = document.getElementById('youtube-url').value;
  if (!url) return alert('유튜브 URL을 입력하세요.');
  const BACKEND_URL = getBackendUrl();

  document.getElementById('loading').style.display = 'block';

  try {
    // 대상은 항상 첫 화면 직접 지정. 미리보기에서 클릭한 좌표가 없으면 중단.
    if (!selectedPoint) {
      alert('먼저 첫 화면 미리보기에서 추적할 사람을 클릭하세요.');
      document.getElementById('loading').style.display = 'none';
      return;
    }
    const created = await fetch(`${BACKEND_URL}/api/v1/motions`, {
      method: 'POST', headers: apiHeaders(),
      body: JSON.stringify({ url, target: 'point', ref_image_b64: '',
        target_point: selectedPoint,
        render: ((document.getElementById('render') || {}).value || 'avatar'),
        char_image_b64: '' })
    });
    if (created.status === 401) { alert('API 키가 필요합니다.'); return; }
    if (created.status === 404) throw new Error('v1-missing');
    if (!created.ok) throw new Error('v1-create-failed: ' + created.status);
    const { job_id } = await created.json();
    lastJob = job_id;
    for (;;) {
      await sleep(1000);
      const s = await fetch(`${BACKEND_URL}/api/v1/motions/${job_id}`, { headers: apiHeaders() });
      const st = await s.json();
      setProgress(st.progress || 0, `처리 중 (${st.status}) ${st.message || ''}`);
      if (st.status === 'done') {
        const renderMode = ((document.getElementById('render') || {}).value || 'avatar');
        const wantRef = renderMode === 'ref' || renderMode === 'both';
        const motion = await fetchAllFrames(BACKEND_URL, job_id, st.fps);
        document.getElementById('loading').style.display = 'none';
        document.getElementById('progress').style.display = 'none';
        document.getElementById('bvh-btn').disabled = false;
        lastMotion = motion;
        document.getElementById('replay-btn').disabled = false;
        // 원본 음원이 있으면 아바타 재생과 함께 재생 (동일 길이라 싱크 일치)
        if ((st.formats || []).includes('audio')) {
          const key = apiKeyParam();
          setDanceAudio(`${BACKEND_URL}/api/v1/motions/${job_id}/audio${key ? '?api_key=' + encodeURIComponent(key) : ''}`);
        } else {
          setDanceAudio('');
        }
        if (wantRef) {
          // 참고 영상: 준비될 때까지 대기 후 아바타와 동시에 시작
          setProgress(100, '참고 영상 대기 중...');
          const refUrl = await pollGenVideo(BACKEND_URL, job_id);
          setRefVideo(refUrl || '');
        } else {
          setRefVideo('');
        }
        playMotion(motion);
        return;
      }
      if (st.status === 'error') throw new Error(st.error || 'job failed');
    }
  } catch (v1err) {
    console.warn('[DEBUG] v1 실패, legacy로 폴백:', v1err);
    try { await legacyProcessYoutube(url, getBackendUrl()); }
    catch (err) {
      document.getElementById('loading').style.display = 'none';
      alert('백엔드 통신 에러가 발생했습니다.');
      console.error('[DEBUG] fetch 실패:', err);
    }
  }
}

function replayMotion() {
  if (!lastMotion) return alert('먼저 안무 따기로 안무를 추출하세요.');
  document.getElementById('loading').style.display = 'none';
  document.getElementById('progress').style.display = 'none';
  playMotion(lastMotion);
}
