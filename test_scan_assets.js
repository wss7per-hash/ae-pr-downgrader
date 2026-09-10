// 抽取 AEP-Downgrader.html 的脚本，验证网页版「工程体检」逻辑 scanAssetsJS，
// 与 Python scan_assets.py 同套思路、结果一致（用合成样本，无真实工程依赖）。
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, 'AEP-Downgrader.html'), 'utf8');
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.error('未找到 script'); process.exit(1); }

// 网页脚本在 <script> 顶部有：if(typeof document==='undefined') return;
// 这会跳过 UI 逻辑（含工程体检 AssetScan）。在浏览器里 document 存在、正常执行；
// 但 node 里没有 document，故此处补一个最小 DOM 桩，确保脚本能跑到 AssetScan 定义。
// 注意：脚本只“定义”函数，document 真正被“调用”发生在用户交互时，桩只需不报错即可。
function fakeEl() {
  const el = {
    style: {}, dataset: {}, classList: { add() {}, remove() {} },
    setAttribute() {}, appendChild() {}, removeChild() {}, remove() {},
    addEventListener() {}, querySelector() { return null; },
    querySelectorAll() { return []; }, hidden: false, value: '', innerHTML: '', textContent: '',
  };
  return el;
}
global.document = {
  getElementById() { return fakeEl(); },
  createElement() { return fakeEl(); },
  querySelector() { return null; },
  querySelectorAll() { return []; },
};
global.window = {};
eval(m[1]);
const scanAssetsJS = global.window.AssetScan && global.window.AssetScan.scan;
if (typeof scanAssetsJS !== 'function') { console.error('AssetScan.scan 未导出'); process.exit(1); }
const assetManifest = global.window.AssetManifest;
const deliveryZip = global.window.DeliveryZip;
if (typeof assetManifest !== 'object' || typeof deliveryZip !== 'object') { console.error('AssetManifest/DeliveryZip 未导出'); process.exit(1); }

let pass = 0, fail = 0;
function chk(name, cond, extra) {
  if (cond) { pass++; console.log('  PASS  ' + name); }
  else { fail++; console.log('  FAIL  ' + name + (extra ? '  ' + extra : '')); }
}

function wideStr(s) {
  // 模拟 AE 二进制里的 UTF-16LE 文本
  const out = [];
  for (let i = 0; i < s.length; i++) { out.push(s.charCodeAt(i) & 0xFF, 0x00); }
  return Buffer.from(out);
}
function asciiStr(s) { return Buffer.from(s, 'utf8'); }

console.log('== 1. AE 二进制体检 ==');
// 构造含第三方插件 / 表达式 / 素材路径 / ADBE 效果的字节
const aeBuf = Buffer.concat([
  asciiStr('RIFX'), Buffer.alloc(8), asciiStr('Egg!'), Buffer.alloc(16),
  wideStr('Trapcode Particular v6.0'),
  asciiStr('thisLayer.position.wiggle(2, 30); loopOut()'),
  wideStr('C:\\proj\\footage\\clip.mov'),
  wideStr('D:\\assets\\bg.png'),
  asciiStr('ADBE Gaussian Blur 2.0'),
  asciiStr('ADBE Transform Group'),
  asciiStr('Adobe Heiti Std'),
  asciiStr('Microsoft YaHei'),
]);
const aeRes = { ftype: 'aep', ok: true, srcMajor: 25, srcVersion: '25.0', srcLabel: '2025' };
const aeRep = scanAssetsJS(new Uint8Array(aeBuf), aeRes, 'demo.aep');
chk('kind 为 aep', aeRep.kind === 'aep', aeRep.kind);
chk('第三方插件 Trapcode 命中', aeRep.thirdParty.indexOf('Trapcode') >= 0, JSON.stringify(aeRep.thirdParty));
chk('素材路径 clip.mov 识别', aeRep.mediaPaths.some((p) => p.indexOf('clip.mov') >= 0), JSON.stringify(aeRep.mediaPaths));
chk('素材路径 bg.png 识别', aeRep.mediaPaths.some((p) => p.indexOf('bg.png') >= 0));
chk('AE 内置效果命中', aeRep.adobeEffects.some((e) => e.indexOf('ADBE Gaussian Blur') >= 0), JSON.stringify(aeRep.adobeEffects));
chk('表达式被标记', aeRep.hasExpression && aeRep.expressionCount >= 1);
chk('字体识别 Adobe', aeRep.fonts.indexOf('Adobe') >= 0, JSON.stringify(aeRep.fonts));
chk('第三方插件风险提示生成', aeRep.risks.some((r) => r.indexOf('第三方插件') >= 0));

