// Three.js 씬 + 아바타 본 매핑 + 회전/루트/손 적용.
// 재생 튜닝: ?damp=회전수렴(0~1, 기본 0.5) ?root=0 루트이동 끄기
// ?rootScale=이동배율(기본 1) ?rootLerp=이동수렴(기본 0.5)
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x222222);

const camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 0.1, 100);
camera.position.set(0, 1.2, 3);

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(window.innerWidth, window.innerHeight);
document.body.appendChild(renderer.domElement);

const controls = new THREE.OrbitControls(camera, renderer.domElement);
controls.target.set(0, 1, 0);

const light = new THREE.DirectionalLight(0xffffff, 1.2);
light.position.set(1, 2, 3);
scene.add(light);
scene.add(new THREE.AmbientLight(0xffffff, 0.6));

let rigNodes = {};
let modelRoot = null;
const basePos = new THREE.Vector3(0, 0, 0);
const _targetPos = new THREE.Vector3();
const _targetQuat = new THREE.Quaternion();
const QP = new URLSearchParams(location.search);
const DAMP = Math.min(1, Math.max(0, parseFloat(QP.get('damp') ?? '0.3') || 0));
const ROOT_ON = (QP.get('root') ?? '1') !== '0';
const ROOT_SCALE = parseFloat(QP.get('rootScale') ?? '1') || 1;
const ROOT_LERP = Math.min(1, Math.max(0, parseFloat(QP.get('rootLerp') ?? '0.5') || 0));
// 바닥: 발이 뚫리는지 판단하는 눈금. Xbot 발바닥 휴지 높이가 y=0.
// ?ground=0 으로 끄기.
const GROUND_ON = (QP.get('ground') ?? '1') !== '0';
if (GROUND_ON) {
  const grid = new THREE.GridHelper(10, 20, 0x888888, 0x333333);
  grid.position.y = 0;
  scene.add(grid);
  const slab = new THREE.Mesh(
    new THREE.PlaneGeometry(10, 10),
    new THREE.MeshBasicMaterial({ color: 0x1a1a1a, transparent: true, opacity: 0.55 }));
  slab.rotation.x = -Math.PI / 2;
  slab.position.y = -0.002;
  scene.add(slab);
}
const fingerBones = { Left: { wrist: null, fingers: { Thumb: [], Index: [], Middle: [], Ring: [], Pinky: [] } },
                      Right: { wrist: null, fingers: { Thumb: [], Index: [], Middle: [], Ring: [], Pinky: [] } } };
const loader = new THREE.GLTFLoader();
loader.load('https://threejs.org/examples/models/gltf/Xbot.glb', (gltf) => {
  const model = gltf.scene;
  scene.add(model);
  modelRoot = model;
  basePos.copy(model.position);

  model.traverse((obj) => {
    if (obj.isBone) {
      // 휴지자세 저장: 서버 오일러값은 Xbot 바인드 상대 회전이라,
      // 휴지가 identity가 아닌 아바타는 rest와 합성해야 뒤집히지 않는다.
      obj.userData.restQ = obj.quaternion.clone();
      // Mixamo 본명: 'mixamorig:LeftArm' (콜론 있음, 구버전은 콜론 없음). 정규화 후 매칭.
      const base = obj.name.replace(/^mixamorig:?/i, '').toLowerCase();
      const put = (names, key) => { if (names.includes(base)) rigNodes[key] = obj; };
      put(['spine'], 'Spine');
      put(['spine1'], 'Chest');
      put(['neck'], 'Neck');
      put(['head'], 'Head');
      put(['hips'], 'Hips');
      put(['spine2'], 'Spine2'); // 방향 리타겟용 중계 (애니메이션 안 걸음)
      put(['leftshoulder'], 'LeftShoulder');
      put(['rightshoulder'], 'RightShoulder');
      put(['leftarm'], 'LeftUpperArm');
      put(['leftforearm'], 'LeftLowerArm');
      put(['rightarm'], 'RightUpperArm');
      put(['rightforearm'], 'RightLowerArm');
      put(['leftupleg'], 'LeftUpperLeg');
      put(['leftleg'], 'LeftLowerLeg');
      put(['rightupleg'], 'RightUpperLeg');
      put(['rightleg'], 'RightLowerLeg');
      put(['leftfoot'], 'LeftFoot');
      put(['rightfoot'], 'RightFoot');
      put(['lefttoebase'], 'LeftToe');
      put(['righttoebase'], 'RightToe');
      // 손: 좌우 판별 + 손목/손가락 마디 수집 (모델에 손가락 본이 없으면 빈 채로 둠)
      const side = /left/i.test(obj.name) ? 'Left' : (/right/i.test(obj.name) ? 'Right' : null);
      if (side) {
        const finger = ['Thumb', 'Index', 'Middle', 'Ring', 'Pinky'].find((f) => new RegExp(f, 'i').test(obj.name));
        if (finger) {
          const m = obj.name.match(/(\d+)\s*$/);
          fingerBones[side].fingers[finger].push({ bone: obj, idx: m ? parseInt(m[1], 10) : 99 });
        } else if (/hand/i.test(obj.name)) {
          fingerBones[side].wrist = obj;
        }
      }
    }
  });
  console.log('[DEBUG] mapped bones:', Object.keys(rigNodes));
  // 손가락 마디 순서 정렬 (1→끝마디)
  for (const s of ['Left', 'Right'])
    for (const f of Object.keys(fingerBones[s].fingers))
      fingerBones[s].fingers[f].sort((a, b) => a.idx - b.idx);
  detectRetarget();
});

