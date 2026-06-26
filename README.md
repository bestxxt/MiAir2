# MiAir2 - 为小爱音箱添加 DLNA 与 AirPlay 支持 (扫码登录版)

> 本项目基于 [KiriChen-Wind/MiAir](https://github.com/KiriChen-Wind/MiAir) 二次开发。
> **主要新特性**：全面移除明文账号密码登录，新增 **安全可靠的扫码登录机制**，大幅降低账号安全隐患与异地登录封号风险，同时使用原生 `docker-compose` 简化了部署流程。

## 引用以下开源项目代码 由衷感谢

**[XiaoMusic](https://github.com/hanxi/xiaomusic "XiaoMusic")** &ensp; **[AirPlay2 Receiver](https://github.com/openairplay/airplay2-receiver "AirPlay2 Receiver")** &ensp; **[MaCast](https://github.com/xfangfang/Macast "MaCast")**

## 快速开始

### 本地直接运行 (Windows / macOS / Linux)
*确保设备已安装 Python 3.10+*

1. 克隆或下载本项目到本地。
2. 进入项目目录，使用终端执行：
```bash
python miair.py
```
*(程序将自动安装相关依赖库，请确保网络畅通)*
3. 安装完成后，在浏览器访问 `http://<主机IP>:8300` 打开 Web 管理界面，使用小米 App 扫码登录并选择您的音箱即可。


### Docker Compose 部署 (推荐)

支持平台：Linux / OpenWrt / macOS / NAS

1. 确保已安装 [Docker](https://docs.docker.com/engine/install/) 和 [Docker Compose](https://docs.docker.com/compose/install/)
2. 在项目根目录下执行以下命令一键启动：
```bash
docker compose up -d --build
```
3. 部署完成后，访问 `http://<容器宿主机IP>:8300` 打开 Web 管理界面。

*注意：Docker 容器默认使用 `network_mode: "host"` 以确保 DLNA 和 AirPlay 的发现广播能正常工作。*

### Docker 常用运维命令
```bash
docker compose logs -f miair     # 查看实时日志
docker compose stop miair        # 停止服务
docker compose start miair       # 启动服务
docker compose restart miair     # 重启服务
```

## 更新日志 & 功能特性

- ✅ **新增扫码登录**：移除配置文件中的明文密码，通过小米原生网页长轮询接口安全登录。
- ✅ **支持 Docker 部署**：提供标准的 Dockerfile 和 docker-compose.yml 部署方案。
- ✅ **优化 DLNA/AirPlay**：稳定获取设备列表及播放控制。
- ⏳ 支持 OpenWrt 部署

## 交流反馈
如果您遇到问题，欢迎提交 Issue，或者参考原项目讨论群：
**[需要帮助&交流&测试版本发布](https://qun.qq.com/universal-share/share?ac=1&authKey=1zXhx2zxgw9GG2mkecypT9clD7q0B3W3l4K0D4fQirmpDWakz0Oy2BI3ocDrgzbh&busi_data=eyJncm91cENvZGUiOiI3NDEyNjcyOTgiLCJ0b2tlbiI6InYwbitXQTF5cE9MaUJCR0hMUk03OWV0WkFoMThxbjJRaWI4dHVlbUpGdW5OdEZBVEpXMXF0T1dQUnRmRXRzYVgiLCJ1aW4iOiIxODQxOTM4MDQwIn0%3D&data=_OrA-eASJMwYwx-Uj-BReC1Xh3zGAdkn8CQskbEsQ5S66bhqvvO6dJ-QrSlRl-Ks00l5XDw1FANE8Um0w5yB8Q&svctype=4&tempid=h5_group_info "需要帮助&交流&测试版本发布")**