console.log('\n== 2. PR XML 体检 ==');
const prXml =
  '<?xml version="1.0" encoding="UTF-8"?>\n' +
  '<PremiereData Version="3">\n' +
  '<Project ObjectRef="1"/>\n' +
  '<Project ObjectID="1" ClassID="62ad66dd-0dcd-42da-a660-6d8fbde94876" Version="43">\n' +
  '  <Name>婚礼片头</Name>\n' +
  '  <Sequence ObjectRef="3"><VideoFilter ObjectRef="10"><Filter>Beauty Box</Filter></VideoFilter></Sequence>\n' +
  '  <Clip ObjectRef="20"><MediaPath>C:\\footage\\wedding.mov</MediaPath><FilePath>D:\\music\\theme.wav</FilePath></Clip>\n' +
  '  <Font ObjectRef="30">Adobe Heiti Std</Font>\n' +
  '  <Font ObjectRef="31">Microsoft YaHei</Font>\n' +
  '  <Effect Expression="thisComp.layer(1).opacity">Opacity</Effect>\n' +
  '</Project>\n</PremiereData>\n';
const prRes = { ftype: 'prproj', ok: true, srcNumber: 43, srcLabel: '2025', xml: prXml };
const prRep = scanAssetsJS(new Uint8Array(Buffer.from(prXml, 'utf8')), prRes, 'demo.prproj');
chk('kind 为 prproj', prRep.kind === 'prproj', prRep.kind);
chk('PR 版本标签含 2025', prRep.versionLabel.indexOf('2025') >= 0, prRep.versionLabel);
chk('PR 字体标签提取 Adobe Heiti', prRep.fonts.some((f) => f.indexOf('Adobe Heiti') >= 0), JSON.stringify(prRep.fonts));
chk('PR 媒体路径 wedding.mov', prRep.mediaPaths.some((p) => p.indexOf('wedding.mov') >= 0), JSON.stringify(prRep.mediaPaths));
chk('PR 媒体路径 theme.wav', prRep.mediaPaths.some((p) => p.indexOf('theme.wav') >= 0));
chk('PR 表达式标记', prRep.hasExpression);
chk('PR 无第三方插件（Beauty Box 不在清单）', prRep.thirdParty.length === 0, JSON.stringify(prRep.thirdParty));

console.log('\n== 3. AEPX 文本体检 ==');
const aepxXml =
  '<?xml version="1.0" encoding="UTF-8"?>\n' +
  '<AEPX><Project ObjectRef="1"/>\n' +
  '<Project ObjectID="1" Version="24"><Effect matchName="ADBE Color Balance (HLS)">CB</Effect>\n' +
  '<Expression>wiggle(3, 20)</Expression>\n' +
  '<Footage Source="C:\\ae\\shot.jpg"/><Font>SimSun</Font><Plugin>Trapcode Form</Plugin>\n' +
  '</Project></AEPX>\n';
const axRes = { ftype: 'aepx', ok: true, srcMajor: 24, srcVersion: '24.0', srcLabel: '2024' };
const axRep = scanAssetsJS(new Uint8Array(Buffer.from(aepxXml, 'utf8')), axRes, 'demo.aepx');
chk('AEPX 第三方插件 Trapcode 命中', axRep.thirdParty.indexOf('Trapcode') >= 0, JSON.stringify(axRep.thirdParty));
chk('AEPX 字体 SimSun 命中', axRep.fonts.indexOf('SimSun') >= 0, JSON.stringify(axRep.fonts));
chk('AEPX ADBE 效果命中', axRep.adobeEffects.some((e) => e.indexOf('ADBE Color Balance') >= 0));
chk('AEPX 表达式标记', axRep.hasExpression);

console.log('\n== 4. 错误分支 ==');
const bad = scanAssetsJS(new Uint8Array(Buffer.from('not a project')), { ftype: 'unknown', ok: false }, 'junk.bin');
chk('未知类型不崩溃', bad.kind === 'unknown' && Array.isArray(bad.risks), JSON.stringify(bad));

