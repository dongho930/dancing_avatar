// 모션 재생 (full 단일): Kalidokit 변환 + 서버 보조관절(손·발·목·머리).
function setDanceAudio(url) {
  const box = document.getElementById('audio-box');
  const el = document.getElementById('dance-audio');
  if (!url || !el) { if (box) box.style.display = 'none'; return; }
  el.src = url;
  if (box) box.style.display = 'block';
}
// 참고 영상 URL 등록 (아바타 재생과 동시에 재생/정지). ''이면 패널 숨김.
function setRefVideo(url) {
  const box = document.getElementById('genvideo-box');
  const el = document.getElementById('genvideo');
  if (!url || !el) { if (box) box.style.display = 'none'; if (el) el.removeAttribute('src'); return; }
  el.src = url;
  if (box) box.style.display = 'block';
}
function _playMedia(el) {
  if (!el || !el.src) return false;
  try { el.currentTime = 0; el.play().catch(() => {}); } catch (e) { return false; }
  return true;
}
function playMotion(motionData) {
  if (window.__playTimer) { try { clearInterval(window.__playTimer); } catch (e) {} window.__playTimer = null; }
  let frameIdx = 0;
  const interval = 1000 / (motionData.fps || 30);
  const audio = document.getElementById('dance-audio');
  const refvid = document.getElementById('genvideo');
  const useAudio = !!(audio && audio.src);
  const useRef = !!(refvid && refvid.src);
  _playMedia(useAudio ? audio : null);
  _playMedia(useRef ? refvid : null);

  const timer = window.__playTimer = setInterval(() => {
    if (frameIdx >= motionData.frames.length) {
      clearInterval(timer);
      if (window.__playTimer === timer) window.__playTimer = null;
      if (useAudio) { try { audio.pause(); } catch (e) {} }
      if (useRef) { try { refvid.pause(); } catch (e) {} }
      return;
    }

    const frame = motionData.frames[frameIdx];
    applyRoot(frame);
    applyHands(frame.handJoints);
    // 발·목·머리는 서버 계산값 (Kalidokit 출력에 없음)
    const feet = frame.footJoints || null;
    if (feet) {
      for (const [name, e] of Object.entries(feet)) {
        const bone = rigNodes[name];
        if (bone && e && e.length === 3) dampQuat(bone, e[0], e[1], e[2]);
      }
    }
    const nk = frame.neckJoints || null;
    if (nk) {
      if (nk.neck && rigNodes.Neck) dampQuat(rigNodes.Neck, nk.neck[0], nk.neck[1], nk.neck[2]);
      if (nk.head && rigNodes.Head) dampQuat(rigNodes.Head, nk.head[0], nk.head[1], nk.head[2]);
    }
    // 서버 FK가 있으면 그대로 적용 (모델상대값이라 감쇠 불필요). 구 저장분만 Kalidokit 폴백.
    if (frame.fkJoints) {
      applyFK(frame.fkJoints);
    } else {
    const lms = frame;
    if ((lms.poseWorldLandmarks || []).length > 0 && (lms.poseLandmarks || []).length > 0) {
      const poseRig = Kalidokit.Pose.solve(lms.poseWorldLandmarks, lms.poseLandmarks, {
        runtime: 'mediapipe',
        enableLockAxis: true
      });

      if (poseRig) {
        applyRotationDamped(rigNodes.Spine, poseRig.Spine, 0.45);
        applyRotationDamped(rigNodes.Chest, poseRig.Chest, 0.25);
        // Hips는 회전만 (position은 화면 정규화 단위라 루트 시스템과 충돌)
        if (poseRig.Hips && poseRig.Hips.rotation) applyRotationDamped(rigNodes.Hips, poseRig.Hips.rotation, 0.7);
        applyRotation(rigNodes.LeftUpperArm, poseRig.LeftUpperArm);
        applyRotation(rigNodes.LeftLowerArm, poseRig.LeftLowerArm);
        applyRotation(rigNodes.RightUpperArm, poseRig.RightUpperArm);
        applyRotation(rigNodes.RightLowerArm, poseRig.RightLowerArm);
        applyRotation(rigNodes.LeftUpperLeg, poseRig.LeftUpperLeg);
        applyRotation(rigNodes.LeftLowerLeg, poseRig.LeftLowerLeg);
          applyRotation(rigNodes.RightUpperLeg, poseRig.RightUpperLeg);
          applyRotation(rigNodes.RightLowerLeg, poseRig.RightLowerLeg);
        }
      }
    }
    frameIdx++;
  }, interval);
}
