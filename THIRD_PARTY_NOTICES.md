# 第三方组件许可清单 / Third-party notices

本文件逐项列出 AMBRACE **分发出去的**第三方软件、库、模型权重与素材及其原许可。
原许可文本的逐字副本在 [THIRD_PARTY_LICENSES/](THIRD_PARTY_LICENSES/)，
机器可读的校验清单在 [THIRD_PARTY_LICENSES/MANIFEST.json](THIRD_PARTY_LICENSES/MANIFEST.json)。

本清单自 **v3.5.4**（项目自有代码切换为 AGPL-3.0-only）起随每次发布维护；v3.5.3 及更早版本
为 MIT 时代，没有本文件 —— 但那些版本所分发的第三方组件同样受各自原许可约束，
其清单以各发布版本内的实际打包内容为准。

本清单的定位与边界：

- 第三方组件**继续按其原许可使用**，本项目的 AGPL-3.0-only 既不取代也不覆盖它们。
  复合许可的 `AND` / `OR` **原样保留，不折叠**。
- 本清单**不声称穷尽了全部依赖树**，只覆盖已逐项取得许可证据的条目；未取得证据的
  条目如实标为「无文件级证据」，不按通过计。
- 本文不是法律意见。

This file lists, item by item, the third-party software, libraries, model weights
and artwork that AMBRACE **redistributes**, together with their original licenses.
Verbatim copies live in [THIRD_PARTY_LICENSES/](THIRD_PARTY_LICENSES/).

This file is maintained with every release since **v3.5.4**, the release in which
this project's own code switched to AGPL-3.0-only. v3.5.3 and earlier were MIT
releases and had no such file; the third-party components they shipped remain
subject to their own original licenses, and their itemisation follows the
contents actually packaged in each of those releases.

Third-party components remain under their own licenses; this project's
AGPL-3.0-only grant neither replaces nor overrides them, and `AND` / `OR`
relations are preserved as written. This list does not claim to be exhaustive:
it covers the items for which license evidence has been obtained item by item.
This is not legal advice.

---

## 一、随源码分发的 Python 依赖 / Python dependencies (source distribution)

发布包（源码 zip 与源码仓）**不含 `site-packages`**；下表文本是从本机实际安装的那
一份 `*.dist-info` 里**逐字复制**出来的，供只拿源码的人查阅。容器镜像里
`pip install` 会各自安装同样的许可文件，内容同源。

The source distribution does not contain `site-packages`. The texts below were
copied verbatim from the `*.dist-info` of the actually installed distributions.

