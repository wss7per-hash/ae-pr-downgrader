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

console.log('\n' + '='.repeat(46));
console.log('  ' + pass + ' PASS / ' + fail + ' FAIL');
console.log('='.repeat(46));
process.exit(fail ? 1 : 0);
