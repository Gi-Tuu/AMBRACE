"""控制台设计系统（V1 基座）：字号阶梯 / 间距栅格 / 图标与照片渲染器。

这里是**唯一真源**——`server_controller.py` 里不应再出现裸字号、裸色号与手绘图标。

三条实测前提（2026-09-21）：
- Tk 8.6、`scaling≈1.332`（128% DPI）：正字号按「点」走，Tk 自己乘缩放；**间距不会自动缩放**，
  所以 `SP` 必须显式乘 scale，否则高分屏下字大间距小、版面发挤。
- 环境有 Pillow 12.3 + ImageTk：图标只需一套**纯白透明** PNG，运行时按主题染色，
  一套素材吃三套主题（否则 21 枚 × 3 主题 = 63 张）。
- 照片级素材（`assets/photo/`）四角亮度 7–8、暖色占比 0%，本身就是近黑，
  贴在 `#0D1424` 卡上会形成"内凹画框"，不需要抠背景；但**主体也近黑**，所以取图时
  统一走 gamma 0.72 抬中间调（实测数字见 `PhotoStore._lift`）。
"""
from __future__ import annotations

import pathlib
import tkinter as tk

from PIL import Image, ImageTk

ASSETS = pathlib.Path(__file__).resolve().parent / "assets"

# ── 字体族 ──────────────────────────────────────────────────────────
FONT_UI = "Microsoft YaHei UI"      # 中文界面
# 数字/ID/时间戳一律等宽：KPI 数值在卡片之间不再跳位（Win11 预装，缺失时退 Consolas）
FONT_NUM = "Cascadia Mono"
_FONT_NUM_FALLBACK = "Consolas"


def resolve_num_font(root: tk.Misc) -> str:
    """等宽字体可用性检测（缺 Cascadia Mono 时退 Consolas，都没有就退回 UI 字体）。"""
    try:
        import tkinter.font as tkfont
        fams = set(tkfont.families(root))
        for cand in (FONT_NUM, _FONT_NUM_FALLBACK):
            if cand in fams:
                return cand
    except Exception:
        pass
    return FONT_UI


def init_fonts(root: tk.Misc) -> str:
    """启动时解析一次等宽族名并写回模块级 FONT_NUM（f() 之后取到的都是解析结果）。"""
    global FONT_NUM
    FONT_NUM = resolve_num_font(root)
    return FONT_NUM


# ── 字号阶梯（点；Tk 会按 DPI 缩放）───────────────────────────────────
# 语义命名，禁止页面里再写裸数字字号
TYPE = {
    "h1": 16,        # 页面主标题
    "h2": 14,        # 卡片 / 小节标题
    "title": 12,     # 侧栏项 / 小节标题
    "body": 11,      # 正文
    "caption": 10,   # 次要说明
    "micro": 9,      # 极小注脚（轴标签等）
    "nano": 8,       # 图内标注（热力图轴标签这类，压在图形上）
    "num_xl": 22,    # KPI 主数值
    "num_lg": 16,    # 次级数值
    "num": 12,       # 表格内数字
}


def f(role: str, bold: bool = False, num: bool = False):
    """取字体元组：f('h2') / f('num_xl', num=True) 。"""
    fam = (FONT_NUM if num else FONT_UI)
    size = TYPE[role]
    return (fam, size, "bold") if bold else (fam, size)


# ── 间距栅格（8pt 体系，按 DPI 显式缩放）─────────────────────────────
_SP_BASE = {"xxs": 4, "xs": 8, "sm": 12, "md": 16, "lg": 24, "xl": 32}
_scale = 1.0


def set_scale(v: float) -> None:
    global _scale
    _scale = float(v) or 1.0


def sp(role: str) -> int:
    return max(1, round(_SP_BASE[role] * _scale))


def px(logical: int) -> int:
    """逻辑像素 → 物理像素（Tk 图片不自动缩放，必须自己乘）。"""
    return max(1, round(logical * _scale))


