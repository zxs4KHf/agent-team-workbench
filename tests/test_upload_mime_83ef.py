# 任务 20260923-221452-83ef 自测：/api/upload 的 MIME 别名归一化与错误回显。
# 跑法：py -3 tests/test_upload_mime_83ef.py（仓库根目录）。附件根与工作区都指向临时目录，不碰生产 attachments/、console/。
import base64
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import bus_core as B  # noqa: E402
import bus_web as W  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from frontsrc import front_src  # noqa: E402  ADR 0015：按页面拆模块后，源码断言读「模块 + 拆出去的文件」

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64  # 最小 JPEG 文件头
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
HEIC = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64


class TestUploadMime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        cls.tmp = tempfile.mkdtemp(prefix="bus83ef-")
        cls._attach, cls._ws = W.ATTACH_ROOT, B.WORKSPACE
        W.ATTACH_ROOT = os.path.join(cls.tmp, "attachments")
        B.WORKSPACE = cls.tmp
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), W.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        W.ATTACH_ROOT, B.WORKSPACE = cls._attach, cls._ws
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def upload(self, name, mime, raw, field="type"):
        body = {"name": name, field: mime, "data": base64.b64encode(raw).decode()}
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/upload", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def test_alias_table_is_explicit(self):
        self.assertEqual(W.UPLOAD_MIME_ALIAS, {"image/jpg": "image/jpeg", "image/pjpeg": "image/jpeg", "image/jfif": "image/jpeg"})
        for bad in ("image/svg+xml", "image/heic", "image/avif", "image/tiff"):
            self.assertNotIn(bad, W.UPLOAD_TYPES)
            self.assertNotIn(bad, W.UPLOAD_MIME_ALIAS)

    def test_image_jpg_declared_jpg_succeeds(self):
        code, r = self.upload("photo.jpg", "image/jpg", JPEG)
        self.assertEqual(code, 200, r)
        self.assertEqual((r["mime"], r["received"]), ("image/jpeg", "image/jpg"), "成功体 mime 为归一化后类型，received 为客户端原话")
        self.assertTrue(r["path"].endswith(".jpg"))
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, r["path"])))

    def test_pjpeg_and_jfif_succeed(self):
        code, r = self.upload("photo.jpg", "image/pjpeg", JPEG, field="mime")  # 旧客户端字段名
        self.assertEqual((code, r["mime"]), (200, "image/jpeg"), r)
        code, r = self.upload("scan.jfif", "image/jfif; charset=binary", JPEG)
        self.assertEqual((code, r["mime"]), (200, "image/jpeg"), r)

    def test_heic_rejected_with_actual_mime(self):
        code, r = self.upload("IMG_0001.HEIC", "image/heic", HEIC)
        self.assertEqual(code, 400, r)
        self.assertIn("image/heic", r["error"])
        self.assertIn("仅 PNG/JPEG/GIF/WebP", r["error"])
        self.assertIn("HEIC 请先转 JPG", r["error"])
        self.assertNotIn("图片数据无效", r["error"])
        self.assertEqual(r["mime"], "image/heic")

    def test_empty_mime_with_jpg_extension(self):
        # 空 MIME + .jpg：按扩展名推定 image/jpeg，再由文件头校验 → 成功
        code, r = self.upload("local.jpg", "", JPEG)
        self.assertEqual((code, r["mime"]), (200, "image/jpeg"), r)
        # 空 MIME + .jpg 但内容是 PNG：现行 _sniff 以文件头定落盘扩展名（存成 .png），不比对声明；「声明与内容不符返回 400」属第二步，本轮不做
        code, r = self.upload("fake.jpg", "", PNG)
        self.assertEqual(code, 200, r)
        self.assertTrue(r["path"].endswith(".png"), r)
        # 空 MIME + .jpg 但内容不是任何支持的图片：400，回显「空」与提示
        code, r = self.upload("junk.jpg", "", b"not an image at all" * 4)
        self.assertEqual(code, 400, r)
        self.assertIn("声明的 空 不符", r["error"])
        self.assertIn("HEIC 请先转 JPG", r["error"])
        self.assertEqual(r["mime"], "")
        # 空 MIME + 未知扩展名：不支持的图片类型 空
        code, r = self.upload("blob.bin", "", JPEG)
        self.assertEqual(code, 400, r)
        self.assertIn("不支持的图片类型 空", r["error"])
        self.assertIn("HEIC 请先转 JPG", r["error"])

    def test_octet_stream_falls_back_to_extension(self):
        # application/octet-stream 与空 MIME 同样按扩展名推定，再由文件头校验
        code, r = self.upload("scan.png", "application/octet-stream", PNG)
        self.assertEqual((code, r["mime"], r["received"]), (200, "image/png", "application/octet-stream"), r)
        code, r = self.upload("scan.jpeg", "application/octet-stream", JPEG)
        self.assertEqual((code, r["mime"]), (200, "image/jpeg"), r)
        code, r = self.upload("blob.dat", "application/octet-stream", JPEG)
        self.assertEqual(code, 400, r)
        self.assertIn("不支持的图片类型 application/octet-stream", r["error"])
        self.assertEqual(r["mime"], "application/octet-stream")

    def test_413_bodies_carry_mime(self):
        big = b"\xff\xd8\xff" + b"\x00" * (W.MAX_UPLOAD_BYTES + 1)
        code, r = self.upload("big.jpg", "image/jpeg", big)
        self.assertEqual((code, r["mime"]), (413, "image/jpeg"), r)
        self.assertIn("8 MB", r["error"])
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/upload", data=b"", headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(req, timeout=10)
            self.fail("空请求应 413")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 413)
            self.assertEqual(json.loads(e.read().decode("utf-8")), {"error": "上传请求超过限制", "mime": ""})

    def test_svg_rejected(self):
        code, r = self.upload("icon.svg", "image/svg+xml", b"<svg xmlns='http://www.w3.org/2000/svg'/>")
        self.assertEqual(code, 400, r)
        self.assertIn("image/svg+xml", r["error"])

    def test_frontend_single_implementation(self):
        # 前端白名单只有一份实现：web/v2/js/image_types.js；model.js 只转发 globalThis.WB_IMAGE。v1 页面已于 2026-09-28 移除
        def read(*parts):
            with open(os.path.join(REPO, *parts), encoding="utf-8") as f:
                return f.read()
        impl = read("web", "v2", "js", "image_types.js")
        model, views2, app = read("web", "v2", "js", "model.js"), front_src("views2.js"), front_src("app.js")
        v2html = read("web", "v2", "index.html")
        rule = r"/\.(png|jpe?g|jfif|gif|webp)$/i"
        self.assertEqual(impl.count(rule), 1)
        self.assertIn("ACCEPT = 'image/png,image/jpeg,image/gif,image/webp'", impl)
        self.assertIn("!!file && typeof file === 'object'", impl, "null 守卫")
        hits = []
        for root, dirs, files in os.walk(os.path.join(REPO, "web")):
            dirs[:] = [d for d in dirs if d not in ("archive", "vendor")]
            for f in files:
                if f.endswith((".js", ".mjs", ".html")):
                    with open(os.path.join(root, f), encoding="utf-8", errors="ignore") as fh:
                        if rule in fh.read():
                            hits.append(os.path.relpath(os.path.join(root, f), REPO).replace(os.sep, "/"))
        self.assertEqual(hits, ["web/v2/js/image_types.js"], hits)
        self.assertIn("export const looksLikeImage = WB_IMAGE.looksLikeImage", model)
        self.assertIn("export const IMAGE_ACCEPT = WB_IMAGE.ACCEPT", model)
        self.assertIn('accept="${IMAGE_ACCEPT}"', views2)
        self.assertNotIn('accept="image/*"', views2)
        self.assertIn("if (!looksLikeImage(file))", app)
        self.assertIn("#qdTray", app, "保留 Leader 热修的抽屉图片逻辑")
        self.assertFalse(os.path.exists(os.path.join(REPO, "web", "index.html")), "v1 页面已移除，不要复活第二份实现")
        self.assertIn('<script src="/v2/js/image_types.js"></script>', v2html)
        self.assertIn('<script src="/v2/js/image_types.js"></script>\n<script type="module" src="/v2/js/app.js"></script>', v2html)

    def test_old_wordings_gone(self):
        # 两句旧文案（外层包装「图片数据无效：」与「MIME 类型 … 不在白名单内」）全仓（.py/.js/.html，排除 archive 与 log）为 0 命中
        hits = []
        for root, dirs, files in os.walk(REPO):
            dirs[:] = [d for d in dirs if d not in (".git", ".wt", "node_modules", "archive", "log", "outbox", "inbox", "tmp", "shots", "docs")]
            for f in files:
                if f.endswith((".py", ".js", ".mjs", ".html")) and f != os.path.basename(__file__):
                    with open(os.path.join(root, f), encoding="utf-8", errors="ignore") as fh:
                        txt = fh.read()
                    for w in ("图片数据无效：", "不在白名单内，只允许 PNG/JPEG/GIF/WebP"):
                        if w in txt:
                            hits.append((os.path.relpath(os.path.join(root, f), REPO), w))
        self.assertEqual(hits, [], hits)

if __name__ == "__main__":
    unittest.main(verbosity=2)