| 组件 / 组件版本 | 许可（不折叠） | 说明 | 文本 |
|---|---|---|---|
| lunardate 0.3.0 | **GPL-3.0-or-later** | 强 copyleft；进程内 import，用于农历节日换算。保留上游许可原文；本清单不改变其授权条件 | [LICENSE.txt](THIRD_PARTY_LICENSES/python/lunardate-0.3.0/LICENSE.txt) |
| edge-tts 7.2.8 | MIT **AND** LGPL-3.0-or-later | 按**文件**切分：仅 `src/edge_tts/srt_composer.py` 为 MIT，其余文件为 LGPLv3；进程内 import | [LICENSE](THIRD_PARTY_LICENSES/python/edge-tts-7.2.8/LICENSE) |
| shapely 2.1.2 | BSD-3-Clause **AND** LGPL-2.1-or-later | 本体 BSD-3；**LGPL-2.1 来自捆绑的 GEOS 动态库**（`shapely.libs/`），不是 shapely 本体。另有第三份 MSVC 运行时清单（无可识别许可正文） | [LICENSE.txt](THIRD_PARTY_LICENSES/python/shapely-2.1.2/LICENSE.txt) · [LICENSE_GEOS](THIRD_PARTY_LICENSES/python/shapely-2.1.2/LICENSE_GEOS) · [LICENSE_win32](THIRD_PARTY_LICENSES/python/shapely-2.1.2/LICENSE_win32) |
| opencv-python 5.0.0.93 | wrapper：MIT；捆绑组件：分别适用各自许可 | 不同组件的 Apache、BSD、ISC、Zlib、MPL、LGPL 等条件不可概括为任选其一。实际组件随平台与 wheel 而异，详见原文清单。项目源码 zip 不包含安装环境的 wheel；容器构建或运行环境安装、再分发二进制时，应按实际包含的组件履行相应义务 | [LICENSE.txt](THIRD_PARTY_LICENSES/python/opencv-python-5.0.0.93/LICENSE.txt) · [LICENSE-3RD-PARTY.txt](THIRD_PARTY_LICENSES/python/opencv-python-5.0.0.93/LICENSE-3RD-PARTY.txt) |
| numpy 2.5.2 | BSD-3-Clause **AND** 0BSD **AND** MIT **AND** Zlib **AND** CC0-1.0 **AND** (Apache-2.0 OR BSD-3-Clause) | ⚠ **实测 20 份许可文件，不是「一句 BSD」可覆盖**：含 highway 的 Apache-2.0/BSD-3 双选一、pythoncapi-compat 的 0BSD、dragon4/pcg64/sfc64 的 MIT、x86-simd-sort(Intel)/svml/pocketfft/lapack_lite 的 BSD-3、random-inl.h 与 splitmix64 的 CC0 | [numpy-2.5.2/](THIRD_PARTY_LICENSES/python/numpy-2.5.2/LICENSE.txt)（20 份） |
| orjson 3.12.0 | MPL-2.0 **AND** (Apache-2.0 OR MIT) | 三份许可文件齐全；**明确不是全 MIT** | [LICENSE-MPL-2.0](THIRD_PARTY_LICENSES/python/orjson-3.12.0/LICENSE-MPL-2.0) · [LICENSE-APACHE](THIRD_PARTY_LICENSES/python/orjson-3.12.0/LICENSE-APACHE) · [LICENSE-MIT](THIRD_PARTY_LICENSES/python/orjson-3.12.0/LICENSE-MIT) |
| tqdm 4.70.0 | MPL-2.0 **AND** MIT | 复合；文件内列明 MPL-2.0 例外条款。**MPL-2.0 全文**见 orjson 那一份 | [LICENCE](THIRD_PARTY_LICENSES/python/tqdm-4.70.0/LICENCE) |
| certifi 2026.7.22 | MPL-2.0 | 包内只有**引用块**、无 MPL 全文；全文见 orjson 那一份 | [LICENSE](THIRD_PARTY_LICENSES/python/certifi-2026.7.22/LICENSE) |
| regex 2026.9.3 | Apache-2.0 **AND** CNRI-Python | 复合；派生自 CPython 2.6/3.1 的 `re` 模块，后者是 CNRI 授权 | [LICENSE.txt](THIRD_PARTY_LICENSES/python/regex-2026.9.3/LICENSE.txt) |
| aiohttp 3.14.3 | Apache-2.0 **AND** MIT | 复合；另捆绑 `vendor/llhttp`（MIT） | [LICENSE.txt](THIRD_PARTY_LICENSES/python/aiohttp-3.14.3/LICENSE.txt) · [vendor/llhttp/LICENSE](THIRD_PARTY_LICENSES/python/aiohttp-3.14.3/vendor/llhttp/LICENSE) |
| httptools 0.8.0 | MIT | 捆绑 `vendor/http-parser`（MIT，Joyent）＋ `vendor/llhttp`（MIT，Fedor Indutny） | [LICENSE](THIRD_PARTY_LICENSES/python/httptools-0.8.0/LICENSE) · [vendor/http-parser/LICENSE-MIT](THIRD_PARTY_LICENSES/python/httptools-0.8.0/vendor/http-parser/LICENSE-MIT) · [vendor/llhttp/LICENSE](THIRD_PARTY_LICENSES/python/httptools-0.8.0/vendor/llhttp/LICENSE) |
| yarl 1.24.5 | Apache-2.0 | 上游**自带 NOTICE**，Apache-2.0 §4(d) 的随附义务对它天然可满足 | [LICENSE](THIRD_PARTY_LICENSES/python/yarl-1.24.5/LICENSE) · [NOTICE](THIRD_PARTY_LICENSES/python/yarl-1.24.5/NOTICE) |
| tzdata 2026.3 | Apache-2.0 | 短式声明（无全文）＋ `LICENSE_APACHE` 全文，两个都要 | [LICENSE](THIRD_PARTY_LICENSES/python/tzdata-2026.3/LICENSE) · [licenses/LICENSE_APACHE](THIRD_PARTY_LICENSES/python/tzdata-2026.3/licenses/LICENSE_APACHE) |
| multidict 6.7.1 | Apache-2.0 | 短式声明（**无全文**）；引用 orjson/tzdata 的 Apache-2.0 全文 | [LICENSE](THIRD_PARTY_LICENSES/python/multidict-6.7.1/LICENSE) |
| Pillow 12.3.0 | MIT-CMU | 单个 76KB 文件内混装 MIT/BSD/Apache/LGPL-2.1/GPL-2/GPL-3/Zlib 多段 | [LICENSE](THIRD_PARTY_LICENSES/python/Pillow-12.3.0/LICENSE) |
| psutil 7.2.2 / idna 3.19 / protobuf 7.36.1 / oauthlib 3.3.1 / python-dotenv 1.2.3 / jsonpatch 1.33 | BSD-3-Clause | 需保留 BSD 三条款与署名 | [psutil](THIRD_PARTY_LICENSES/python/psutil-7.2.2/LICENSE) · [idna](THIRD_PARTY_LICENSES/python/idna-3.19/LICENSE.md) · [protobuf](THIRD_PARTY_LICENSES/python/protobuf-7.36.1/LICENSE) · [oauthlib](THIRD_PARTY_LICENSES/python/oauthlib-3.3.1/LICENSE) · [python-dotenv](THIRD_PARTY_LICENSES/python/python-dotenv-1.2.3/LICENSE) · [jsonpatch](THIRD_PARTY_LICENSES/python/jsonpatch-1.33/LICENSE) |
| pyasn1 0.6.4 / xxhash 4.0.1 | BSD-2-Clause | 只有两条款，不是 BSD-3 | [pyasn1](THIRD_PARTY_LICENSES/python/pyasn1-0.6.4/LICENSE.rst) · [xxhash](THIRD_PARTY_LICENSES/python/xxhash-4.0.1/LICENSE) |
| charset-normalizer 3.5.1 / ecdsa 0.19.2 / sqlalchemy 2.0.52 / tiktoken 0.14.0 / durationpy 0.11 | MIT | 需保留版权头与 MIT 全文 | [charset-normalizer](THIRD_PARTY_LICENSES/python/charset-normalizer-3.5.1/LICENSE) · [ecdsa](THIRD_PARTY_LICENSES/python/ecdsa-0.19.2/LICENSE) · [sqlalchemy](THIRD_PARTY_LICENSES/python/sqlalchemy-2.0.52/LICENSE) · [tiktoken](THIRD_PARTY_LICENSES/python/tiktoken-0.14.0/LICENSE) · [durationpy](THIRD_PARTY_LICENSES/python/durationpy-0.11/LICENSE) |
| rank-bm25 0.2.2 / websocket-client 1.9.2 / frozenlist 1.8.0 / google-crc32c 1.8.0 / overrides 7.7.0 | Apache-2.0 | Apache-2.0 全文 | [rank-bm25](THIRD_PARTY_LICENSES/python/rank-bm25-0.2.2/LICENSE) · [websocket-client](THIRD_PARTY_LICENSES/python/websocket-client-1.9.2/LICENSE) · [frozenlist](THIRD_PARTY_LICENSES/python/frozenlist-1.8.0/LICENSE) · [google-crc32c](THIRD_PARTY_LICENSES/python/google-crc32c-1.8.0/LICENSE) · [overrides](THIRD_PARTY_LICENSES/python/overrides-7.7.0/LICENSE) |

