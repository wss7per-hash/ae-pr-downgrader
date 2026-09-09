// 提取 AEP-Downgrader.html 内联脚本的核心逻辑，用真实样本验证
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, 'AEP-Downgrader.html'), 'utf8');
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.error('未找到 script'); process.exit(1); }

// 只提供 window，不提供 document -> 脚本在 UI 部分前 return，核心逻辑已挂到 window.AEPCore
global.window = {};
eval(m[1]);
const C = global.window.AEPCore;
if (!C) { console.error('AEPCore 未导出'); process.exit(1); }

let pass = 0, fail = 0;
function chk(name, cond, extra) {
  if (cond) { pass++; console.log('  PASS  ' + name + (extra ? '  ' + extra : '')); }
  else { fail++; console.log('  FAIL  ' + name + (extra ? '  ' + extra : '')); }
}

console.log('== 1. 版本字位域解码（AE 2025 实测样本 0x0F08062F）==');
const d = C.decodeWord(0x0F08062F);
chk('major=25', d.major === 25, 'got ' + d.major);
chk('minor=0', d.minor === 0);
chk('patch=0', d.patch === 0);
chk('build=47', d.build === 47, 'got ' + d.build);
chk('os=12 (Windows)', d.os === 12, 'got ' + d.os);
chk('非 beta', d.beta === false);
chk('version 字符串', d.version === '25.0.0', 'got ' + d.version);

console.log('\n== 2. makeWord 往返一致性 ==');
const w = C.makeWord(25, 0, 0, 12, 47, false);
chk('makeWord(25,0,0,12,47) === 0x0F08062F', w === 0x0F08062F,
    'got 0x' + w.toString(16).toUpperCase());

console.log('\n== 3. 最小改动策略：只替换 major ==');
const w24 = C.replaceMajorInWord(0x0F08062F, 24);
const d24 = C.decodeWord(w24);
chk('major 变 24', d24.major === 24, 'got ' + d24.major);
chk('平台保留 Win', d24.os === 12);
chk('build 保留 47', d24.build === 47);
const w23 = C.replaceMajorInWord(0x0F08062F, 23);
chk('major 变 23', C.decodeWord(w23).major === 23);
const w22 = C.replaceMajorInWord(0x0F08062F, 22);
chk('major 变 22', C.decodeWord(w22).major === 22);
const w11 = C.replaceMajorInWord(0x0F08062F, 11);
chk('major 变 11 (CS6)', C.decodeWord(w11).major === 11);

console.log('\n== 4. 真实 AEP：AE 2025 的 secret.aep ==');
const aepPath = 'C:/Program Files/Adobe/Adobe After Effects 2025/Support Files/Required/secret.aep';
if (fs.existsSync(aepPath)) {
  const u8 = new Uint8Array(fs.readFileSync(aepPath));
  const r = C.scanAep(u8, 'secret.aep');
  chk('识别成功', r.ok === true, r.error);
  chk('源 major=25', r.srcMajor === 25, 'got ' + r.srcMajor);
  chk('格式字节 0x60', r.srcFormatByte === 0x60, 'got 0x' + r.srcFormatByte.toString(16));
  chk('定位到 3 处标记', r.hits.length === 3, 'got ' + r.hits.length);
  chk('含 svap 块', r.hits.some(h => h.note.indexOf('svap') >= 0));

  C.computeNewValues(r, 23, false);
  const out = C.applyPatch(u8, r);
  chk('长度不变', out.length === u8.length);
  let diff = [];
  for (let i = 0; i < out.length; i++) if (out[i] !== u8[i]) diff.push(i);
  // 与 Python 版 aep_core.py 的实测结果必须逐字节一致
  chk('降到 23 只改 5 个字节 @20,21,33,36,37',
      diff.length === 5 && diff.join(',') === '20,21,33,36,37', 'offsets ' + diff.join(','));
  const chk2 = C.scanBytes(out, 'secret.aep');
  chk('往返校验 major=23', chk2.ok && chk2.srcMajor === 23, 'got ' + (chk2.ok ? chk2.srcMajor : chk2.error));

  // 激进模式
  const r2 = C.scanAep(u8, 'secret.aep');
  C.computeNewValues(r2, 23, true);
  const out2 = C.applyPatch(u8, r2);
  const c2 = C.decodeWord(new DataView(out2.buffer).getUint32(36, false));
  chk('激进模式 minor/patch 归零', c2.major === 23 && c2.minor === 0 && c2.patch === 0,
      'got ' + c2.version);
} else {
  console.log('  (跳过：未找到 secret.aep)');
}

