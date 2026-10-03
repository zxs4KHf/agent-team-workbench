#!/usr/bin/env python3
"""代理出口自测：测量出口 IP / ASN / 稳定性与目标站点可达性，输出 markdown 表格。

只用标准库（urllib / ssl / socket）。本工具只报告事实，不评估、不改善任何
服务地区资格、账号信任或风控结果；测量通过不等于资格改变。

代理入参里的 user:password 在表格、备注与异常提示中一律掩码（只留 scheme +
host:port），因此输出可以直接贴进合议稿。

用法：
  py -3 tools/proxy_selftest.py                         # 直连自测（不走任何代理）
  py -3 tools/proxy_selftest.py http://user:pw@1.2.3.4:8080 socks5://5.6.7.8:1080
  py -3 tools/proxy_selftest.py --file tools/proxies.txt --repeat 5 --timeout 8
"""
import argparse
import datetime
import json
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

GEO_URL = "http://ip-api.com/json/?fields=status,country,regionName,city,isp,org,as,proxy,hosting,query"
GEO_FALLBACK = "https://ipinfo.io/json"
TRACE_URL = "https://chatgpt.com/cdn-cgi/trace"
REACH_TARGETS = [("chatgpt.com", "https://chatgpt.com/"), ("auth.openai.com", "https://auth.openai.com/")]
DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
MARK = "proxy-selftest/1"


def mask(proxy: str) -> str:
    """去掉代理串里可能含密码的 userinfo，只保留 scheme + host:port。"""
    if not proxy:
        return "(direct)"
    scheme, sep, rest = proxy.partition("://")
    if not sep:
        scheme, rest = "", proxy
    host = rest.rsplit("@", 1)[-1]
    return scheme + "://" + host if sep else host


def parse_proxy(proxy: str):
    """-> (scheme, host, port, username, password)；port 缺失或非法时为 None。"""
    u = urllib.parse.urlsplit(proxy if "://" in proxy else "http://" + proxy)
    try:
        port = u.port
    except ValueError:
        port = None
    user = urllib.parse.unquote(u.username) if u.username else None
    pw = urllib.parse.unquote(u.password) if u.password else None
    return u.scheme.lower(), u.hostname or "", port, user, pw


def make_opener(proxy, ctx):
    """返回 (opener, note)。socks5 需要 PySocks，缺失或入参不完整时跳过该代理。"""
    if not proxy:
        return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)), "direct"
    scheme, host, port, user, pw = parse_proxy(proxy)
    if scheme.startswith("socks"):
        try:
            import socks
            import sockshandler
        except Exception:
            return None, "跳过：未安装 PySocks（stdlib 无 SOCKS5 支持，pip install pysocks 后重试）"
        if not host or not port:
            return None, "socks5 代理需要 host:port，当前入参解析失败"
        # 位置参数顺序是 (proxy_type, addr, port, rdns, username, password)，账号必须走关键字。
        handler = sockshandler.SocksiPyHandler(socks.SOCKS5, host, int(port), username=user, password=pw)
        return urllib.request.build_opener(handler, urllib.request.HTTPSHandler(context=ctx)), scheme
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy}), urllib.request.HTTPSHandler(context=ctx)), scheme


def fetch(opener, url, ua, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "*/*", "X-Selftest-Marker": MARK})
    started = time.monotonic()
    with opener.open(req, timeout=timeout) as resp:
        body = resp.read(65536)
        status = resp.getcode()
    return status, (time.monotonic() - started) * 1000.0, body.decode("utf-8", "replace")


def parse_trace(text):
    out = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def geo(opener, ua, timeout):
    try:
        status, ms, body = fetch(opener, GEO_URL, ua, timeout)
        data = json.loads(body)
        if data.get("status") == "success":
            return {
                "ip": data.get("query", ""), "asn": data.get("as", ""), "org": data.get("org", ""),
                "geo": " ".join(x for x in [data.get("country", ""), data.get("regionName", ""), data.get("city", "")] if x),
                "flags": " ".join(f for f, on in (("proxy", data.get("proxy")), ("hosting", data.get("hosting"))) if on) or "-",
                "ms": ms,
            }
    except Exception:
        pass
    try:
        status, ms, body = fetch(opener, GEO_FALLBACK, ua, timeout)
        data = json.loads(body)
        org = data.get("org", "")
        return {
            "ip": data.get("ip", ""), "asn": org.split(" ")[0] if org else "", "org": org,
            "geo": ", ".join(x for x in [data.get("country", ""), data.get("region", ""), data.get("city", "")] if x),
            "flags": "-", "ms": ms,
        }
    except Exception as e:
        return {"ip": "", "asn": "", "org": "", "geo": "", "flags": "geo 取不到：%s" % type(e).__name__, "ms": 0.0}


