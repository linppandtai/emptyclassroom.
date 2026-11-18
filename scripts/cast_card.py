# -*- coding: utf-8 -*-
"""cast-card · 人物介绍卡渲染器

一份 job.json 驱动：几个角色、一张阵容图、一套配色，输出 PNG / JPG / 预览页。

用法：
    python cast_card.py job.json
    python cast_card.py job.json --out design/cast --no-lineup

设计要点（都是踩出来的，改动前先读 references/layout.md）：
  · 立绘从画布底站上来，脚部「先沉底再渐隐」——只做 alpha 渐变会留一圈灰雾；
  · 文字必须落在立绘左缘以外，深墨字压深色衣服会直接消失；
  · 引文宽度按立绘左缘反推，断点由配置人工给（数据里 quote 是数组）；
  · 立绘高度会被自动收窄，保证不压文字栏、不越右边界。
"""

import argparse
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ── 默认值 ────────────────────────────────────────────────────────────────

DEFAULTS = {
    "title": "",
    "series": "人物",
    "project": "",
    "project_en": "",
    "out": "cast",
    "palette": {"paper": "#F9F4E8", "ink": "#1D2027", "red": "#D62C3E", "dim": "#807666"},
    "card": {"w": 1080, "h": 1440},
    "portrait": {"height": 1100, "cx": 0.7315, "crop": 0.86,
                 "fade_start": 0.90, "fade_rate": 2.0, "fade_len": 0.10},
    "text": {"body_size": 22, "quote_size": 22},
    "lineup": {"enabled": True, "w": 2160, "h": 1440, "height": 1010},
    "members": [],
}

# 基准尺寸：所有排版数字按这个写，实际画布按 w/1080 等比缩放
BASE_W, BASE_H = 1080, 1440

SERIF_CANDIDATES = [
    "C:/Windows/Fonts/NotoSerifSC-VF.ttf",
    "C:/Windows/Fonts/SourceHanSerifSC-Regular.otf",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
]
LATIN_CANDIDATES = [
    "C:/Windows/Fonts/georgia.ttf",
    "C:/Windows/Fonts/constan.ttf",
    "C:/Windows/Fonts/times.ttf",
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
]


# ── 小工具 ────────────────────────────────────────────────────────────────

def hex2rgb(s):
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def pick(paths):
    for p in paths:
        if os.path.exists(p):
            return p
    raise SystemExit("找不到可用字体，请在 SERIF_CANDIDATES / LATIN_CANDIDATES 里补一条：%s" % paths)


class Fonts:
    def __init__(self):
        self.serif_path = pick(SERIF_CANDIDATES)
        self.latin_path = pick(LATIN_CANDIDATES)
        self._cache = {}

    def serif(self, size, weight=600):
        key = ("s", size, weight)
        if key not in self._cache:
            f = ImageFont.truetype(self.serif_path, size)
            try:
                f.set_variation_by_axes([weight])   # 只有可变字体支持，静态字体忽略
            except Exception:
                pass
            self._cache[key] = f
        return self._cache[key]

    def latin(self, size):
        key = ("l", size)
        if key not in self._cache:
            self._cache[key] = ImageFont.truetype(self.latin_path, size)
        return self._cache[key]


def paper(w, h, color, seed=7):
    """纸底加极轻噪点：大面积纯色会出细微色阶，颗粒能压掉。"""
    rng = np.random.default_rng(seed)
    n = rng.normal(0, 1.6, (h, w, 1)).repeat(3, axis=2)
    a = np.clip(np.array(color, np.float32) + n, 0, 255).astype(np.uint8)
    return Image.fromarray(a, "RGB").convert("RGBA")


def vtext(d, text, cx, top, font, fill, step):
    """竖排：每字水平居中于 cx 逐字下落。PIL 没有竖排模式。"""
    y = top
    for c in text:
        d.text((cx - font.getlength(c) / 2, y), c, font=font, fill=fill)
        y += step


def tracked(d, text, xy, font, fill, tracking=0.0):
    x, y = xy
    for c in text:
        d.text((x, y), c, font=font, fill=fill)
        x += font.getlength(c) + tracking
    return x


def twidth(text, font, tracking=0.0):
    return sum(font.getlength(c) for c in text) + tracking * max(0, len(text) - 1)


NO_HEAD = "。，、；：？！）」』】…·"


