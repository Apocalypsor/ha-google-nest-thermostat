# Google Nest 温控器（轮询）

与 [STRATIS AC](https://github.com/Apocalypsor/ha-stratis-ac) 一样使用 Home Assistant `DataUpdateCoordinator` 共享轮询的独立 custom component。

**直接调用 Google 官方 SDM REST API，不需要 Pub/Sub 主题、订阅、发布权限或 Pub/Sub 客户端，也不依赖 HA 官方 Nest 集成。** 仍需 Google 云端连接，不能在本地局域网脱离 Google 运行。

## 功能

- 原生温控实体：制热、制冷、自动冷热、关闭、目标温度、工作状态、湿度。
- 支持设备提供的 Eco 预设、风扇定时控制；风扇“开启”默认运行 15 分钟，“自动”取消手动定时，仍允许暖通系统自动控制风扇。
- 独立温度、湿度和风扇定时结束传感器。
- 默认 **30 秒**拉取一次，全体实体共享一次设备列表请求；可配置 30–900 秒。在 Nest 或 Google Home 中的修改会在下一次成功读取后更新，Google 云端本身也可能有延迟。
- 自动刷新 OAuth，保存旋转后的刷新令牌；401 只刷新并重试一次；授权失效提示重新验证；限流及网络错误自动退避。
- 控制前读取实际设备状态，控制后回读确认，界面不把请求值直接当作已生效状态。
- 设备离线、取消共享或读取失败时显示不可用；后续轮询能发现新共享的温控器。
- 英文、简体中文配置和错误提示；诊断数据排除凭据、账号、住宅名称和设备标识。

## 安装与配置

需要 **Home Assistant 2026.10.4 或更新版本、Python 3.14.2 或更新版本**，以及 Google Cloud 的 SDM API、Web OAuth 客户端、Device Access 项目和对应刷新令牌。Google Device Access 注册及费用要求仍然适用。Device Access 中的事件功能可以保持关闭。

1. 把 `custom_components/google_nest_thermostat` 复制到 HA 的 `/config/custom_components/google_nest_thermostat`。
2. 重启 HA，在“设置 → 设备与服务 → 添加集成”选择 **Google Nest Thermostat (Polling)**。
3. 输入 Device Access 项目 UUID、OAuth 客户端 ID、客户端密钥和刷新令牌。
4. 在“配置”中调整刷新间隔和手动风扇时长。

仓库发布后也可在 HACS／Marketplace 中将仓库添加为自定义 Integration。组件本身没有额外 Python 依赖。

已有官方 Nest 集成的有效 SDM 刷新令牌可以复用，必须配套使用原客户端 ID 和密钥。请勿把密钥或令牌发到聊天、工单或 Git 仓库。

## 本机授权助手

1. 在 Google Cloud 的 Web OAuth 客户端中添加回调 URI：`http://localhost:8765/callback`。将该客户端绑定到 Device Access 项目，下载客户端 JSON，保存为 `.local/client_secret.json`。
2. 在浏览器所在电脑运行：

   ```sh
   python3 scripts/authorize.py \
     --client-secrets .local/client_secret.json \
     --project-id 你的_DEVICE_ACCESS_UUID
   ```

3. 浏览器选择 Google 账号，允许住宅信息并勾选温控器。助手只申请 `sdm.service` 权限。
4. 助手读取设备验证授权后，把配置字段保存到仅当前用户可读写的 `.local/nest_credentials.json`。在本机查看该文件，将四项字段填入 HA。

助手仅监听本机回环地址，校验 OAuth state，不在终端输出令牌／授权码，且不覆盖已有凭据文件。浏览器必须能访问运行助手电脑的 localhost；不建议直接在远程 NAS 上运行。更改 `--port` 时需注册对应回调 URI。

Google 的 OAuth 测试模式、撤销授权等仍可能导致令牌过期。出现重新认证提示时，用原账号重新授权并填写新刷新令牌；可按需提供新的客户端密钥。

## 行为和限制

- 所有 SDM 温度读取和命令均为 **摄氏度**，HA 会按其单位设置转换华氏显示及服务输入；与温控器自己的显示单位无关。
- `HEATCOOL` 映射到 HA 的 `heat_cool`，冷热区间至少相差 1.5°C。
- OFF 时单独设置温度会被拒绝，只有明确指定制热／制冷模式的操作才可能开启系统。
- Eco 时请先退出 Eco 再设置温度；明确选择正常 HVAC 模式时会退出手动 Eco。
- Google 限制每个项目／用户每分钟 5 次设备列表读取、10 次单设备读取。默认轮询占用每分钟 2 次列表请求。连续控制可能触发独立命令配额，组件会明确报错并延迟重试。
- 不支持摄像头／门铃事件、SDM 未提供的附加室内传感器、远程固件更新或本地 LAN 控制。

## 从 HA 官方 Nest 迁移

新组件域名为 `google_nest_thermostat`，不会覆盖官方 `nest` 源码或配置。确认新组件可用后，可停用旧温控器集成，避免重复控制或自动化冲突；如仍需原集成的摄像头功能，请注意自动化所使用的温控实体。

**实体 ID 和历史不会自动迁移。** 若要保留原自动化引用和历史关联，先备份 HA，再在实体设置中释放旧实体 ID，将同一 ID 分配给新实体。组件不会自动删除旧 Pub/Sub 资源，确定没有其他消费者后再手动清理。

开发与测试命令见 [英文说明](README.md#development)。MIT 许可证；独立项目，与 Google 无隶属关系。
