# Linux 蓝牙 GSM ↔ SIP 网关

本项目通过 Docker Compose 在 Linux 主机上运行 BlueZ、oFono、BlueALSA、Baresip、Asterisk 和 Web 管理服务，将手机 GSM 通话桥接到 SIP 软电话。iPhone 和 Android 通过真实的 HFP HF 服务连接，双向通话音频通过 ALSA Loopback 对接 SIP。

用户只需操作 Docker Compose。容器会自动释放宿主机对蓝牙适配器的占用，不需要预先执行 `systemctl stop bluetooth`、手工配对或填写 MAC 地址。

## 功能

- 启动时自动停止并屏蔽宿主机 BlueZ，加载蓝牙内核模块并接管 HCI 适配器。
- 停止容器时默认恢复宿主机蓝牙服务。
- Web 页面扫描、配对、信任、连接和解除配对。
- 自动读取适配器和手机 MAC，由 oFono 管理 HFP 连接及通话，不需要手动填写 RFCOMM 通道。
- BlueALSA 处理 SCO 音频，Baresip 通过 ALSA Loopback 与 Asterisk 的本地 SIP 对端互通。
- Web 页面配置 SIP 软电话账号和密码。
- 手机来电和 Linphone 去电在接通后自动录音到 Compose 根目录 `./Recordings`。
- 一键修复宿主机占用、HCI 状态和容器内的 BlueZ、oFono、BlueALSA。
- 蓝牙断开后自动尝试重新连接；关键进程退出时由 Compose 重启。

## 要求

- 使用 systemd 的 Linux 主机或开发板。
- 原生 Docker Engine 和 Docker Compose 插件。
- 可用的经典蓝牙适配器，手机支持 HFP。
- 宿主机内核提供 `snd-aloop` 模块；Compose 通过只读 `/lib/modules` 挂载按需加载。
- 必须使用 rootful Docker；Docker Desktop、Rootless Docker、Snap/Flatpak 沙箱版本不支持所需的宿主机命名空间访问。

## 启动

本地构建并启动：

```bash
docker compose up -d --build
```

浏览器访问：

```text
http://<开发板IP>:8080
```

进入页面后：

1. 确认“蓝牙已交接”和“适配器已开启”。
2. 若状态异常，点击“一键修复蓝牙环境”。
3. 点击“扫描 10 秒”，找到手机后点击“配对”。
4. 在手机端确认配对请求。
5. 页面会自动保存 MAC，并通过 oFono 建立 HFP 连接。
6. 在“SIP 软电话”中设置分机账号和密码。

不需要使用特殊启动脚本；即使忘记操作方式并直接执行 `docker compose up -d`，入口脚本也会运行相同的自动交接与修复流程。

## GHCR 镜像

仓库包含 `.github/workflows/publish-ghcr.yml`，GitHub Actions 仅构建 `linux/amd64`：

- 推送到 `master`：发布 `latest` 和 `sha-<提交>` 标签；
- 推送 `v*` Git 标签：发布对应版本标签；
- Pull Request：只验证镜像构建，不推送；
- 可在 Actions 页面手动触发构建。

镜像地址自动使用小写形式：

```text
ghcr.io/<仓库所有者>/<仓库名>:latest
```

使用 GHCR 镜像时，通过当前 Shell 环境变量设置实际地址：

```bash
export GATEWAY_IMAGE=ghcr.io/<仓库所有者>/<仓库名>:latest
```

然后执行：

```bash
docker compose pull
docker compose up -d
```

工作流使用仓库自带的 `GITHUB_TOKEN` 和 `packages: write` 权限，不需要额外创建 GHCR 密钥。首次发布后可在 GitHub Packages 中调整镜像可见性。

## 可选环境变量

项目不使用环境变量文件，也没有 `env_file`。需要修改默认值时，在执行 Docker Compose 前通过当前 Shell 的 `export` 或命令前缀传入。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `GATEWAY_IMAGE` | `bluetooth-gsm-sip-gateway:local` | 本地镜像名或 GHCR 完整镜像地址 |
| `GATEWAY_WEB_HOST` | `0.0.0.0` | Web 管理服务监听地址 |
| `GATEWAY_WEB_PORT` | `8080` | Web 管理服务端口 |
| `GATEWAY_ADMIN_TOKEN` | 空 | 设置后保护所有修改接口，浏览器页面中需填写相同令牌 |
| `GATEWAY_RESTORE_HOST_ON_STOP` | `1` | 容器正常停止时恢复宿主机蓝牙；设为 `0` 保持交接状态 |

例如：

```bash
export GATEWAY_IMAGE=ghcr.io/<仓库所有者>/<仓库名>:latest
export GATEWAY_WEB_HOST=0.0.0.0
export GATEWAY_WEB_PORT=8080
export GATEWAY_ADMIN_TOKEN='请替换为随机管理令牌'
export GATEWAY_RESTORE_HOST_ON_STOP=1
docker compose up -d
```

也可以只对单次命令生效：

```bash
GATEWAY_WEB_PORT=8081 GATEWAY_ADMIN_TOKEN='请替换为随机管理令牌' docker compose up -d
```

## 蓝牙控制权

容器已有 `privileged: true` 和 `pid: host`，管理脚本通过宿主机 PID 1 的命名空间调用宿主机 `systemctl`，操作范围限制为：

- `bluetooth.service` 与 `dbus-org.bluez.service`；
- `bluetooth`、`btusb`、`rfcomm`、`snd-aloop` 内核模块；
- HCI 蓝牙设备。

首次接管时，宿主机蓝牙服务原始状态会保存到 Docker 卷 `gateway-state`。正常停止或点击“归还蓝牙给宿主机”时，会按记录恢复服务状态。强制终止可能绕过清理流程；这种情况下应重新正常启动并停止容器，或手动恢复宿主机蓝牙服务。

## 配置与持久化

- Docker Compose 配置：`docker-compose.yml`
- 容器镜像配置：`Dockerfile`
- Web 与管理后端：`app/`
- 容器入口与蓝牙控制脚本：`scripts/`
- Asterisk 基础配置：`config/asterisk/`
- 手机、SIP 和 Baresip 运行配置：Docker 卷 `gateway-state`
- BlueZ 配对记录：宿主机 `/var/lib/bluetooth`
- 双向通话录音：Compose 根目录 `./Recordings`

运行时生成的密码、手机 MAC 和配对记录不会提交到 Git。

## 常用命令

```bash
docker compose logs -f
docker compose restart
docker compose down
```

查看 Asterisk 状态：

```bash
docker exec asterisk-mobile asterisk -rx "sip show peers"
docker exec asterisk-mobile asterisk -rx "dialplan show from-hfp"
```

## 安全说明

该容器需要控制物理蓝牙硬件和加载 ALSA Loopback，因此拥有较高宿主机权限。建议仅在专用设备或可信主机运行，在局域网可访问管理页面时设置 `GATEWAY_ADMIN_TOKEN`，并修改 SIP 分机默认密码。

镜像基于 Ubuntu 22.04 和 Asterisk 18；本地分机暂时沿用 `chan_sip`。不要将该容器作为通用服务部署到不可信环境。
