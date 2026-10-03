// e0de 冒烟：唯一实现 web/v2/js/image_types.js 的行为（null 守卫、空 type / octet-stream 按扩展名放行、非图片拒绝），以及 v2 model.js 转发的是同一个函数对象。
// 跑法：node tests/smoke_image_types_e0de.mjs（仓库根目录）
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
new Function(readFileSync('web/v2/js/image_types.js', 'utf8'))();
const I = globalThis.WB_IMAGE;
let fails = 0; const ok = (c, m) => { console.log((c ? 'PASS  ' : 'FAIL  ') + m); if (!c) fails++; };
ok(I && Object.isFrozen(I), 'WB_IMAGE 已定义且冻结');
ok(I.looksLikeImage(null) === false && I.looksLikeImage(undefined) === false && I.looksLikeImage('x.png') === false, 'null / undefined / 非对象 → false（null 守卫）');
ok(I.looksLikeImage({ type: '', name: 'local.JPG' }) === true, '空 type + .JPG → 放行');
ok(I.looksLikeImage({ type: 'application/octet-stream', name: 'scan.jfif' }) === true, 'octet-stream + .jfif → 放行（服务端再裁决）');
ok(I.looksLikeImage({ type: 'image/heic', name: 'IMG.HEIC' }) === true, 'image/* 一律先入托盘，由服务端回显拒绝');
ok(I.looksLikeImage({ type: 'application/pdf', name: 'a.pdf' }) === false && I.looksLikeImage({ type: '', name: 'photo.heic' }) === false, 'pdf / 空 type + .heic → 拒');
ok(I.ACCEPT === 'image/png,image/jpeg,image/gif,image/webp', 'accept 四类');
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} }; globalThis.location = { search: '', pathname: '/', hash: '' };
const M = await import(pathToFileURL('web/v2/js/model.js').href);
ok(M.looksLikeImage === I.looksLikeImage && M.IMAGE_ACCEPT === I.ACCEPT, 'model.js 转发同一函数对象与 accept，不是第二份实现');
console.log(fails ? `\n${fails} FAILED` : '\nALL PASS'); process.exit(fails ? 1 : 0);
