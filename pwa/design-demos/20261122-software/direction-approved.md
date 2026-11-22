# direction-approved

- 项目：AMBRACE PWA 生产 UI 方向
- 用户原话：**“工作台 那款吧”**
- 选中：`20261122-software/workbench.html`（01 工作台 / 主从结构）
- 参考截图：`pwa/design-demos/20261122-software/shots/workbench-desktop.png`、`workbench-mobile.png`
- 用户明确未选：阅读器（02）、控制台（03）、上一版海报式三方向（已作废）。
- 执行边界：把工作台的**信息架构与视觉语言**落进生产 PWA；不改变数据契约、不伪造上下文数据、不把未选方向的装饰性元素带进来。
- 生产实现后仍需 `flutter analyze`、`flutter test`、`flutter build web --release`，并做浏览器验收。