### 1.1 dist 内没有许可文件、取证自上游的两项

以下两个包装好后**不带任何许可文件**，所以文本取自它们的**上游官方仓库**：

| 组件 / 组件版本 | 许可 | 上游 LICENSE | 文本 |
|---|---|---|---|
| ctranslate2 4.8.2 | MIT | <https://github.com/OpenNMT/CTranslate2> | [LICENSE](THIRD_PARTY_LICENSES/upstream/MIT_OpenNMT-CTranslate2_LICENSE.txt) |
| rapidocr-onnxruntime 1.2.3 | Apache-2.0 | <https://github.com/RapidAI/RapidOCR> | [LICENSE](THIRD_PARTY_LICENSES/upstream/Apache-2.0_RapidAI-RapidOCR_LICENSE.txt) |

> 口径说明：这两项在**本机 wheel 内没有文件级证据**，本清单按上游官方 LICENSE 登记，
> 并在此明确「证据来源是上游仓库而非已安装包」，不把机器读数写成已核。

---

## 二、随源码分发的 Flutter / Dart 与原生组件 / Flutter, Dart and native

| 组件 | 许可 | 说明 |
|---|---|---|
| Flutter engine + Dart VM | BSD-3-Clause | 由 Flutter SDK 带入，随 APK 分发 |
| Material Icons / Roboto | Apache-2.0 | `uses-material-design: true` |
| AndroidX、Firebase | Apache-2.0 | 由各插件依赖引入，随 APK 分发 |
| Shizuku API 13.1.5（`dev.rikka.shizuku:api` / `:provider`） | MIT，Copyright (c) 2021 RikkaW | Android 侧依赖；见 <https://github.com/RikkaApps/Shizuku-API>。⚠ Shizuku **App 本体**为 Apache-2.0 且明文禁止复用其图标／应用名／applicationId —— 本项目未复用 |
| dbus 0.7.15 / geoclue 0.1.1 / gsettings 0.2.8 | MPL-2.0 | **均为 Linux 桌面端传递依赖，不进 Android APK**，但进 `pubspec.lock` 与 Linux 检出树 |

