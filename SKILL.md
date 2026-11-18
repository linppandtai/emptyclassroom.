---
name: cast-card
description: 生成人物 / 角色介绍卡与阵容图。竖排姓名、立绘、身份、三行描述、原文引文，右上角一个座位标记；单人卡 1080×1440，阵容图 2160×1440，同时输出 PNG、JPG 和一张预览页。当用户要做人物卡、角色设定图、人物介绍图、cast 介绍、角色阵容图、galgame / 游戏 / 小说人物介绍时使用。
agent_created: true
---

# cast-card

## 用途

把一组角色（名字、罗马字、身份、立绘、几句描述、一句原文）批量排成统一的人物介绍卡，外加一张多人并排的阵容图。

排版是一套固定的编辑设计语言：**窄文字栏 + 高立绘 + 竖排姓名 + 朱红细线**。几何全部写在 `scripts/cast_card.py` 里，换配色和文案不需要动代码。

## 何时使用

- 用户说「把里面的角色做成人物介绍图」「做几张角色卡」「来个 cast 页」「角色阵容图」
- 已有一组透明底立绘 PNG，需要成套的平面物料
- 已有品牌色（纸 / 墨 / 强调色），需要与海报、logo 保持同一套视觉

不适用：真人照片排版、需要角色互动姿势的插画、单张海报（那是 poster 类任务）。

## 工作流

### 1. 先把素材和文案备齐

从项目里找出每个角色的透明底立绘（`.png`，带 alpha），以及原文 / 设定资料。**文案有两条硬要求**：

- **引文必须是原文**，逐句回源文件核对。写错的引文比不写更伤——尤其是作者自己发的。
- **描述三行不加引号**。只有引文块用「」，让读者一眼分清哪句是复述、哪句是原作。描述是作者视角的概括（可以写「他数过椅子」），不能伪装成台词。

若同一角色有多个表情，先只做主卡（默认表情），表情变体留作后续可选。

### 2. 写 job.json

复制 `assets/job.example.json` 修改。路径用相对项目根的相对路径，在项目根目录执行脚本。

| 字段 | 说明 |
|---|---|
| `title` / `series` / `project` / `project_en` | 作品名、系列名（默认「人物」）、企划名与英文名 |
| `palette` | `paper` / `ink` / `red` / `dim`，十六进制 |
| `card` | `w` / `h`，默认 1080×1440。排版按 `w/1080` 等比缩放，换尺寸不用改代码 |
| `portrait` | `height`（立绘高）、`cx`（水平中心，≤1 视为画布比例）、`crop`（裁到底部留多少，默认 0.86）、`fade_start` / `fade_len` / `fade_rate`（底部渐隐） |
| `text` | `body_size` / `quote_size` |
| `members[]` | `no` `portrait` `name` `roman` `role` `seat`(`solid`/`hollow`) `body[]` `quote[]` `scale` |
| `lineup` | `enabled` / `w` / `h` / `height` |

`quote` 是**数组**，一行一条：自动断行会把「明天」这类词拆开，断点必须人工给。

`seat` 用 `hollow` 表示「这个人不在座位上」——空框是朱红的，是全卡唯一的彩色标记，用来标缺席者。

### 3. 跑一次，校准

```bash
python <skill>/scripts/cast_card.py job.json --no-lineup   # 先只验证单卡
python <skill>/scripts/cast_card.py job.json               # 再加阵容图
```

只需要看两件事：

- **立绘有没有压到文字栏**：脚本会自动等比缩小（`_fit`），但缩太多人像就小了。把 `portrait.height` 调小、或 `cx` 右移，能换回更多空间。
- **多人并排是否一样高**：不同立绘的画幅比例差很大（656×1511 与 447×1734 并存很常见），统一「高度」会让画幅更长的人显得矮一头。给这个人加 `scale`（1.05~1.15 之间试）。

### 4. 交付

输出目录会有 `<no>_<name>.png`、`jpg/` 同名 JPG（quality 92，发微信 / 贴文章用）、`index.html` 预览页（三列网格 + 阵容通栏，点击看原图）。用 `present_files` 把预览页和需要的图一起给出去。

## 排版规则

未读 `references/layout.md` 不要改几何数字。那里写了每条规则的来历，含四个踩破过的问题（文字被深色衣服吃掉、引文孤字落行首、底部灰雾、立绘越界出血）和各尺寸的比例依据。

一句话版本：**文字永远在立绘左缘以外；立绘脚部先沉底再渐隐；断点人工定；立绘高度自动收窄。**

## 环境

- Python 3 + `numpy` + `Pillow`
- 字体：中文衬线优先 `NotoSerifSC-VF.ttf`（可变字重，`set_variation_by_axes` 才有粗细层次），拉丁用 Georgia。脚本内有候选路径表，换 macOS / Linux 时自动 fallback；全找不到会报错并列出候选，按提示补一条。
- 若报 `ModuleNotFoundError: numpy`，说明当前解释器是精简版：换用带依赖的解释器（如 `~/.workbuddy/binaries/python/envs/default/Scripts/python`），或 `pip install numpy pillow`。不要为此改写脚本。

## 资源

- `scripts/cast_card.py` 渲染器（唯一需要执行的代码）
- `references/layout.md` 几何比例与四条踩坑记录
- `assets/job.example.json` 可直接改的配置模板
