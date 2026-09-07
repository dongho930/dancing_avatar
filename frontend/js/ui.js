// UI 흐름: 잡 생성 → 폴링 → 재생/생성영상 표시.
async function processYoutube() {
  const url = document.getElementById('youtube-url').value;
  if (!url) return alert('유튜브 URL을 입력하세요.');
  const BACKEND_URL = getBackendUrl();

  document.getElementById('loading').style.display = 'block';

  try {
    const target = ((document.getElementById('target') || {}).value || 'auto');
    let refB64 = '';
    if (target === 'face') {
      setProgress(0, '기준 얼굴사진 읽는 중...');
      refB64 = await readRefPhoto();
      if (!refB64) { alert('얼굴사진을 먼저 선택하세요.'); document.getElementById('loading').style.display = 'none'; return; }
    }
    const created = await fetch(`${BACKEND_URL}/api/v1/motions`, {
      method: 'POST', headers: apiHeaders(),
      body: JSON.stringify({ url, target, ref_image_b64: refB64,
        render: ((document.getElementById('render') || {}).value || 'avatar'),
        char_image_b64: await readImageInput('char-photo') })
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
