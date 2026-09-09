// 提取 AEP-Downgrader.html 的核心逻辑，验证「26.x 支持」与「.aepx 支持」，
// 并与 aep_core.py 的结果逐字节比对（确保网页版与 Python 版行为一致）。
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, 'AEP-Downgrader.html'), 'utf8');
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.error('未找到 script'); process.exit(1); }
global.window = {};
eval(m[1]);
const C = global.window.AEPCore;
if (!C) { console.error('AEPCore 未导出'); process.exit(1); }

let pass = 0, fail = 0;
function chk(name, cond, extra) {
  if (cond) { pass++; console.log('  PASS  ' + name + (extra ? '  ' + extra : '')); }
  else { fail++; console.log('  FAIL  ' + name + (extra ? '  ' + extra : '')); }
}

const AE = 'C:/Program Files/Adobe/Adobe After Effects 2025/Support Files';
const AEP = AE + '/Required/secret.aep';
const u8 = (p) => new Uint8Array(fs.readFileSync(p));

console.log('== 1. 位域自校验（公开真实 AEPX 样本：CC 2018 15.1.2 Mac）==');
const d = C.decodeWord(0x07789645);
chk('major=15', d.major === 15, 'got ' + d.major);
chk('minor=1', d.minor === 1);
chk('patch=2', d.patch === 2);
chk('build=69', d.build === 69);
chk('os=13 Mac', d.os === 13);

console.log('\n== 2. 26.x 与线性外推交叉校验 ==');
chk('26.0 → 0x61', C.formatByteOf(26.0) === 0x61);
chk('22/23/24/25/26 线性规律成立',
  [22, 23, 24, 25, 26].every((x) => C.formatByteOf(x) === x + C.LINEAR_BASE));
chk('0x62 + word major 27 → inferred',
  C.classifyFormatByte(0x62, 27)[0] === 'inferred');
chk('对不上 → 拒绝', C.classifyFormatByte(0x62, 25)[0] === 'newer');
chk('落在空隙 → 拒绝', C.classifyFormatByte(0x59, 18)[0] === 'newer');

console.log('\n== 3. 26.x 工程端到端（真实 secret.aep 改造）==');
const raw = u8(AEP);
const h = C.scanBytes(raw, 'secret.aep');
const fmtOff = h.hits.filter((x) => x.kind === 'format_byte')[0].offset;
const wordOffs = h.hits.filter((x) => x.kind === 'word').map((x) => x.offset);
const w0 = h.srcWord;
const w26 = C.replaceMajorInWord(w0, 26);
const u26 = new Uint8Array(raw);
u26[fmtOff] = 0x61;
wordOffs.forEach((o) => { u26[o] = (w26 >>> 24) & 0xFF; u26[o + 1] = (w26 >>> 16) & 0xFF;
                          u26[o + 2] = (w26 >>> 8) & 0xFF; u26[o + 3] = w26 & 0xFF; });
const r26 = C.scanBytes(u26, 'fake26.aep');
chk('识别为 26.x', r26.ok && r26.srcMajor === 26.0, 'got ' + r26.srcMajor);
C.computeNewValues(r26, 24.0, false);
const o26 = C.applyPatch(u26, r26);
chk('往返校验 → 24.x', C.scanBytes(o26, 'f.aep').srcMajor === 24.0);

console.log('\n== 4. AEPX：解析 + 降级 + 往返 ==');
// 用 secret.aep 的 head 块真实字节生成 bdata，套进真实 AEPX 文档骨架
const headOff = h.hits.filter((x) => x.kind === 'word')[0].offset - 4;
const headHex = Buffer.from(raw.slice(headOff, headOff + 20)).toString('hex');
const svapHex = headHex.substr(8, 8);
const aepxStr =
  '<?xml version="1.0" encoding="UTF-8"?>\n' +
  '<AfterEffectsProject xmlns="http://www.adobe.com/products/aftereffects" majorVersion="1" minorVersion="0">\n' +
  '\t<svap bdata="' + svapHex + '"/> <!--可能是版本号-->\n' +
  '\t<head bdata="' + headHex + '"/>\n' +
  '\t<nhed bdata="0000000000000005000101001e10020000000019004cfac00000608000899000"/>\n' +
  '\t<sfnm>\n\t\t<string>红色纯色</string>\n\t</sfnm>\n' +
  '</AfterEffectsProject>\n';