// 서버 오일러각 → 본 (휴지자세 합성 + 댐핑). 본이 없으면 조용히 스킵.
function dampQuat(bone, x, y, z) {
  _targetQuat.setFromEuler(new THREE.Euler(x, y, z, 'XYZ'));
  // rest 합성: Xbot은 rest=identity라 기존과 동일, 휴지가 있는 아바타는 뒤집힘 방지.
  const rest = bone.userData.restQ;
  if (rest) _targetQuat.premultiply(rest);
  if (DAMP >= 1) bone.quaternion.copy(_targetQuat);
  else bone.quaternion.slerp(_targetQuat, DAMP);
}

// ---- 방향 기반 리타겟 (휴지/축 약속이 Xbot과 다른 아바타용) ----
// Xbot 본 방향 (fk.py BIND dlocal). 서버 FK는 이 축 약속 기준으로 계산됨.
const XBOT_DIR = {
  Spine: [0, 0.995111, -0.098767], Chest: [0, 0.988802, -0.149234],
  LeftUpperArm: [1, 0, 0], RightUpperArm: [-1, 0, 0],
  LeftLowerArm: [1, 0, 0], RightLowerArm: [-1, 0, 0],
  LeftUpperLeg: [0, -0.999979, 0.006415], RightUpperLeg: [0, -0.999979, 0.006449],
  LeftLowerLeg: [0, -0.997755, -0.066974], RightLowerLeg: [0, -0.997752, -0.06701],
  LeftFoot: [0, -0.63174, 0.77518], RightFoot: [0, -0.63174, 0.77518],
};
// Xbot 월드 계산용 부모 체인 (Spine2는 항등이라 생략)
const FK_PAR = { Hips: null, Spine: 'Hips', Chest: 'Spine',
  LeftShoulder: 'Chest', RightShoulder: 'Chest',
  LeftUpperArm: 'LeftShoulder', LeftLowerArm: 'LeftUpperArm',
  RightUpperArm: 'RightShoulder', RightLowerArm: 'RightUpperArm',
  LeftUpperLeg: 'Hips', LeftLowerLeg: 'LeftUpperLeg', LeftFoot: 'LeftLowerLeg',
  RightUpperLeg: 'Hips', RightLowerLeg: 'RightUpperLeg', RightFoot: 'RightLowerLeg' };
const FK_CHAIN = ['Hips', 'Spine', 'Chest', 'LeftShoulder', 'RightShoulder',
  'LeftUpperArm', 'LeftLowerArm', 'RightUpperArm', 'RightLowerArm',
  'LeftUpperLeg', 'LeftLowerLeg', 'LeftFoot', 'RightUpperLeg', 'RightLowerLeg', 'RightFoot'];