其余直接依赖的机器读数为 BSD-3 / MIT / Apache-2.0，**Android 打包路径无 copyleft**。
本清单未逐个打开每个 Dart 包的 LICENSE 文件人工判读，因此这里不宣称 Dart 侧已穷尽。

The machine-read classification for the remaining direct dependencies is
BSD-3 / MIT / Apache-2.0, with no copyleft on the Android packaging path. Not
every Dart package's LICENSE file has been opened and read individually, so this
document does not claim the Dart side is exhaustive.

---

## 三、随源码分发的模型权重 / Model weights redistributed in the source archive

发布包（`scripts/make_release.py` 的 `bundle_models`）会把 `backend/models/` 下
两个权重目录**再分发**进 zip。这两套权重**不被本项目的 AGPL 覆盖**，按上游
MIT 授权：

| 权重 | 上游 | 许可 | 文本 |
|---|---|---|---|
| bge-m3 | <https://huggingface.co/Xenova/bge-m3> · <https://huggingface.co/BAAI/bge-m3> | MIT | [LICENSE](THIRD_PARTY_LICENSES/models/MIT_FlagOpen-FlagEmbedding_LICENSE.txt) |
| faster-whisper-small | <https://huggingface.co/Systran/faster-whisper-small>（转换自 <https://github.com/openai/whisper>） | MIT | [LICENSE](THIRD_PARTY_LICENSES/models/MIT_openai-whisper_LICENSE.txt) |

- 下载来源的脚本级证据：`scripts/download_models.py` 指向
  <https://huggingface.co/Xenova/bge-m3>（含 hf-mirror 回退）；whisper-small 的唯一
  来源痕迹是本机 HuggingFace 缓存目录名。