def probe(proxy, args, ctx):
    row = {"proxy": mask(proxy), "note": "", "ip": "", "asn": "", "geo": "", "flags": "",
           "stable": "", "trace": "", "ua": "", "reach": "", "tls": ""}
    opener, note = make_opener(proxy, ctx)
    row["note"] = note
    if opener is None:
        return row
    row.update(geo(opener, args.ua, args.timeout))
    ips = {row["ip"]} if row["ip"] else set()
    for _ in range(max(0, args.repeat - 1)):
        g = geo(opener, args.ua, args.timeout)
        if g["ip"]:
            ips.add(g["ip"])
    if args.repeat < 2:
        row["stable"] = "未采样（--repeat≥2）"
    elif not row["ip"]:
        row["stable"] = "无数据"
    else:
        row["stable"] = ("稳定（%d/%d 同一 IP）" % (len(ips), args.repeat)) if len(ips) == 1 else ("**不稳定（%d 个出口 IP）**" % len(ips))
    try:
        status, ms, body = fetch(opener, TRACE_URL, args.ua, args.timeout)
        t = parse_trace(body)
        row["trace"] = "HTTP %s · %.0fms · loc=%s" % (status, ms, t.get("loc", "?"))
        row["tls"] = t.get("tls", "?")
        seen = t.get("uag", "")
        row["ua"] = "未改写" if seen == args.ua else ("改写为 `%s`" % seen if seen else "未回显")
    except urllib.error.HTTPError as e:
        row["trace"] = "HTTP %s · %.0fms" % (e.code, 0)
    except Exception as e:
        row["trace"] = "失败：%s" % type(e).__name__
    reach = []
    if not args.no_reach:
        for name, url in REACH_TARGETS:
            try:
                status, ms, _ = fetch(opener, url, args.ua, args.timeout)
                reach.append("%s %s (%.0fms)" % (name, status, ms))
            except urllib.error.HTTPError as e:
                reach.append("%s HTTP %s" % (name, e.code))
            except Exception as e:
                reach.append("%s 失败(%s)" % (name, type(e).__name__))
    row["reach"] = "; ".join(reach)
    return row


def render(rows, args):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = ["# 代理出口自测 %s" % now, "",
             "- 参数：repeat=%d timeout=%ss 探测 UA=`%s`" % (args.repeat, args.timeout, args.ua),
             "- 只测事实：出口 IP/ASN/稳定性/可达性/TLS 版本与 UA 是否被改写。",
             "- `代理` 列已掩码，不含 user:password，可直接粘贴转发。",
             "- **任何结果都不改变服务地区资格，也不构成规避风控的手段或承诺。**", "",
             "| 代理 | 出口 IP | ASN | 归属地 | 标记 | 稳定性 | Cloudflare trace | UA | TLS | 可达性 | 备注 |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append("| `{proxy}` | {ip} | {asn} | {geo} | {flags} | {stable} | {trace} | {ua} | {tls} | {reach} | {note} |".format(**r))
    stab = ("- `稳定性` 是连续 %d 次查询的出口 IP 集合大小，中途换段会在这里暴露。" % args.repeat) if args.repeat >= 2 \
        else "- `稳定性` 本轮未采样（--repeat<2），需要 --repeat≥2 才能判断是否换段。"
    lines += ["", "## 读表要点",
              "- `标记` 出现 `hosting` = 机房/云段，`proxy` = 公开代理段；住宅 ISP 一般两者都为空。",
              stab,
              "- `UA` 为「改写」说明链路上有中间件改写了请求头；`trace` 的 `tls` 是 Cloudflare 侧观测到的 TLS 版本。",
              "- 可达性两个目标都失败 ≠ 账号问题，可能只是本机断网或代理不通；先跑一次不带代理的直连自测做对照。",
              "- 取不到 geo 或 trace 只说明探测失败，不代表代理质量结论。"]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="代理出口自测：出口 IP/ASN/稳定性 + 目标站点可达性 + UA 改写观测（仅标准库）")
    ap.add_argument("proxies", nargs="*", help="代理 URL，如 http://host:port 或 socks5://host:port；留空则做直连自测")
    ap.add_argument("--file", help="从文件按行读取代理（# 开头为注释），默认 tools/proxies.txt（存在则用）")
    ap.add_argument("--repeat", type=int, default=5, help="出口 IP 稳定性采样次数，默认 5")
    ap.add_argument("--timeout", type=float, default=8.0, help="单次请求超时秒数，默认 8")
    ap.add_argument("--ua", default=DEFAULT_UA, help="探测用 User-Agent，与 Cloudflare 回显比对以判断是否被改写")
    ap.add_argument("--no-reach", action="store_true", help="跳过 chatgpt.com / auth.openai.com 可达性探测")
    args = ap.parse_args()

    listed = [p for p in args.proxies if p.strip()]
    path = args.file or "tools/proxies.txt"
    try:
        with open(path, encoding="utf-8") as f:
            listed += [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    except OSError:
        pass
    if not listed:
        print("没有代理入参，改为直连自测。", file=sys.stderr)
        listed = [""]

    ctx = ssl.create_default_context()
    rows = []
    for p in listed:
        try:
            rows.append(probe(p, args, ctx))
        except Exception as e:
            rows.append({"proxy": mask(p), "note": "探测异常：%s" % type(e).__name__, "ip": "", "asn": "",
                         "geo": "", "flags": "", "stable": "", "trace": "", "ua": "", "tls": "", "reach": ""})
    print(render(rows, args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