let NEED_DIR = false;
const AVA_DIR = {}; // rig명 → 본 프레임 기준 단위 방향
function detectRetarget() {
  NEED_DIR = false;
  for (const name of Object.keys(XBOT_DIR)) {
    const bone = rigNodes[name];
    if (!bone) continue;
    // 실제 계층의 첫 본 자식 (Mixamo/RPM 모두 체인 구조)
    let kid = null;
    if (name === 'Chest') kid = rigNodes.Spine2;
    else if (name === 'LeftLowerArm') kid = (fingerBones.Left || {}).wrist;
    else if (name === 'RightLowerArm') kid = (fingerBones.Right || {}).wrist;
    else {
      bone.children.forEach((c) => { if (!kid && c.isBone) kid = c; });
    }
    if (!kid || kid.position.lengthSq() < 1e-12) continue;
    const d = kid.position.clone().normalize();
    AVA_DIR[name] = d;
    const xd = new THREE.Vector3(...XBOT_DIR[name]).normalize();
    if (d.angleTo(xd) > 0.15) NEED_DIR = true;
  }
  console.log('[DEBUG] retarget mode:', NEED_DIR ? 'direction' : 'legacy-xbot', Object.keys(AVA_DIR).length, 'dirs');
}
const _pwq = new THREE.Quaternion();
const _vv = new THREE.Vector3();
function applyFK(fk) {
  if (!fk) return;
  if (!NEED_DIR) {
    // Xbot 계열: 기존 그대로 (덮어쓰기)
    for (const [name, e] of Object.entries(fk)) {
      const bone = rigNodes[name];
      if (bone && e && e.length === 3) dampQuat(bone, e[0], e[1], e[2]);
    }
    return;
  }
  // 1. Xbot 월드 방향 계산
  const W = {};
  for (const name of FK_CHAIN) {
    const e = fk[name];
    if (!e || e.length !== 3) continue;
    const ql = new THREE.Quaternion().setFromEuler(new THREE.Euler(e[0], e[1], e[2], 'XYZ'));
    const p = FK_PAR[name];
    W[name] = (p && W[p]) ? W[p].clone().multiply(ql) : ql;
  }
  // 2. Hips는 절대 방향 그대로
  if (W.Hips && rigNodes.Hips) {
    rigNodes.Hips.parent.getWorldQuaternion(_pwq);
    _targetQuat.copy(_pwq.invert()).multiply(W.Hips);
    if (DAMP >= 1) rigNodes.Hips.quaternion.copy(_targetQuat);
    else rigNodes.Hips.quaternion.slerp(_targetQuat, DAMP);
  }
  // 3. 각 본: 아바타 자신의 축이 Xbot 월드 방향을 보도록 최소 회전
  for (const name of Object.keys(XBOT_DIR)) {
    const bone = rigNodes[name];
    const dAv = AVA_DIR[name];
    if (!bone || !dAv || !W[name]) continue;
    _vv.set(...XBOT_DIR[name]).normalize().applyQuaternion(W[name]); // 목표 월드 방향
    bone.parent.getWorldQuaternion(_pwq);
    _vv.applyQuaternion(_pwq.clone().invert()); // 부모 프레임 기준
    _targetQuat.setFromUnitVectors(dAv, _vv.clone().normalize());
    if (DAMP >= 1) bone.quaternion.copy(_targetQuat);
    else bone.quaternion.slerp(_targetQuat, DAMP);
  }
  // 4. 말단/쇄골/목은 작은 델타라 휴지 합성으로 (뒤집힘 없음)
  for (const name of ['Neck', 'Head', 'LeftShoulder', 'RightShoulder', 'LeftToe', 'RightToe']) {
    const e = fk[name];
    const bone = rigNodes[name];
    if (bone && e && e.length === 3) dampQuat(bone, e[0], e[1], e[2]);
  }
}
// 루트 이동 적용 (서버 root[x,y,z] → 모델 위치). 없으면 유지.
function applyRoot(frame) {
  if (!ROOT_ON || !modelRoot) return;
  const rt = frame.root || frame.rt;
  if (!rt || rt.length !== 3) return;
  _targetPos.set(basePos.x + rt[0] * ROOT_SCALE,
                 basePos.y + rt[1] * ROOT_SCALE,
                 basePos.z + rt[2] * ROOT_SCALE);
  if (ROOT_LERP >= 1) modelRoot.position.copy(_targetPos);
  else modelRoot.position.lerp(_targetPos, ROOT_LERP);
}
function applyHands(handJoints) {
  if (!handJoints) return;
  for (const side of ['Left', 'Right']) {
    const hj = handJoints[side];
    const fb = fingerBones[side];
    if (!hj || !fb) continue;
    if (hj.wrist && fb.wrist) {
      dampQuat(fb.wrist, hj.wrist[0], hj.wrist[1], hj.wrist[2]);
    }
    for (const [fname, segs] of Object.entries(hj.fingers || {})) {
      const bones = fb.fingers[fname] || [];
      for (let i = 0; i < bones.length; i++) {
        const e = segs[Math.min(i, segs.length - 1)];
        if (e) dampQuat(bones[i].bone, e[0], e[1], e[2]);
      }
    }
  }
}
function applyRotation(bone, rotation, damp = 1) {
  if (!bone || !rotation) return;
  dampQuat(bone, rotation.x * damp, rotation.y * damp, rotation.z * damp);
}
function applyRotationDamped(bone, rotation, damp) {
  applyRotation(bone, rotation, damp);
}

// 렌더링 루프
function render() {
  requestAnimationFrame(render);
  renderer.render(scene, camera);
}
window.addEventListener('resize', () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});
render();
