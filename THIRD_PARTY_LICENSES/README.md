# THIRD_PARTY_LICENSES — 第三方原许可文本副本

本目录是**上游许可证原文的机械副本**，不含任何第三方源码或模型权重。
清单与逐字校验见 [MANIFEST.json](MANIFEST.json)；人读的说明见仓库根
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) 与 [NOTICE](../NOTICE)。

## 为什么单独放一个目录

AGPL-3.0-only 要求随分发附上第三方原许可与源码提供入口。本仓的发布包是**源码形态**，
不带 `site-packages`，所以把「本机实际安装的那一份 dist-info 许可文件」原样导出到这里，
让只拿到源码的人不必先装依赖就能读到上游原文。

## 三类来源

| 来源 | 含义 | 重新获取 |
|---|---|---|
| `python/` | 从本机 venv 的 `*.dist-info` 里**逐字复制**的原许可与 NOTICE 文件 | `scripts/collect_license_notices.py --export`（需本机 venv，无需网络） |
| `upstream/` | dist 内 0 份许可文件的少数项（ctranslate2 / rapidocr-onnxruntime）与两个随包再分发的模型权重，取自**上游官方 LICENSE** | `scripts/collect_license_notices.py --fetch-upstream`（需网络） |
| `MANIFEST.json` | 每份文本的 dist／版本／SPDX／来源 URL／sha256 | `--verify` 重算 sha256（不需要 venv，CI 可跑） |

## 边界

`LicenseRef-OpenCV-Platform-Components` 指向随附的 OpenCV 原组件许可清单，
不是一份新增授权或豁免。不同平台的实际捆绑组件分别适用自己的条件，不能任选一种许可覆盖全部。

- 这些文本**只保留上游版权人的原样声明**，不改写、不折叠复合许可（`AND`/`OR` 原样保留），
  也不由本项目的 AGPL 覆盖或替换——第三方组件继续按各自原许可条款使用。
- 本目录**不声称**清单穷尽了全部依赖树；`THIRD_PARTY_NOTICES.md` 里逐条标了覆盖分母与状态。
- 容器镜像里 `pip install` 会各自安装对应 dist-info 的许可文件，与本目录内容同源。
- 模型权重**不在本目录**：这里只有其上游模型仓的 MIT 原文。