def wrap(text, font, max_w):
    """按字断行 + 避头点：标点不许落行首，宁可超出一个字的宽度。"""
    lines, cur = [], ""
    for c in text:
        if font.getlength(cur + c) <= max_w or c in NO_HEAD:
            cur += c
        else:
            lines.append(cur)
            cur = c
    if cur:
        lines.append(cur)
    return lines


def corner_ticks(d, w, h, inset, ln, col):
    for x, y, dx, dy in ((inset, inset, 1, 1), (w - inset, inset, -1, 1),
                         (inset, h - inset, 1, -1), (w - inset, h - inset, -1, -1)):
        d.line([(x, y), (x + dx * ln, y)], fill=col, width=1)
        d.line([(x, y), (x, y + dy * ln)], fill=col, width=1)


def seat(px, kind, ink, red, ss=4):
    """单人座位格：实心=在场，空框=缺席（朱红）。空框描边向内收半个线宽。"""
    s = px * ss
    r = max(2, round(s * 0.14))
    lw = max(3, round(s * 0.10))
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if kind == "hollow":
        d.rounded_rectangle([lw // 2, lw // 2, s - 1 - lw // 2, s - 1 - lw // 2],
                            radius=r, outline=red + (255,), width=lw)
    else:
        d.rounded_rectangle([0, 0, s - 1, s - 1], radius=r, fill=ink + (255,))
    return im.resize((px, px), Image.LANCZOS)


def load_figure(path, h, crop, paper_rgb, fade_start, fade_len, fade_rate):
    h = max(2, int(round(h)))
    im = Image.open(path).convert("RGBA")
    bbox = im.split()[3].getbbox() or (0, 0, im.width, im.height)
    im = im.crop((bbox[0], 0, bbox[2], im.height))          # 去左右透明边，便于居中
    im = im.crop((0, 0, im.width, max(2, round(im.height * crop))))
    w = max(1, round(im.width * h / im.height))
    im = im.resize((w, h), Image.LANCZOS)
    return sink_and_fade(im, int(h * (1 - fade_len)), h, paper_rgb, fade_rate, fade_start)


def sink_and_fade(im, y0, y1, paper_rgb, rate=2.0, start=0.9):
    """先沉底再渐隐：颜色先乘向纸色，再与 alpha 一起归零。

    只做 alpha 渐变的话，半透明的人像压在浅色纸上会留一圈灰雾；
    而素材底部常是被硬切的，这圈雾正好把切边暴露成一条直边。
    """
    a = np.asarray(im).astype(np.float32)
    h = a.shape[0]
    y = np.arange(h, dtype=np.float32)[:, None, None]
    t = np.clip((y - y0) / max(1, (y1 - y0)), 0, 1)
    t = t * t * (3 - 2 * t)
    k = np.clip(t * rate, 0, 1)
    rgb = a[..., :3] * (1 - k) + np.array(paper_rgb, np.float32) * k
    out = np.concatenate([rgb, a[..., 3:4] * (1 - t)], axis=2)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGBA")


# ── 渲染器 ────────────────────────────────────────────────────────────────

class Renderer:
    def __init__(self, job):
        self.job = job
        pal = job["palette"]
        self.paper = hex2rgb(pal["paper"])
        self.ink = hex2rgb(pal["ink"])
        self.red = hex2rgb(pal["red"])
        self.dim = hex2rgb(pal["dim"])
        self.F = Fonts()
        self.cw, self.ch = job["card"]["w"], job["card"]["h"]
        self.s = self.cw / BASE_W          # 排版等比系数
        self.members = job["members"]

    def px(self, v):
        return round(v * self.s)

    # —— 单人卡 ——
    def card(self, m):
        W, H, s = self.cw, self.ch, self.s
        canvas = paper(W, H, self.paper)
        d = ImageDraw.Draw(canvas)
        M = self.px(110)                     # 左边距，也是文字栏左缘

        tracked(d, self.job["series"], (M, self.px(92)),
                self.F.serif(self.px(19), 560), self.dim + (255,), self.px(3.2))
        tracked(d, "%s / %02d" % (m["no"], len(self.members)), (self.px(162), self.px(94)),
                self.F.latin(self.px(19)), self.dim + (255,), self.px(1.6))
        seat_px = self.px(40)
        canvas.alpha_composite(seat(seat_px, m.get("seat", "solid"), self.ink, self.red),
                               (W - M - seat_px, self.px(86)))

        # 文字栏宽度从配置推，立绘据此让位
        body_max = self.px(380)
        p = self.job["portrait"]
        fig_h = self.px(p["height"]) if p["height"] < 10 else p["height"] * s
        fig_cx = self.px(p["cx"] * BASE_W) if p["cx"] < 1 else self.px(p["cx"])
        fig = self._fit(m["portrait"], fig_h * m.get("scale", 1.0), fig_cx,
                        M + body_max + self.px(16), W - self.px(6), p)
        fig_left = round(fig_cx - fig.width / 2)
        canvas.alpha_composite(fig, (fig_left, H - fig.height))

        # 姓名：竖排 + 朱红细线，线离字 10px；再远就变成两个不相干的元素
        name_size = self.px(100)
        step = round(name_size * 1.14)
        top = self.px(196)
        f_name = self.F.serif(name_size, 640)
        d.rectangle([M - self.px(4), top + self.px(6), M - self.px(2),
                     top + step * len(m["name"]) - self.px(22)], fill=self.red + (255,))
        vtext(d, m["name"], M + self.px(60), top, f_name, self.ink + (255,), step)

        ny = top + step * len(m["name"]) + self.px(14)
        if m.get("roman"):
            tracked(d, m["roman"], (M + self.px(8), ny),
                    self.F.latin(self.px(19)), self.dim + (255,), self.px(4.2))
        if m.get("role"):
            tracked(d, m["role"], (M + self.px(8), ny + self.px(40)),
                    self.F.serif(self.px(21), 500), self.ink + (200,), self.px(2.0))

        d.line([(M, ny + self.px(96)), (M + body_max, ny + self.px(96))],
               fill=self.ink + (44,), width=1)

        f_body = self.F.serif(self.px(self.job["text"]["body_size"]), 480)
        y = ny + self.px(152)
        for line in m.get("body", []):
            for seg in wrap(line, f_body, body_max):
                d.text((M, y), seg, font=f_body, fill=self.ink + (222,))
                y += self.px(54)

        # 引文：宽度不能踩进立绘，深墨字压在深色衣服上会直接消失
        f_q = self.F.serif(self.px(self.job["text"]["quote_size"]), 500)
        qmax = fig_left - M - self.px(26)
        qlines = []
        for seg in m.get("quote", []):
            qlines += wrap(seg, f_q, qmax)
        if qlines:
            qy = self.px(1146)
            bar = self.px(38) * len(qlines) - self.px(12)
            d.rectangle([M, qy + self.px(6), M + self.px(3), qy + self.px(6) + bar],
                        fill=self.red + (255,))
            for i, line in enumerate(qlines):
                d.text((M + self.px(26), qy + i * self.px(38)), line,
                       font=f_q, fill=self.ink + (238,))

        self._footer(canvas, d, W, H)
        corner_ticks(d, W, H, self.px(44), self.px(30), self.ink + (46,))
        return canvas

    def _fit(self, path, h, cx, min_left, max_right, p):
        """立绘收窄：先按配置高度试，压到文字栏或越右边界就等比缩小。"""
        for _ in range(24):
            fig = load_figure(path, h, p["crop"], self.paper,
                              p.get("fade_start", 0.9), p.get("fade_len", 0.10),
                              p.get("fade_rate", 2.0))
            left, right = cx - fig.width / 2, cx + fig.width / 2
            if left >= min_left and right <= max_right:
                return fig
            h = int(h * 0.97)
        return fig

    def _footer(self, canvas, d, W, H):
        m = self.px(110)
        lock = seat(self.px(24), "solid", self.ink, self.red)
        canvas.alpha_composite(lock, (m, H - self.px(128)))
        if self.job["project"]:
            tracked(d, self.job["project"], (m + self.px(36), H - self.px(132)),
                    self.F.serif(self.px(17), 620), self.ink + (235,), self.px(2.4))
        if self.job["project_en"]:
            tracked(d, self.job["project_en"], (m + self.px(36), H - self.px(106)),
                    self.F.latin(self.px(10)), self.dim + (255,), self.px(1.5))
        if self.job["title"]:
            # 作品名只放左侧：右侧整条竖带都被人物的深色衣服占着，灰字压上去等于没有
            f = self.F.serif(self.px(13), 420)
            tracked(d, self.job["title"], (m, H - self.px(56)),
                    f, self.dim + (255,), self.px(1.8))

    # —— 阵容图 ——
    def lineup(self):
        cfg = self.job["lineup"]
        w, h = cfg["w"], cfg["h"]
        s = w / BASE_W / 2                       # 阵容宽是卡片的三倍，系数另算
        canvas = paper(w, h, self.paper)
        d = ImageDraw.Draw(canvas)
        n = len(self.members)
        col = w // n

        tracked(d, self.job["series"], (56, 88),
                self.F.serif(30, 600), self.ink + (255,), 5.0)
        tracked(d, "CAST", (56, 132), self.F.latin(15), self.dim + (255,), 4.0)
        if self.job["project_en"]:
            f = self.F.latin(13)
            tw = twidth(self.job["project_en"], f, 2.2)
            tracked(d, self.job["project_en"], (w - 100 - tw, 96), f, self.dim + (255,), 2.2)
        if self.job["title"]:
            f = self.F.serif(17, 500)
            tw = twidth(self.job["title"], f, 2.4)
            tracked(d, self.job["title"], (w - 100 - tw, 124), f, self.ink + (200,), 2.4)

        for i, m in enumerate(self.members):
            cx = col * i + col // 2
            if i:
                d.line([(col * i, 210), (col * i, h - 48)], fill=self.ink + (36,), width=1)

            canvas.alpha_composite(seat(34, m.get("seat", "solid"), self.ink, self.red),
                                   (col * i + 56, 214))
            tracked(d, m["no"], (col * i + 102, 218), self.F.latin(17), self.dim + (255,), 1.6)
            if m.get("roman"):
                tracked(d, m["roman"], (col * i + 56, 300), self.F.latin(14), self.dim + (255,), 2.8)
            if m.get("role"):
                tracked(d, m["role"], (col * i + 56, 330), self.F.serif(15, 460),
                        self.ink + (190,), 1.6)

            vtext(d, m["name"], col * (i + 1) - 78, 246, self.F.serif(56, 620),
                  self.ink + (255,), 64)

            p = self.job["portrait"]
            fh = load_figure(m["portrait"], cfg["height"] * m.get("scale", 1.0),
                             p["crop"], self.paper,
                             p.get("fade_start", 0.9), p.get("fade_len", 0.10),
                             p.get("fade_rate", 2.0))
            canvas.alpha_composite(fh, (round(cx - fh.width / 2), h - fh.height))

        corner_ticks(d, w, h, 48, 34, self.ink + (46,))
        return canvas


# ── 输出 ──────────────────────────────────────────────────────────────────

def preview_html(job, names):
    cards = "\n".join(
        """    <figure>
      <a href="jpg/%s.jpg" target="_blank"><img src="jpg/%s.jpg" alt="%s"></a>
      <figcaption><span class="n">%s</span>%s%s</figcaption>
    </figure>""" % (n, n, n, m["no"], m["name"], "　" + m["roman"] if m.get("roman") else "")
        for n, m in zip(names, job["members"]))
    lineup = ""
    if job["lineup"]["enabled"]:
        ln = "阵容图_%d人" % len(job["members"])
        lineup = """  <div class="lineup">
    <figure>
      <a href="jpg/%s.jpg" target="_blank"><img src="jpg/%s.jpg" alt="阵容"></a>
      <figcaption>阵容图 ／ %d×%d</figcaption>
    </figure>
  </div>""" % (ln, ln, job["lineup"]["w"], job["lineup"]["h"])
    return """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>%(series)s · %(title)s</title>
<style>
  :root{--paper:%(paper)s;--ink:%(ink)s;--red:%(red)s;--dim:%(dim)s;}
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--paper);color:var(--ink);font-family:"Noto Serif SC","Songti SC",serif;
       padding:72px 56px 96px;-webkit-font-smoothing:antialiased}
  .wrap{max-width:1560px;margin:0 auto}
  header{display:flex;align-items:flex-end;justify-content:space-between;
         border-bottom:1px solid rgba(29,32,39,.18);padding-bottom:24px;margin-bottom:44px}
  h1{font-size:38px;font-weight:600;letter-spacing:.28em;line-height:1}
  h1 span{display:block;font-size:11px;letter-spacing:.34em;color:var(--dim);
          margin-top:16px;font-weight:400}
  header .right{text-align:right;font-size:12px;color:var(--dim);line-height:2;letter-spacing:.14em}
  header .right b{color:var(--ink);font-weight:600;font-size:15px}
  .cards{display:grid;grid-template-columns:repeat(%(cols)d,1fr);gap:32px}
  figure a{display:block}
  figure img{width:100%%;display:block;border:1px solid rgba(29,32,39,.10);
             transition:transform .45s cubic-bezier(.2,.7,.2,1)}
  figure a:hover img{transform:translateY(-4px)}
  figcaption{display:flex;align-items:baseline;gap:10px;margin-top:14px;
             font-size:12px;color:var(--dim);letter-spacing:.1em}
  figcaption .n{color:var(--red);font-weight:600}
  .lineup{margin-top:56px}
  footer{margin-top:52px;padding-top:22px;border-top:1px solid rgba(29,32,39,.14);
         display:flex;justify-content:space-between;font-size:11.5px;color:var(--dim)}
</style></head><body><div class="wrap">
  <header>
    <h1>%(series)s<span>CAST ／ %(title)s</span></h1>
    <div class="right"><b>%(project)s</b><br>%(project_en)s</div>
  </header>
  <div class="cards">
%(cards)s
  </div>
%(lineup)s
  <footer><span>%(cw)d×%(ch)d ／ %(n)d 张单卡%(plus)s</span>
  <span>纸 %(paper)s　墨 %(ink)s　朱红 %(red)s</span></footer>
</div></body></html>
""" % dict(series=job["series"], title=job["title"], project=job["project"],
           project_en=job["project_en"], paper=job["palette"]["paper"],
           ink=job["palette"]["ink"], red=job["palette"]["red"], dim=job["palette"]["dim"],
           cards=cards, lineup=lineup, cols=min(3, max(1, len(job["members"]))),
           cw=job["card"]["w"], ch=job["card"]["h"], n=len(job["members"]),
           plus=" + 阵容图" if job["lineup"]["enabled"] else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("job", help="job.json 路径")
    ap.add_argument("--out", help="覆盖输出目录")
    ap.add_argument("--no-lineup", action="store_true")
    args = ap.parse_args()

    job = json.load(open(args.job, encoding="utf-8"))
    for k, v in DEFAULTS.items():
        if k not in job:
            job[k] = v
        elif isinstance(v, dict):
            for kk, vv in v.items():
                job[k].setdefault(kk, vv)
    if args.no_lineup:
        job["lineup"]["enabled"] = False
    if not job["members"]:
        raise SystemExit("job.json 里 members 是空的")

    # 素材缺失时给一条能直接照做的提示，别让用户看 traceback
    missing, lack = [], []
    for m in job["members"]:
        for k in ("no", "name", "portrait"):
            if not m.get(k):
                lack.append("第 %s 个成员缺字段 %s" % (m.get("no", "?"), k))
        if m.get("portrait") and not os.path.exists(m["portrait"]):
            missing.append(m["portrait"])
    if lack or missing:
        msg = []
        if lack:
            msg.append("配置不全：\n  " + "\n  ".join(lack))
        if missing:
            msg.append("找不到立绘（相对当前工作目录 %s）：\n  %s" % (os.getcwd(), "\n  ".join(missing)))
        raise SystemExit("\n".join(msg))

    out = args.out or job["out"]
    os.makedirs(os.path.join(out, "jpg"), exist_ok=True)
    r = Renderer(job)

    names = []
    for m in job["members"]:
        base = "%s_%s" % (m["no"], m["name"])
        names.append(base)
        im = r.card(m)
        png = os.path.join(out, "%s.png" % base)
        im.convert("RGB").save(png, quality=95)
        im.convert("RGB").save(os.path.join(out, "jpg", "%s.jpg" % base),
                               quality=92, subsampling=0)
        print("card  ->", png, im.size)

    if job["lineup"]["enabled"]:
        ln = "阵容图_%d人" % len(job["members"])
        im = r.lineup()
        im.convert("RGB").save(os.path.join(out, "%s.png" % ln), quality=95)
        im.convert("RGB").save(os.path.join(out, "jpg", "%s.jpg" % ln),
                               quality=92, subsampling=0)
        print("lineup->", os.path.join(out, "%s.png" % ln), im.size)

    open(os.path.join(out, "index.html"), "w", encoding="utf-8").write(preview_html(job, names))
    print("preview->", os.path.join(out, "index.html"))


if __name__ == "__main__":
    main()
