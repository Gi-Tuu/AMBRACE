import 'package:flutter/material.dart';

/// 用户协议与免责声明：开源自托管软件的使用边界（重点为作者免责）。
class UserAgreementScreen extends StatelessWidget {
  const UserAgreementScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('用户协议与免责声明')),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: const [
          _Section(
            title: '一、软件性质',
            body: '本项目为开源、自托管软件（MIT License），由使用者自行下载、部署并运行在自己的设备或服务器上。作者以个人身份无偿维护本开源项目，不对任何使用者提供商业化服务承诺。',
          ),
          _Section(
            title: '二、自担风险',
            body: '软件按「现状」（AS IS）提供，不附带任何明示或默示的担保，包括但不限于适销性、特定用途适用性。因部署、配置、使用、升级过程中出现的任何数据丢失、损坏、服务中断或财产损失，均由使用者自行承担。',
          ),
          _Section(
            title: '三、内容责任',
            body: '本软件为通用工具，AI 生成的对话、图片、文字等内容由使用者自行配置的模型、提示词与数据产生，不代表作者观点。作者不对使用者或任何第三方基于本软件产生、传播的内容与行为承担任何责任。',
          ),
          _Section(
            title: '四、数据安全',
            body: '数据默认存储在使用者自己的服务器。请自行做好备份、密钥保管与访问控制（如防火墙、HTTPS、修改默认管理员账号）。因未妥善保护导致的隐私泄露、数据被篡改等后果，由使用者自行负责。',
          ),
          _Section(
            title: '五、合法使用',
            body: '使用者须遵守所在地法律法规，不得将本软件用于违法、侵权、骚扰、诈骗等用途；不得利用软件生成的内容侵犯他人合法权益。使用者的一切使用行为及其后果均与作者无关。',
          ),
          _Section(
            title: '六、远程与多人访问',
            body: '将服务器暴露到公网、通过 Tailscale 等组网分享给他人使用前，使用者须自行评估风险并承担相应责任，包括但不限于他人通过账号或权限管理不当产生的后果。',
          ),
          _Section(
            title: '七、协议变更',
            body: '本协议内容可能随版本更新调整，继续使用本软件即视为接受最新版本协议。',
          ),
        ],
      ),
    );
  }
}

class _Section extends StatelessWidget {
  const _Section({required this.title, required this.body});

  final String title;
  final String body;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 15)),
          const SizedBox(height: 6),
          Text(body, style: const TextStyle(fontSize: 14, height: 1.5, color: Colors.black87)),
        ],
      ),
    );
  }
}