- **权重目录内本身没有许可文件**（本项目从未把许可文本拷进权重目录），所以义务由
  本清单 + `THIRD_PARTY_LICENSES/models/` 承接。发布时这两份许可随 zip 一同分发。

---

## 四、随源码分发的素材与美术 / Artwork and assets

| 素材 | 授权 | 说明 |
|---|---|---|
| `flutter_app/assets/life_visual/`（35 张）、`flutter_app/assets/pet_pixel_art/`（12 张） | 本项目 AGPL-3.0-only | 作者为本项目创作／自行生成的像素素材 |
| App 图标、mipmap、web icons、iOS/macOS 图标 | 本项目 AGPL-3.0-only | 同上 |
| `server_controller/assets/icons_src/*.svg` + `icons/*.png`（23 枚） | **ISC**（Lucide）＋ MIT（Feather 衍生图标） | **保留原文件不改**：`server_controller/assets/LICENSE-lucide.txt` 的版权行与上游当前原文一致；来源与复现方式见 `server_controller/assets/README.md` |
| `server_controller/assets/photo/*.jpg`（8 张） | 作者自有 | 作者自行运行生图模型产出的自有素材；原始文件在仓库外 |
| `backend/data/uploads/pets/{cat,dog,hamster,parrot,rabbit}.png`（5 张） | 本项目 AGPL-3.0-only | `scripts/generate_pet_sprites.py` 纯程序化绘制；git 已跟踪 ⇒ 进发布包 |
| 字体 / 音频 / 3D 模型 | — | git 跟踪文件中 **0** 命中（ttf/otf/mp3/wav/ogg/glb/fbx） |
| `reward-qrcode.png` | **不在开源授权范围内** | 作者个人赞赏码；随包分发仅供致谢，不授予任何人任何权利 |

### 4.1 已知缺口（如实登记，不按通过计）

| 缺口 | 状态 |
|---|---|
| `backend/data/uploads/pets_assets/`（42 个第三方素材文件） | ⚠ **来源与授权未记录**。该目录**未纳入 git**，不进源码发布包；但每日备份 zip 会带上它 |
| 贡献者署名要求 | 本仓**无任何 CLa / 版权转让协议**记录，也无 vendored 第三方源码。若有贡献者要求署名，需另行处理 |
| Dart 侧逐包人工判读 | 未做（见第二节末） |

---

## 五、不随包分发但需知悉 / Not redistributed

- **外部模型与 API 服务**（LLM / 视觉 / 语音 / 生图）：由使用者自行申请与付费，
  其条款以各服务官方说明为准，与本项目许可无关。**本项目的作者不承诺这些服务免费，
  也不参与它们的定价。**
- **用户数据**（聊天、记忆、日记、角色、配置、凭据）：不在开源授权范围内，见
  [NOTICE](NOTICE) 第 5 节。
- **第三方插件**：由其作者按各自许可授权；远程市场索引
  `plugins/marketplace/index.json` 当前为空数组。

---

## 六、复核方式 / How to re-verify

~~~powershell
# 逐字校验所有原许可文本与 MANIFEST 的 sha256（不需要 venv，CI 可跑）
backend\.venv\Scripts\python.exe scripts\collect_license_notices.py --verify

# 从本机已安装的 dist 重新逐字导出（需要 venv，不联网）
backend\.venv\Scripts\python.exe scripts\collect_license_notices.py --export

# 仅重取「dist 内无许可文件」的两项与模型上游许可（需要网络）
backend\.venv\Scripts\python.exe scripts\collect_license_notices.py --fetch-upstream
~~~

仓库内的契约守卫 `backend/tests/test_release_license_contract.py` 会钉住：
`LICENSE` 的 sha256、`NOTICE` 与 README 中英互链、两份 App 资产与根文件逐字节一致、
`make_release.py` / `Dockerfile` / 镜像 label 的许可包含性，以及"App 协议不得再出现
硬编码 MIT 或禁止商用措辞"。