console.log('\n== 5. 真实 FFX：Cracked Tiles.ffx ==');
const ffxPath = 'C:/Program Files/Adobe/Adobe After Effects 2025/Support Files/Presets/Image - Special Effects/Cracked Tiles.ffx';
if (fs.existsSync(ffxPath)) {
  const u8 = new Uint8Array(fs.readFileSync(ffxPath));
  const r = C.scanFfx(u8, 'Cracked Tiles.ffx');
  chk('识别成功', r.ok === true, r.error);
  chk('源 major=24', r.srcMajor === 24, 'got ' + r.srcMajor);
  chk('格式字节 0x5F', r.srcFormatByte === 0x5F, 'got 0x' + r.srcFormatByte.toString(16));
  C.computeNewValues(r, 23, false);
  const out = C.applyPatch(u8, r);
  let diff = [];
  for (let i = 0; i < out.length; i++) if (out[i] !== u8[i]) diff.push(i);
  chk('只改 1 个字节 @27', diff.length === 1 && diff[0] === 27, 'offsets ' + diff.join(','));
  chk('往返校验 major=23', C.scanBytes(out, 'x.ffx').srcMajor === 23);
} else {
  console.log('  (跳过：未找到 ffx)');
}

console.log('\n== 6. 版本边界的处理 ==');
// 0x61 = 26.x 现已按线性规律（fmt = major + 71）收录，但必须与版本字交叉校验
chk('majorOfFormatByte(0x61) = 26', C.majorOfFormatByte(0x61) === 26);
chk('majorOfFormatByte(0x60) = 25', C.majorOfFormatByte(0x60) === 25);
chk('0x62 未被收录（无实测）', C.majorOfFormatByte(0x62) === undefined
  || C.majorOfFormatByte(0x62) === null);
chk('0x62 与版本字一致 → inferred', C.classifyFormatByte(0x62, 27)[0] === 'inferred');
chk('0x62 与版本字不符 → 拒绝', C.classifyFormatByte(0x62, 25)[0] === 'newer');
if (fs.existsSync(aepPath)) {
  // 只把格式字节改成 0x61，但版本字仍是 25.x —— 两边对不上，必须拒绝而不是瞎改
  const u8b = new Uint8Array(fs.readFileSync(aepPath));
  u8b[33] = 0x61;
  const ru = C.scanAep(u8b, 'mismatch.aep');
  chk('格式字节与版本字对不上 → 拒绝', ru.ok === false, ru.error);
  chk('拒绝时 hits 为空（不会误改）', ru.hits.length === 0);

  // 两者一致（都改成 26）→ 采信并标注推断
  const u8c = new Uint8Array(fs.readFileSync(aepPath));
  const r0 = C.scanAep(u8c, 'ok.aep');
  const w26 = C.replaceMajorInWord(r0.srcWord, 26);
  u8c[33] = 0x61;
  r0.hits.filter((x) => x.kind === 'word').forEach((x) => {
    u8c[x.offset] = (w26 >>> 24) & 0xFF; u8c[x.offset + 1] = (w26 >>> 16) & 0xFF;
    u8c[x.offset + 2] = (w26 >>> 8) & 0xFF; u8c[x.offset + 3] = w26 & 0xFF;
  });
  const r26 = C.scanAep(u8c, 'ok26.aep');
  chk('两者一致 → 识别为 26.x', r26.ok && r26.srcMajor === 26.0, r26.error || String(r26.srcMajor));
  chk('标注 inferred', r26.extra.inferred === true);
}

console.log('\n== 7. ZIP（store 模式）与 CRC32 ==');
const crc = C.crc32(new Uint8Array([1,2,3,4,5]));
chk('crc32 有输出', typeof crc === 'number' && crc > 0, '0x' + crc.toString(16));
const blob = C.zipStore([{name:'a.txt', data:new Uint8Array([65,66,67])}]);
chk('zip 生成成功', blob && blob.size > 0, blob ? blob.size + ' bytes' : 'null');

console.log('\n' + '='.repeat(52));
console.log('PASS ' + pass + '   FAIL ' + fail);
console.log('='.repeat(52));
process.exit(fail ? 1 : 0);