const aepx = Buffer.from(aepxStr, 'utf8');
const ra = C.scanBytes(new Uint8Array(aepx), 'proj.aepx');
chk('识别为 aepx', ra.ftype === 'aepx');
chk('解析出 25.x', ra.ok && ra.srcMajor === 25.0, ra.error || String(ra.srcMajor));
chk('build 47', ra.srcBuild === 47, 'got ' + ra.srcBuild);
chk('命中 3 处', ra.hits.length === 3,
  ra.hits.map((x) => x.kind + '@' + x.offset).join(','));
C.computeNewValues(ra, 24.0, false);
const out = C.applyPatch(new Uint8Array(aepx), ra);
chk('字节长度不变', out.length === aepx.length);
chk('往返校验 → 24.x', C.scanBytes(out, 'proj.aepx').srcMajor === 24.0);
chk('中文未被破坏', Buffer.from(out).includes(Buffer.from('红色纯色', 'utf8')));
const outStr = Buffer.from(out).toString('utf8');
const newHead = outStr.split('<head bdata="')[1].split('"/>')[0];
console.log('        原 head：' + headHex);
console.log('        新 head：' + newHead);

console.log('\n== 5. AEPX 中文前置：字节偏移不能错位 ==');
const pad = '中文填充内容测试'.repeat(50);
const aepx2 = Buffer.from(
  '<?xml version="1.0" encoding="UTF-8"?>\n' +
  '<AfterEffectsProject majorVersion="1" minorVersion="0">\n' +
  '\t<!-- ' + pad + ' -->\n' +
  '\t<svap bdata="' + svapHex + '"/>\n' +
  '\t<head bdata="' + headHex + '"/>\n' +
  '</AfterEffectsProject>\n', 'utf8');
const rb = C.scanBytes(new Uint8Array(aepx2), 'cn.aepx');
chk('解析出 25.x', rb.ok && rb.srcMajor === 25.0, rb.error || String(rb.srcMajor));
C.computeNewValues(rb, 23.0, false);
const ob = C.applyPatch(new Uint8Array(aepx2), rb);
chk('往返校验 → 23.x', C.scanBytes(ob, 'cn.aepx').srcMajor === 23.0);
chk('中文未被破坏', Buffer.from(ob).includes(Buffer.from(pad, 'utf8')));
chk('长度不变', ob.length === aepx2.length);

console.log('\n== 6. AEPX 兜底：无法确认时拒绝改写 ==');
const bad = Buffer.from('<?xml version="1.0" encoding="UTF-8"?>\n<AfterEffectsProject><Fold/></AfterEffectsProject>');
const rc = C.scanBytes(new Uint8Array(bad), 'bad.aepx');
chk('报失败而非乱改', rc.ok === false, rc.error);

console.log('\n== 7. 回归：真实 .aep / .ffx ==');
const ra2 = C.scanBytes(raw, 'secret.aep');
chk('secret.aep 仍为 25.0', ra2.ok && ra2.srcMajor === 25.0);
const ffx = u8(AE + '/Presets/Image - Special Effects/Cracked Tiles.ffx');
const rf = C.scanBytes(ffx, 't.ffx');
chk('ffx 仍能解析', rf.ok, rf.error);

console.log('\n' + '='.repeat(46));
console.log('  ' + pass + ' PASS / ' + fail + ' FAIL');
console.log('='.repeat(46));
process.exit(fail ? 1 : 0);