# ── 颜色工具 ────────────────────────────────────────────────────────
def hex_rgb(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


# ── 图标渲染器 ──────────────────────────────────────────────────────
class IconStore:
    """Lucide 纯白 PNG（96px）→ 按主题色染色 → 降采样到显示尺寸 → 缓存 PhotoImage。

    缓存是硬要求：Tk 的 PhotoImage 每次新建都会占一份 Tk 内部图像槽，
    hover/主题切换时反复重建会掉帧并泄漏。
    """

    def __init__(self, icon_dir: pathlib.Path | None = None):
        self.dir = icon_dir or (ASSETS / "icons")
        self._cache: dict[tuple, ImageTk.PhotoImage] = {}
        self._src: dict[str, Image.Image] = {}
        self._missing: set[str] = set()

    def _source(self, name: str) -> Image.Image | None:
        if name in self._src:
            return self._src[name]
        path = self.dir / f"{name}.png"
        if not path.exists():
            if name not in self._missing:
                self._missing.add(name)
            return None
        im = Image.open(path).convert("RGBA")
        self._src[name] = im
        return im

    def get(self, name: str, logical: int, color: str) -> ImageTk.PhotoImage | None:
        size = px(logical)
        key = (name, size, color)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        src = self._source(name)
        if src is None:
            return None
        r, g, b = hex_rgb(color)
        solid = Image.new("RGBA", src.size, (r, g, b, 255))
        alpha = src.getchannel("A")
        tinted = Image.composite(solid, Image.new("RGBA", src.size, (0, 0, 0, 0)), alpha)
        if tinted.size != (size, size):
            tinted = tinted.resize((size, size), Image.LANCZOS)
        ph = ImageTk.PhotoImage(tinted)
        self._cache[key] = ph
        return ph

    @property
    def missing(self) -> set[str]:
        return set(self._missing)


# ── 照片渲染器（V2 用；先建好通道与缓存）────────────────────────────
class PhotoStore:
    """照片级素材：cover 裁切 + 圆角渐隐 + 按显示尺寸缓存。

    源图 1024²–1536×1024（0.4–1.5MB），**绝不能按原始尺寸塞给 Tk**——PhotoImage 按原始
    像素吃内存，8 张就是几十 MB，且首次显示会卡。所以取图时就地降采样到显示尺寸。

    **尺寸一律是物理像素**（不是逻辑点）：Tk 的图片不随 DPI 缩放，而本项目里侧栏宽、
    卡片高本来就是未缩放的裸值，再套一层 px() 反而对不齐。调用方想按逻辑尺寸给值，
    自己乘 `px()`。
    """

    def __init__(self, photo_dir: pathlib.Path | None = None):
        self.dir = photo_dir or (ASSETS / "photo")
        self._cache: dict[tuple, ImageTk.PhotoImage] = {}
        self._src: dict[str, Image.Image] = {}
        self._missing: set[str] = set()

    def has(self, name: str) -> bool:
        """素材在不在。调用方用它决定「有图版式 / 无图版式」，缺图时版面与上版一致。"""
        return (self.dir / name).exists()

    def _source(self, name: str) -> Image.Image | None:
        if name in self._src:
            return self._src[name]
        if name in self._missing:
            return None
        try:
            im = Image.open(self.dir / name).convert("RGBA")
        except (OSError, ValueError):
            self._missing.add(name)   # 缺文件或解码失败：记住，别每次刷新都重试一遍
            return None
        self._src[name] = im
        return im

    def get(self, name: str, w: int, h: int | None = None, radius: int = 0,
            feather: int = 0, mode: str = "cover",
            gamma: float = 0.72) -> ImageTk.PhotoImage | None:
        """→ PhotoImage；素材缺失/损坏返回 None（调用方走无图回退，不留空框）。

        mode：`cover` 等比放大后居中裁满目标框；`contain` 等比缩进框内居中留边；
        `panel` 按高等比缩放贴左、右侧空出的部分交给画框底色（横幅槽位用，见 `_to_panel`）。
        """
        key = (name, w, h, radius, feather, mode, gamma)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        im = self._source(name)
        if im is None:
            return None
        iw, ih = im.size
        if h is None:
            h = max(1, round(ih * w / iw))
        if mode == "contain":
            out = self._to_contain(im, w, h)
        elif mode == "panel":
            out = self._to_panel(im, w, h)
        else:
            out = self._to_cover(im, w, h)
        if gamma and gamma != 1.0:
            out = self._lift(out, gamma)
        if feather or radius:
            # 与既有 alpha 求交（不是覆盖）：panel 模式的右缘渐隐要保住
            base = out.getchannel("A")
            out.putalpha(Image.composite(base, Image.new("L", (w, h), 0),
                                         self._mask(w, h, radius, feather)))
        ph = ImageTk.PhotoImage(out)
        self._cache[key] = ph
        return ph

    @staticmethod
    def _lift(im: Image.Image, gamma: float) -> Image.Image:
        """暗部提亮（只动 RGB，alpha 原样保留）。

        这批素材压得极狠：显示尺寸下整幅均值只有 12–17，而卡片底 `#0D1322` 本身就是 22
        ——原样贴上去**主体比卡片还黑**，空态插画等于没画。gamma<1 抬中间调后均值到 26、
        P95 从 34 升到 58，主体可读；同时四边仍接近卡片色，"内凹画框"的无缝感不丢。
        （对照过"换深色卡纸 + 整体提亮"，那条会在卡上画出一个硬边黑矩形，已弃用。）
        """
        lut = tuple(min(255, int(255 * ((i / 255.0) ** gamma))) for i in range(256))
        r, g, b, a = im.split()
        return Image.merge("RGBA", (r.point(lut), g.point(lut), b.point(lut), a))

    @staticmethod
    def _to_cover(im: Image.Image, w: int, h: int) -> Image.Image:
        iw, ih = im.size
        # 直接 resize 成 (w,h) 会把照片拉变形，所以先放大到覆盖、再居中裁
        s = max(w / iw, h / ih)
        r = im.resize((max(w, round(iw * s)), max(h, round(ih * s))), Image.LANCZOS)
        x, y = (r.size[0] - w) // 2, (r.size[1] - h) // 2
        return r.crop((x, y, x + w, y + h))

    @staticmethod
    def _to_contain(im: Image.Image, w: int, h: int) -> Image.Image:
        iw, ih = im.size
        s = min(w / iw, h / ih)
        r = im.resize((max(1, round(iw * s)), max(1, round(ih * s))), Image.LANCZOS)
        out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        out.paste(r, ((w - r.size[0]) // 2, (h - r.size[1]) // 2))
        return out

    @staticmethod
    def _to_panel(im: Image.Image, w: int, h: int) -> Image.Image:
        """横幅模式：照片按高等比缩放贴左，右侧留出台面给文字。

        为什么裁成横条不行——这批 hero 的构图是「左上表盘 + 右侧大片空黑」，
        cover 到 5:1 的条里正好把表盘切掉。而素材右半本来就是近黑，直接接上画框底色
        读起来是同一块空间；再给它右缘一段渐隐，避免照片与底色之间出现一条竖直接缝。
        """
        from PIL import ImageDraw
        iw, ih = im.size
        s = min(h / ih, w / iw)          # 目标框太窄时退化为按宽缩放（不溢出）
        pw = max(1, round(iw * s))
        r = im.resize((pw, max(1, round(ih * s))), Image.LANCZOS)
        out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        a = r.getchannel("A")
        if pw < w:
            fade = max(16, round(pw * 0.34))
            ramp = Image.new("L", (pw, r.size[1]), 255)
            d = ImageDraw.Draw(ramp)
            for i in range(fade):
                # i 从最右列往里数：最右列必须全透明，否则照片与底色之间是一条硬竖线
                v = int(255 * (i / fade) ** 1.6)
                d.line([(pw - 1 - i, 0), (pw - 1 - i, r.size[1])], fill=v)
            a = Image.composite(a, Image.new("L", (pw, r.size[1]), 0), ramp)
        r.putalpha(a)
        out.paste(r, (0, (h - r.size[1]) // 2), r)
        return out

    @property
    def missing(self) -> set[str]:
        return set(self._missing)

    @staticmethod
    def _mask(w: int, h: int, radius: int, feather: int) -> Image.Image:
        from PIL import ImageDraw, ImageFilter
        m = Image.new("L", (w, h), 0)
        d = ImageDraw.Draw(m)
        d.rounded_rectangle([0, 0, w - 1, h - 1], radius=radius or 12, fill=255)
        if feather:
            # 内部必须是不透明 255，只让最外 feather 圈由 0 渐亮到 255。
            # 早先这里从 0 起画、内部留空，整幅照片与它求交后变成全透明——
            # 表现就是"素材明明在、画框却是一片空白"，只有真机截图才抓得出来。
            ramp = Image.new("L", (w, h), 255)
            dr = ImageDraw.Draw(ramp)
            for i in range(feather):
                a = int(255 * (i / feather) ** 1.5)
                dr.rectangle([i, i, w - 1 - i, h - 1 - i], outline=a)
            ramp = ramp.filter(ImageFilter.GaussianBlur(3))
            # 圆角内取渐隐值、圆角外取 0 ＝ 两者交集
            m = Image.composite(ramp, m, m)
        return m


# ── 开关组名（镜像 backend/app/application/flag_catalog.py 的 CATALOG_GROUPS id）──
# 后端 meta 只给 group id 与 group_order，中文组名由展示端各自映射（App 侧
# feature_flag_catalog.dart 同样是本地映射，属既定模式，不是第二真源）。
# 未知 id 直接显示原 id，不会丢键。
GROUP_LABELS = {
    "agent": "智能体运行与认知",
    "proactive": "主动消息",
    "games": "群聊小游戏",
    "life": "AI 自主生活",
    "lifesense": "生命感增强",
    "outreach_natural": "主动消息自然化",
    "memory": "记忆检索与注入",
    "curated": "编纂知识与前瞻意图",
    "cross_char": "跨角色用户事实",
    "working": "工作记忆",
    "provider": "插件与提供商",
    "pacing": "主动投放节制",
    "review": "主动复习与回忆化",
    "channel": "渠道绑定与群认知",
    "tool_trace": "工具轨迹治理",
    "other": "其他高级开关",
}


# ── 自绘控件 ────────────────────────────────────────────────────────
class Switch(tk.Canvas):
    """圆角滑块开关，替代原生 ttk.Checkbutton（黑底方框与自绘卡片质感冲突）。

    `variable` 仍是 BooleanVar，业务侧读写口径不变；`locked=True` 时只展示不可点
    （服务器锁定态），并用置灰 + 无手型光标表达"这里改不了"。
    """

    def __init__(self, parent, theme, variable=None, locked: bool = False,
                 logical_w: int = 40, logical_h: int = 22, command=None, bg: str | None = None):
        self._t = theme
        self._var = variable
        self._locked = locked
        self._command = command
        # 底色必须能跟随所在行（斑马纹/锁定高亮），否则开关周围会出现一块异色方框
        self._bg = bg or getattr(theme, "card", "#0D1424")
        self._pw = px(logical_w)
        self._ph = px(logical_h)
        super().__init__(parent, width=self._pw, height=self._ph,
                         bg=self._bg, highlightthickness=0, bd=0,
                         cursor="" if locked else "hand2")
        self.bind("<Button-1>", self._on_click)
        self._draw()

    def _bg_color(self):
        return self._bg

    def _on_click(self, _event):
        if self._locked or self._var is None:
            return
        try:
            self._var.set(not bool(self._var.get()))
        except Exception:
            return
        self._draw()
        if self._command:
            self._command()

    def set_locked(self, locked: bool) -> None:
        self._locked = bool(locked)
        self.config(cursor="" if self._locked else "hand2", bg=self._bg)
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        t = self._t
        on = False
        try:
            on = bool(self._var.get()) if self._var is not None else False
        except Exception:
            on = False
        w, h = self._pw, self._ph
        r = max(1, h // 2)
        # ON 态＝暗青轨道 + 亮青滑块：整列开关同时亮时，发光的是滑块而不是一片青块，
        # 视觉噪音低一档，且"开在哪"反而更醒目（亮青实心轨道版在 30 个成列时过响）。
        track_on = getattr(t, "accent_dim", "#0E2B29")
        track_off = getattr(t, "hairline", "#1C2739")
        knob_on = getattr(t, "accent", "#5EEAD4")
        knob_off = getattr(t, "text_muted", "#5F6B82")
        if self._locked:
            track_on = getattr(t, "divider", "#111A2A")
            track_off = getattr(t, "divider", "#111A2A")
            knob_on = getattr(t, "text_muted", "#5F6B82")
        track = track_on if on else track_off
        knob = knob_on if on else knob_off
        # 轨道：圆角矩形（smooth 多边形近似，与 RoundedCard 同一手法）
        self.create_polygon(_rr(w - 1, h - 1, r), smooth=True, splinesteps=10,
                            fill=track, outline="")
        d = h - 4
        x0 = 2 if on else (w - d - 2)
        self.create_oval(x0, 2, x0 + d, 2 + d, fill=knob, outline="")


class Tooltip:
    """悬停浮层：用于被截断的说明文字、完整模型名 / Base URL。

    Tk 原生没有 tooltip；这里用 overrideredirect 的无边框 Toplevel，
    进入 500ms 后弹出、离开即销毁，不抢焦点。
    """

    _DELAY = 500

    def __init__(self, widget, text_getter):
        self.widget = widget
        self.text_getter = text_getter
        self._win = None
        self._after = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<Button-1>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        self._after = self.widget.after(self._DELAY, self._show)

    def _cancel(self):
        if self._after:
            try:
                self.widget.after_cancel(self._after)
            except Exception:
                pass
        self._after = None

    def _show(self):
        self._after = None
        try:
            text = str(self.text_getter() or "").strip()
        except Exception:
            return
        if not text:
            return
        self._hide()
        t = getattr(self.widget.master, "theme", None)
        bg = getattr(t, "surface_alt", "#0F1626") if t else "#0F1626"
        fg = getattr(t, "text", "#EAF0F9") if t else "#EAF0F9"
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        win = self._win = tk.Toplevel(self.widget)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        tk.Label(win, text=text, justify="left", bg=bg, fg=fg, bd=0,
                 font=(FONT_UI, TYPE["caption"]), wraplength=px(360)).pack(padx=10, pady=8)
        win.update_idletasks()
        win.geometry(f"+{x}+{y}")

    def _hide(self, _e=None):
        self._cancel()
        if self._win is not None:
            try:
                self._win.destroy()
            except Exception:
                pass
        self._win = None


def _rr(w: int, h: int, r: int):
    r = min(r, w / 2, h / 2)
    return [r, 0, w - r, 0, w, 0, w, r, w, h - r, w, h, w - r, h, r, h, 0, h, 0, h - r, 0, r, 0, 0]


# 旧 kind → Lucide 名（保持 _make_icon 调用方不改签名）。
# 故意**不**映射 "dot" 与 "diamond"：
#   "dot"     ＝ 5 处状态圆点（页头在线点 / Ollama / 登录条 / 管理页状态行 / 模态启用），应是实心圆；
#   "diamond" ＝ 页头品牌标记位。
# 两者落到 _paint_icon_fallback 的几何兜底才是正确表现，映射成图标反而会把圆点画成开关。
ICON_MAP = {
    "dashboard": "layout-dashboard",
    "server": "server",
    "log": "scroll-text",
}

ICONS = IconStore()
PHOTOS = PhotoStore()


# ── 照片画框 ────────────────────────────────────────────────────────
# 素材本身是近黑的照片（四角亮度 7–8），贴在深色卡上会自然形成"内凹画框"，不需要抠背景；
# 圆角 + 边缘渐隐是为了让照片边界不与卡片硬边打架。亮色主题下**不重出素材**：画框自带
# 深色底（photo_bg），照片永远装在深色面板里。
def photo_bg(t) -> str:
    """画框底色：深色主题用卡片色；亮色主题压成深色媒体面板。

    照片素材只出一套深色版——装进深色面板里就不必为亮色重出图，也不会出现
    "近黑照片坐在白卡上"那种脏边界。
    """
    return t.card if t.dark else mix(t.text, "#000000", 0.55)


def photo_fg(t) -> str:
    """压在照片上的文字色（照片四边近黑，所以亮色主题下也要给浅色字）。"""
    return t.text if t.dark else mix(t.text, "#FFFFFF", 0.86)


def photo_label(parent, t, name: str, w: int, h: int, radius: int = 0,
                feather: int = 0, bg: str | None = None) -> tk.Label | None:
    """固定尺寸的照片画框；素材缺失时返回 None（调用方走无图版式）。

    `bg` 只在画框要贴进非卡片色容器时才给（例如侧栏底部品牌牌），圆角外那圈会用该色，
    否则默认 `photo_bg(t)`。
    """
    ph = PHOTOS.get(name, w, h, radius=radius or px(10), feather=feather or px(5),
                    mode="cover")
    if ph is None:
        return None
    lab = tk.Label(parent, image=ph, bg=bg or photo_bg(t), bd=0)
    lab.image = ph          # 保引用：PhotoImage 被 GC 掉的话画框会变空白
    return lab


class PhotoBanner(tk.Frame):
    """高度固定、宽度跟随容器的照片画框。

    重采样有代价（解码 + LANCZOS），所以 `<Configure>` 去抖 120ms、且只在宽度真的变了时
    重取；同一宽度来回拖动直接命中 PhotoStore 缓存，不会反复解码。

    ⚠ Tk 子类里 `_w / _name / _h / _tclCommands` 是内部保留名，会被 `super().__init__()`
    覆盖成字符串（这里就差点踩：`self._name` 变成 `!photobanner`，素材查不到 → 画框静默
    塌成 0 高）。自定义属性一律加前缀语义名。
    """

    def __init__(self, parent, t, name: str, height: int, radius: int = 0,
                 feather: int = 0, min_w: int = 120, mode: str = "cover"):
        self.t = t
        self._asset = name
        self._box_h = max(1, int(height))
        self._radius = radius or px(10)
        self._feather = feather or px(5)
        self._min_w = min_w
        self._mode = mode
        self._last_w = 0
        self._job = None
        super().__init__(parent, bg=photo_bg(t), height=self._box_h)
        self.pack_propagate(False)      # 子件不得反向决定我的高度（否则换图 → 重排 → 再换图）
        self._label = tk.Label(self, bg=photo_bg(t), bd=0)
        self._label.pack(fill="both", expand=True)
        self.bind("<Configure>", self._on_cfg)

    def _on_cfg(self, event) -> None:
        w = max(self._min_w, int(event.width))
        if w == self._last_w:
            return
        self._last_w = w
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(120, lambda: self._renew(w))

    def _renew(self, w: int) -> None:
        self._job = None
        if not self.winfo_exists():
            return
        ph = PHOTOS.get(self._asset, w, self._box_h, radius=self._radius,
                        feather=self._feather, mode=self._mode)
        if ph is None:
            self.config(height=0)       # 素材缺失/解码失败：整块塌掉，不留空条
            return
        self._label.config(image=ph)
        self._label.image = ph



# ── 表格组件 ────────────────────────────────────────────────────────
def mix(c1: str, c2: str, ratio: float) -> str:
    """两色按 ratio 混合（0→c1，1→c2）。"""
    a, b = hex_rgb(c1), hex_rgb(c2)
    return "#%02X%02X%02X" % tuple(round(a[i] + (b[i] - a[i]) * ratio) for i in range(3))


class DataRow:
    """一行。`cell(i)` 拿到已落位到第 i 列、且带行底色的容器。"""

    def __init__(self, table: "DataTable", index: int, bg: str):
        self._table = table
        self._index = index
        self.bg = bg

    def cell(self, col: int) -> tk.Frame:
        f = tk.Frame(self._table, bg=self.bg)
        f.grid(row=self._index, column=col, sticky="we",
               padx=(self._table.pad_l if col == 0 else self._table.pad, self._table.pad),
               pady=3)
        return f

    def text(self, col: int, value: str, role: str = "body", fg: str | None = None,
             bold: bool = False, num: bool = False, side: str = "left",
             tip: str | None = None) -> tk.Label:
        lab = tk.Label(self.cell(col), text=value if value else "—", bg=self.bg,
                       fg=fg or self._table.t.text_sec, font=f(role, bold, num),
                       anchor="w", justify="left")
        lab.pack(side=side)
        if tip:
            Tooltip(lab, lambda d=tip: d)
        return lab


class DataTable(tk.Frame):
    """列权重表格：宽度按 weight 铺满整卡。

    旧版用 `tk.Label(width=字符数)` 拼列，两个后果：列一多就挤爆、宽屏时表格只占
    左侧一小块而右边全空。这里改成 grid weight 分配，行底色由 cell 容器自带，
    所以斑马纹是连续的（不会出现一格一色）。
    """

    def __init__(self, parent, theme, columns, **kw):
        self.t = theme
        self.cols = columns
        # 列间距用 sm 而不是 md：9 列表格里每列左右各吃 md(16) 会在 150% DPI 下
        # 共吃掉 ~200 物理像素，直接把「主账号/控制台」这类短列的表头挤断
        self.pad = sp("sm")
        self.pad_l = sp("lg")
        super().__init__(parent, bg=theme.card, **kw)
        self.pack(fill="x")
        for i, c in enumerate(columns):
            self.grid_columnconfigure(i, weight=int(c.get("weight", 1)),
                                      minsize=px(int(c.get("min", 0))))
        for i, c in enumerate(columns):
            tk.Label(self, text=c["label"], bg=theme.card, fg=theme.text_muted,
                     font=f("caption", True), anchor=c.get("align", "w")
                     ).grid(row=0, column=i, sticky="we",
                            padx=(self.pad_l if i == 0 else self.pad, self.pad),
                            pady=(0, 6))
        tk.Frame(self, bg=theme.hairline, height=1).grid(
            row=1, column=0, columnspan=len(columns), sticky="we")
        self._row = 2
        self._n = 0

    def add_row(self, bg: str | None = None) -> DataRow:
        if bg is None:
            bg = self.t.card if self._n % 2 == 0 else mix(self.t.card, self.t.card_hover, 0.55)
        self._n += 1
        row = DataRow(self, self._row, bg)
        self._row += 1
        return row


__all__ = ["ASSETS", "FONT_UI", "FONT_NUM", "TYPE", "f", "sp", "px", "set_scale",
           "resolve_num_font", "init_fonts", "IconStore", "PhotoStore", "ICONS", "PHOTOS",
           "photo_bg", "photo_fg", "photo_label", "PhotoBanner",
           "ICON_MAP", "GROUP_LABELS", "Switch", "Tooltip", "DataTable", "DataRow", "mix"]
