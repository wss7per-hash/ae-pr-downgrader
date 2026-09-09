// 提取 AEP-Downgrader.html 内联脚本，验证 Premiere Pro（.prproj）降级逻辑。
// 与 test_prproj.py 覆盖同一组用例，确保网页版与 Python CLI 行为一致。
const fs = require('fs');
const path = require('path');
const zlib = require('zlib');

const html = fs.readFileSync(path.join(__dirname, 'AEP-Downgrader.html'), 'utf8');
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.error('未找到 script'); process.exit(1); }
global.window = {};
eval(m[1]);
const C = global.window.AEPCore;
if (!C) { console.error('AEPCore 未导出'); process.exit(1); }

let pass = 0, fail = 0;
const failed = [];
function chk(name, cond, extra) {
  if (cond) { pass++; console.log('  PASS  ' + name + (extra ? '  ' + extra : '')); }
  else { fail++; failed.push(name); console.log('  FAIL  ' + name + (extra ? '  ' + extra : '')); }
}
function eq(name, got, want) {
  chk(name, got === want, 'got=' + JSON.stringify(got) + ' want=' + JSON.stringify(want));
}

function makeXml(ver, cn) {
  cn = cn || '测试片段 · 中文素材';
  return '<?xml version="1.0" encoding="UTF-8"?>\n' +
    '<PremiereData Version="3">\n' +
    '<Project ObjectRef="1"/>\n' +
    '<Project ObjectID="1" ClassID="62ad66dd-0dcd-42da-a660-6d8fbde94876" Version="' + ver + '">\n' +
    '  <Name>' + cn + '</Name>\n' +
    '  <ProjectViewState Version="7" ObjectRef="2"/>\n' +
    '</Project>\n</PremiereData>\n';
}
const makeGzip = (ver, cn) => new Uint8Array(zlib.gzipSync(Buffer.from(makeXml(ver, cn), 'utf8'), { level: 9 }));

