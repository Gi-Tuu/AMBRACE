# AMBRACE 插件市场（远程）安全须知

> 本目录存放远程插件市场的 `index.json` 索引；用户通过 AMBRACE 扩展页的「市场」在此一键安装插件。

## ⚠️ 安全提示（插件 = 在服务器上运行的代码）

- **插件与 AMBRACE 后端同权限运行（无沙箱）**。安装即授权该插件在你的服务器上执行代码。
  请只发布/安装**可信来源**的插件。
- **远程市场安装默认关闭**：服务端默认关闭远程市场安装（`PLUGIN_ALLOW_REMOTE_INSTALL`）。
  仅管理员在 `.env` 显式开启后，客户端才可远程安装；本地/内置示例插件不受此限制。
- **安装即授权，权限需逐条确认**：manifest 的 `permissions`（如 `write_memory` / `send_message` /
  `douyin_publish` 及只读组 `persona:read` / `memory:read` / `life:read` / `relationship:read`）
  会在安装/升级前要求用户显式同意；升级若**新增**权限需重新同意。
- **来源与哈希可追溯**：已安装插件记录来源、来源 url 与 `sha256` 实际值，便于校验包是否被篡改。

## 发布约定

- 发布插件的 `manifest.json` 中 `name` 全局唯一、`version` 语义化，避免与已发布插件冲突。
- `index.json` 中的 `download_url` 使用 HTTPS；每个条目附带 `size` 与 `sha256`。
- 权限尽量最小化：只声明实际会用到的权限，便于用户信任。
