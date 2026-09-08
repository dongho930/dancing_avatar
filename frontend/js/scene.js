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
      // Mixamo 본명: 'mixamorig:LeftArm' (콜론 있음, 구버전은 콜론 없음). 정규화 후 매칭.
      const base = obj.name.replace(/^mixamorig:?/i, '').toLowerCase();
      const put = (names, key) => { if (names.includes(base)) rigNodes[key] = obj; };
      put(['spine'], 'Spine');
      put(['spine1'], 'Chest');
      put(['neck'], 'Neck');
      put(['head'], 'Head');
      put(['hips'], 'Hips');
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
});

// 서버 오일러각 → 본 (댐핑 적용). 본이 없으면 조용히 스킵.
function dampQuat(bone, x, y, z) {
  _targetQuat.setFromEuler(new THREE.Euler(x, y, z, 'XYZ'));
  if (DAMP >= 1) bone.quaternion.copy(_targetQuat);
  else bone.quaternion.slerp(_targetQuat, DAMP);
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