(async function main() {
  console.log('== 1. 版本对照表 ==');
  eq('2025 → 43', C.PR_BY_NUM[43], '2025');
  eq('2024 → 42', C.PR_BY_NUM[42], '2024');
  eq('2023 → 41', C.PR_BY_NUM[41], '2023');
  eq('2022 → 40', C.PR_BY_NUM[40], '2022');
  eq('2021 → 39', C.PR_BY_NUM[39], '2021');
  eq('CC 2020 → 38', C.PR_BY_NUM[38], 'CC 2020');
  eq('2026 → 45', C.PR_BY_NUM[45], '2026');
  eq('通用兜底号', C.PR_SAFE_ANY, 1);
  eq('标签：43', C.prLabelOf(43), '2025');
  eq('标签：1', C.prLabelOf(1), '通用兼容');

  console.log('\n== 2. 扫描：gzip 工程 ==');
  const r = await C.scanPrproj(makeGzip(43), 'demo.prproj');
  chk('扫描成功', r.ok, r.error);
  eq('识别为 gzip', r.wasGzip, true);
  eq('源版本号', r.srcNumber, 43);
  eq('源版本标签', r.srcLabel, '2025');
  eq('命中 1 处', r.hits.length, 1);
  eq('命中偏移处的值', r.xml.substr(r.hits[0].offset, r.hits[0].length), '43');
  chk('没有误命中 ProjectViewState 的 Version=7', r.hits[0].note.indexOf('根 Project') >= 0);

  console.log('\n== 3. 扫描：未压缩的纯 XML ==');
  const plain = new Uint8Array(Buffer.from(makeXml(36), 'utf8'));
  const r2 = await C.scanPrproj(plain, 'old.prproj');
  chk('扫描成功', r2.ok, r2.error);
  eq('识别为非 gzip', r2.wasGzip, false);
  eq('源版本号', r2.srcNumber, 36);

  console.log('\n== 4. 2026 稀疏序列化标记 ==');
  const r26 = await C.scanPrproj(makeGzip(45), 'y2026.prproj');
  eq('源版本号', r26.srcNumber, 45);
  chk('标记 sparse2026', r26.extra.sparse2026 === true);

  console.log('\n== 5. 转换：43 → 42 ==');
  const o = await C.convertPrBytes(makeGzip(43), 42);
  const txt = zlib.gunzipSync(Buffer.from(o.data)).toString('utf8');
  chk('输出仍是 gzip', o.data[0] === 0x1F && o.data[1] === 0x8B);
  chk('XML 里已是 42', txt.indexOf('Version="42"') > 0);
  chk('不再有 43', txt.indexOf('Version="43"') < 0);
  chk('中文保留', txt.indexOf('测试片段 · 中文素材') > 0);
  chk('ProjectViewState 未被误改', txt.indexOf('ProjectViewState Version="7"') > 0);
  chk('除版本号外内容完全一致', txt.replace('Version="42"', 'Version="43"') === makeXml(43));
  const rc = await C.scanPrproj(o.data, 'x.prproj');
  eq('重新扫描读到 42', rc.srcNumber, 42);

  console.log('\n== 6. 转换：43 → 1（通用兜底，位数变化）==');
  const o1 = await C.convertPrBytes(makeGzip(43), C.PR_SAFE_ANY);
  const t1 = zlib.gunzipSync(Buffer.from(o1.data)).toString('utf8');
  chk('XML 里已是 1', t1.indexOf('Version="1"') > 0);
  chk('回改 43 后与原文本一致', t1.replace('Version="1"', 'Version="43"') === makeXml(43));
  eq('重新扫描读到 1', (await C.scanPrproj(o1.data)).srcNumber, 1);

  console.log('\n== 7. 纯 XML 输出保持不压缩 ==');
  const op = await C.convertPrBytes(plain, 34);
  chk('输出不是 gzip', !(op.data[0] === 0x1F && op.data[1] === 0x8B));
  chk('内容是 XML', Buffer.from(op.data).toString('utf8').indexOf('Version="34"') > 0);

  console.log('\n== 8. 错误分支 ==');
  const e1 = await C.scanPrproj(new Uint8Array(Buffer.from('hello, not a project')), 'x.prproj');
  chk('非 gzip 非 XML → 失败', !e1.ok);
  const e2 = await C.scanPrproj(new Uint8Array(zlib.gzipSync(Buffer.from('<html>nope</html>'))), 'x.prproj');
  chk('gzip 但非 PR XML → 失败', !e2.ok, e2.error);
  const e3 = await C.scanPrproj(new Uint8Array(zlib.gzipSync(
    Buffer.from('<?xml version="1.0"?>\n<PremiereData Version="3">\n</PremiereData>'))), 'x.prproj');
  chk('缺 Version 属性 → 失败', !e3.ok);
  const e4 = await C.scanPrproj(new Uint8Array([0x1f, 0x8b, 0x08, 0x00, 1, 2, 3, 4, 5]), 'x.prproj');
  chk('损坏 gzip → 失败且不抛异常', !e4.ok, e4.error);

  console.log('\n== 9. scanAny 自动分派 ==');
  const pa = await C.scanAny(makeGzip(43), 'demo.prproj');
  eq('.prproj → prproj', pa.ftype, 'prproj');
  const pb = await C.scanAny(new Uint8Array([0x52, 0x49, 0x46, 0x58, 0, 0, 0, 8, 0x45, 0x67, 0x67, 0x21]), 'x.aep');
  eq('.aep → 走 AE 分支（不误判为 gzip）', pb.ftype !== 'prproj', true);
  const pc = await C.scanAny(new Uint8Array(Buffer.from(makeXml(40))), 'noext');
  eq('无扩展名的纯 XML PR 工程 → prproj', pc.ftype, 'prproj');

  console.log('\n== 10. 幂等性 ==');
  const a1 = await C.convertPrBytes(makeGzip(43), 42);
  const a2 = await C.convertPrBytes(makeGzip(43), 42);
  chk('两次输出字节完全一致', Buffer.from(a1.data).equals(Buffer.from(a2.data)));

  console.log('\n' + '='.repeat(60));
  console.log('通过 ' + pass + '   失败 ' + fail);
  if (failed.length) { console.log('失败项：'); failed.forEach(n => console.log('   - ' + n)); }
  console.log('='.repeat(60));
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error('异常：', e); process.exit(1); });
