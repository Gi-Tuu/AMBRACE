# server_controller/assets — 控制台美术资产

## icons/ 与 icons_src/

- 来源：**Lucide**（`lucide-static` v1.47.0，**ISC 许可**，见 `LICENSE-lucide.txt`）。
- `icons_src/*.svg` 是官方原始矢量（`stroke="currentColor"`）；`icons/*.png` 是本地栅格化产物
  （96×96 纯白 + 透明底，共 23 枚 / 约 96KB），**不要手改 PNG**。
- 重新生成（改图标、升版本、换尺寸时）：

  ```
  backend\.venv\Scripts\python.exe server_controller\build_icons.py
  ```

  脚本会把 `currentColor` 换成纯白并用 Chrome/Edge headless 栅格化，需要本机装有 Chrome 或 Edge。

- **为什么只存一套白色 PNG**：控制台有 3 套主题（极光 / 暗色 / 亮色）。运行时由
  `console_ui.IconStore` 用 Pillow 按主题色染色 + 降采样到显示尺寸 + 缓存 `PhotoImage`，
  一套素材吃三套主题；缓存是必须的，否则 hover / 切主题会反复新建 Tk 图像槽并掉帧。

- 使用的 23 枚：`layout-dashboard` `server` `scroll-text` `brain` `users` `ticket`
  `toggle-right` `clipboard-list` `gauge`（导航 9）｜`refresh-cw` `copy` `log-in` `log-out`
  `save` `search` `chevron-down` `chevron-right` `trash-2` `more-horizontal`（动作 10）｜
  `check-circle-2` `alert-triangle` `x-circle` `help-circle`（状态 4）

- 两个**故意不映射**的旧 kind：`dot`（5 处状态圆点）与 `diamond`（页头品牌标记位）走
  `_paint_icon_fallback` 的几何兜底——它们本就该是实心圆/菱形，映射成图标反而会把状态点画成开关。

## photo/ — 照片级素材（8 张，合计约 93KB）

来源：**用户自己跑生图模型产出**（提示词清单与原始 PNG 一起放在仓库外的源目录里，路径由环境变量 `AMBRACE_PHOTO_SRC` 指定，不进仓）。
仓库里存的是**派生产物**：原始 PNG 8.6MB → 按显示尺寸降采样 + JPEG q84 = 93KB。

槽位与尺寸（长边）：

| 文件 | 用在哪 | 显示框（物理像素） | 模式 |
|---|---|---|---|
| `photo_about_hero.jpg` | 概览页顶部品牌横幅 | 整卡宽 × px(150) | `panel`（贴左 + 右缘渐隐，右侧留文字） |
| `photo_sidebar_brand.jpg` | 侧栏底部品牌画框 | 207 × px(110) | `cover` |
| `photo_empty_accounts.jpg` | 账号管理页空态 | px(150) 见方 | `cover` |
| `photo_empty_audit.jpg` | 审计页空态 | px(150) 见方 | `cover` |
| `photo_empty_log.jpg` | 运行日志页空态（压在文本区中央，有日志即撤） | px(150) 见方 | `cover` |
| `photo_empty_disconnected.jpg` | 管理页 404（接口未上线） | px(150) 见方 | `cover` |
| `photo_locked_gate.jpg` | 管理页 401（未登录 / 权限不足） | px(150) 见方 | `cover` |
| `photo_offline.jpg` | 管理页后端不可达 | px(150) 见方 | `cover` |

重新生成（用户出了新图、同名放进源目录后跑一次，**到货即生效、不用改代码**）：

```
backend\.venv\Scripts\python.exe server_controller\build_photos.py [源目录]
```

源目录由环境变量 `AMBRACE_PHOTO_SRC` 指定（未设置时原地重跑 `assets/photo`，等于无操作）；脚本按白名单挑正名（源目录里
带时间戳后缀的旧变体会被忽略），缺哪张就跳过哪张。

三条必须知道的口径：

1. **不带 alpha**：圆角与边缘渐隐由 `console_ui.PhotoStore` 运行时算（`putalpha`），
   所以素材本身存 JPEG 就够，不必为透明底付 PNG 的体积。
2. **一律走 gamma 0.72 提亮**：这批图压得极狠（显示尺寸下整幅均值 12–17，而卡片底
   `#0D1322` 本身亮度 22），原样贴上去主体比卡片还黑。提亮后均值 26、P95 从 34 升到 58，
   主体可读且四边仍接近卡片色，"内凹画框"的无缝感不丢。
3. **亮色主题不重出素材**：`photo_bg(t)` 在亮色下把画框压成深色媒体面板，照片永远装在
   深色框里；压在照片上的文字用 `photo_fg(t)`，避免深色字坐进深色照片。

