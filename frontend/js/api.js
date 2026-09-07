// 백엔드 통신: 잡 생성 폴링은 ui.js, 프레임/파일 조회는 여기.
async function fetchAllFrames(base, jobId, fps) {
  // 모바일 데이터 절약: stride=2로 절반만 (데스크톱은 1). 필요시 URL ?stride=1
  const stride = new URLSearchParams(location.search).get('stride') || (window.innerWidth < 768 ? 2 : 1);
  let page = 0, frames = [];
  for (;;) {
    const r = await fetch(`${base}/api/v1/motions/${jobId}/frames?page=${page}&page_size=120&stride=${stride}`, { headers: apiHeaders() });
    if (!r.ok) throw new Error('frames fetch failed: ' + r.status);
    const j = await r.json();
    frames = frames.concat(j.frames);
    setProgress(100, `프레임 수신 중... ${frames.length}/${j.filtered_frames}`);
    if (j.frames.length === 0 || frames.length >= j.filtered_frames) return { fps: j.fps || fps, total_frames: j.total_frames, frames };
    page++;
  }
}
async function readImageInput(id) {
  const inp = document.getElementById(id);
  const f = inp && inp.files && inp.files[0];
  if (!f) return '';
  const buf = await f.arrayBuffer();
  let bin = '';
  const bytes = new Uint8Array(buf);
  for (let i = 0; i < bytes.length; i += 8192)
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 8192));
  return 'data:' + (f.type || 'image/jpeg') + ';base64,' + btoa(bin);
}
async function readRefPhoto() { return readImageInput('ref-photo'); }
async function refVideoUrl(base, jobId) {
  const key = apiKeyParam();
  return `${base}/api/v1/motions/${jobId}/video${key ? '?api_key=' + encodeURIComponent(key) : ''}`;
}
// 참고 영상 준비 대기 → URL 반환 (없으면 null). 아바타와 함께 재생용.
async function pollGenVideo(base, jobId) {
  const box = document.getElementById('genvideo-box');
  for (let i = 0; i < 600; i++) {  // 최대 ~10분 (확산 추론 대기)
    await sleep(1000);
    try {
      const s = await fetch(`${base}/api/v1/motions/${jobId}`, { headers: apiHeaders() });
      const st = await s.json();
      const fmts = st.formats || [];
      if (fmts.includes('video')) {
        const url = await refVideoUrl(base, jobId);
        document.getElementById('genvideo').src = url;
        box.style.display = 'block';
        setProgress(100, '참고 영상 준비됨');
        return url;
      }
      if (st.status === 'error') return null;
      setProgress(st.progress || 0, `참고 영상 대기 중... ${st.message || ''}`);
    } catch (e) { /* 다음 폴링 */ }
  }
  return null;
}
async function downloadBVH() {
  if (!lastJob) return alert('먼저 안무를 변환하세요.');
  const base = getBackendUrl();
  const r = await fetch(`${base}/api/v1/motions/${lastJob}/bvh`, { headers: apiHeaders() });
  if (!r.ok) return alert('BVH 다운로드 실패: ' + r.status);
  const blob = await r.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `motion_${lastJob}.bvh`;
  a.click();
}
async function legacyProcessYoutube(url, BACKEND_URL) {
    console.log('[DEBUG] legacy 요청 →', `${BACKEND_URL}/api/process-youtube`);
    const response = await fetch(`${BACKEND_URL}/api/process-youtube`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: url })
    });
    const rawText = await response.text();
    if (!response.ok) { alert(`서버 에러 (status: ${response.status})`); return; }
    const resData = JSON.parse(rawText);
    document.getElementById('loading').style.display = 'none';
    if (resData && resData.status === 'success') {
      lastMotion = resData.data;
      document.getElementById('replay-btn').disabled = false;
      playMotion(resData.data);
    }
    else alert('모션 추출 실패');
}
