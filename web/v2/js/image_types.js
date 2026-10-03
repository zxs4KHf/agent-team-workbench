// 图片附件白名单 —— 全仓唯一实现（v2 model.js 从 globalThis.WB_IMAGE 取用，不各存一份；v1 页面已于 2026-09-28 删除）。
// 经典脚本而非 ES module（历史原因：当年要给 v1 内联脚本用）。规则：accept 显式四类；托盘过滤 = type 以 image/ 开头 或 文件名匹配 png/jpg/jpeg/jfif/gif/webp；
// 空 type 的本地文件按扩展名放行，最终由服务端（bus_web.py handle_upload）裁决。带 null 守卫。
(function (root) {
  const ACCEPT = 'image/png,image/jpeg,image/gif,image/webp';
  const NAME_RE = /\.(png|jpe?g|jfif|gif|webp)$/i;
  const looksLikeImage = file => !!file && typeof file === 'object' && (String(file.type || '').startsWith('image/') || NAME_RE.test(String(file.name || '')));
  root.WB_IMAGE = Object.freeze({ ACCEPT, NAME_RE, looksLikeImage });
})(typeof globalThis !== 'undefined' ? globalThis : window);