console.log('\n== 5. 发包清单 AssetManifest ==');
const mi = {
  name: 'demo.aep',
  rep: {
    kind: 'aep', versionLabel: '2025 (25.0)',
    fonts: ['Microsoft YaHei'], thirdParty: ['Trapcode'], adobeEffects: ['ADBE Gaussian Blur'],
    hasExpression: true, expressionCount: 2,
    mediaPaths: ['C:\\proj\\a.png', 'D:\\miss\\b.mov'],
  },
};
const nameMap = { 'a.png': { name: 'a.png' } };
const man = assetManifest.build([mi], nameMap);
chk('清单含 1 个工程', man.projects.length === 1);
chk('清单素材数=2', man.summary.media === 2, JSON.stringify(man.summary));
chk('清单缺失数=1', man.summary.mediaMissing === 1, JSON.stringify(man.summary));
chk('清单标记 a.png 存在', man.projects[0].media[0].status === 'yes', man.projects[0].media[0].status);
chk('清单标记 b.mov 缺失', man.projects[0].media[1].status === 'no');
const manMd = assetManifest.toMarkdown(man);
chk('MD 含标题', manMd.indexOf('# 发包清单') >= 0);
chk('MD 含缺失标记 ❌', manMd.indexOf('❌') >= 0);
const manJs = JSON.parse(assetManifest.toJson(man));
chk('JSON 可解析含 summary.mediaMissing=1', manJs.summary && manJs.summary.mediaMissing === 1, assetManifest.toJson(man).slice(0, 80));
const manCsv = assetManifest.toCsv(man);
chk('CSV 含表头 工程,类别', manCsv.split('\n')[0].indexOf('工程,类别') >= 0, manCsv.split('\n')[0]);

console.log('\n== 6. 纯 JS ZIP（store）==');
function parseZip(buf) {
  const out = [];
  const dv = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
  let off = 0;
  while (off + 30 <= dv.byteLength) {
    const sig = dv.getUint32(off, true);
    if (sig !== 0x04034b50) break;
    const nameLen = dv.getUint16(off + 26, true);
    const extraLen = dv.getUint16(off + 28, true);
    const compSize = dv.getUint32(off + 18, true);
    let name = '';
    for (let i = 0; i < nameLen; i++) name += String.fromCharCode(dv.getUint8(off + 30 + i));
    const dataOff = off + 30 + nameLen + extraLen;
    const data = Uint8Array.from(buf.subarray(dataOff, dataOff + compSize));
    out.push({ name: name, data: data, crcOff: off + 14 });
    off = dataOff + compSize;
  }
  return out;
}
const zip = deliveryZip.build([
  { name: 'a.txt', data: new Uint8Array([104, 105]) },
  { name: 'media/b.png', data: new Uint8Array([1, 2, 3, 4]) },
]);
chk('ZIP 以 PK\\x03\\x04 开头', zip[0] === 0x50 && zip[1] === 0x4B && zip[2] === 0x03 && zip[3] === 0x04, zip.slice(0, 4).toString());
const parsed = parseZip(zip);
chk('ZIP 含 2 个条目', parsed.length === 2, String(parsed.length));
chk('ZIP 条目名正确', parsed[0].name === 'a.txt' && parsed[1].name === 'media/b.png', JSON.stringify(parsed.map(p => p.name)));
chk('ZIP 数据无损 (hi)', parsed[0].data[0] === 104 && parsed[0].data[1] === 105);
chk('ZIP 数据无损 (1234)', parsed[1].data[0] === 1 && parsed[1].data[3] === 4);
const dvZip = new DataView(zip.buffer, zip.byteOffset, zip.byteLength);
const storedCrc = dvZip.getUint32(parsed[0].crcOff, true);
chk('ZIP crc32 匹配', storedCrc === deliveryZip.crc32(parsed[0].data), storedCrc + ' vs ' + deliveryZip.crc32(parsed[0].data));
let hasEOCD = false;
for (let i = 0; i + 4 <= zip.length; i++) {
  if (zip[i] === 0x50 && zip[i + 1] === 0x4B && zip[i + 2] === 0x05 && zip[i + 3] === 0x06) { hasEOCD = true; break; }
}
chk('ZIP 含 EOCD 标记 (PK\\x05\\x06)', hasEOCD);

console.log('\n' + '='.repeat(46));
console.log('  ' + pass + ' PASS / ' + fail + ' FAIL');
console.log('='.repeat(46));
process.exit(fail ? 1 : 0);
