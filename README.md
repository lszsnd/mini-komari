# Mini Komari

一个 Komari 风格的轻量 VPS 探针，核心流程是：**先搭建主控 Master，然后在主控网页里生成被控 Agent 安装命令，再拿去另一台 VPS 执行。**

单文件 Python 实现，无需 pip。

## 架构

```text
主控 Master Web 面板
  ├─ 展示所有节点状态
  ├─ 生成 Agent 安装命令
  ├─ GET  /api/nodes
  └─ POST /api/report  ◀── 被控 Agent 定时上报
```

## 功能

- Master 主控面板：集中展示多个节点
- 简洁白/银色 Web UI，适合移动端和桌面端
- Web 页面内置 Agent 安装命令生成器
- Agent 数据每 3 秒局部刷新，不会打断正在填写的安装命令表单
- 复制安装命令支持 Clipboard API 和兼容 fallback
- Agent 被控端：定时采集 CPU、内存、磁盘、网络并上报
- 节点卡片显示运行压力状态，不在公开展示页暴露公网 IP
- HMAC Token 签名校验，防止乱上报；未指定时自动生成随机 Token
- Agent 命令生成器默认隐藏 Token，可手动显示/隐藏
- Agent 上报数据会做字段校验和长度限制，避免异常数据污染面板
- systemd 常驻运行
- 主控网页登录保护：首次访问网页注册管理员账号，之后登录使用
- 节点数据持久化到 `/opt/mini-komari/nodes.json`，主控重启后自动恢复
- 顶部统计栏显示总节点、在线节点、离线节点、分组数量
- 支持 `update` 一键更新程序并保留已有配置/节点数据
- 安装完成后自动健康检查，失败时提示查看日志
- 支持 `curl | bash` 一键安装

## 文件

```text
mini_komari.py   # 主程序，支持 master/agent/standalone
install.sh       # 一键安装脚本
uninstall.sh     # 卸载脚本
README.md
LICENSE
.gitignore
```

## 推荐使用流程

### 1. 上传项目到 GitHub

例如仓库：

```text
https://github.com/lszsnd/mini-komari
```

### 2. 安装主控 Master

在主控服务器执行：

```bash
curl -fsSL https://raw.githubusercontent.com/lszsnd/mini-komari/main/install.sh | bash -s -- master 6060
```

参数：

```text
master 6060
│      └────────────────────────────────────── 主控面板端口
└───────────────────────────────────────────── 安装模式
```

不传 Token 时，安装脚本会自动生成随机上报密钥，并保存在 `/opt/mini-komari/TOKEN` 和 `/opt/mini-komari/mini-komari.env`。

安装脚本会自动识别主控公网 IP，并生成类似下面的面板地址：

```text
http://主控公网IP:6060/
```

如果自动识别不准，也可以手动指定主控公网 URL：

```bash
curl -fsSL https://raw.githubusercontent.com/lszsnd/mini-komari/main/install.sh | bash -s -- master 6060 "" http://你的域名或IP:6060
```

也可以手动指定 Token：

```bash
curl -fsSL https://raw.githubusercontent.com/lszsnd/mini-komari/main/install.sh | bash -s -- master 6060 强随机TOKEN http://你的域名或IP:6060
```

然后打开安装输出里的面板地址。首次访问会进入注册页面，创建管理员账号后即可登录。

安装脚本会自动检查本机：

```text
http://127.0.0.1:6060/health
```

如果健康检查失败，按提示查看：

```bash
journalctl -u mini-komari -f
```

### 3. 在主控网页生成 Agent 命令

打开网页后，你会看到：

```text
生成被控 Agent 安装命令
```

填写：

- 主控地址：一般自动填好，例如 `http://主控IP:6060`
- 节点名：例如 `hk-node-1`
- 分组：例如 `香港`
- Token：默认为空，点击“生成”创建强随机 Token，也可手动填写

点击复制安装命令。

Agent 数据会持续局部刷新，但不会替换正在填写的表单；复制成功后会提示该命令仅显示一次，并清空节点名、分组和 Token，避免密钥留在页面。

### 4. 去被控 VPS 执行复制出来的命令

类似：

```bash
curl -fsSL https://raw.githubusercontent.com/lszsnd/mini-komari/main/install.sh | bash -s -- agent http://主控IP:6060 自动生成的TOKEN hk-node-1 香港
```

几秒后，主控网页就会出现该节点。

## 如果仓库名不是 lszsnd/mini-komari

主控安装：

```bash
MINI_KOMARI_REPO=你的用户名/你的仓库 \
curl -fsSL https://raw.githubusercontent.com/你的用户名/你的仓库/main/install.sh | bash -s -- master 6060
```

Agent 安装命令也可以在主控网页里生成。

最稳的非管道方式：

```bash
mkdir -p /tmp/mini-komari && cd /tmp/mini-komari
curl -fsSLO https://raw.githubusercontent.com/你的用户名/你的仓库/main/install.sh
curl -fsSLO https://raw.githubusercontent.com/你的用户名/你的仓库/main/mini_komari.py
bash install.sh master 6060
```

## API

```text
GET  /                 Web 面板
GET  /api/nodes        节点列表 JSON
GET  /api/status       /api/nodes 兼容别名
GET  /health           健康检查
GET  /login            登录页面
GET  /register         首次注册管理员页面
GET  /logout           退出登录
POST /login            登录并创建会话
POST /register         首次创建管理员账号
POST /api/report       Agent 上报，无需网页登录，使用 HMAC Token 签名
POST /api/delete       删除节点，需要网页登录会话
```

## 管理命令

```bash
systemctl status mini-komari
journalctl -u mini-komari -f
systemctl restart mini-komari
systemctl stop mini-komari
```

## 更新

从 GitHub 拉取最新版程序并重启服务，保留已有 systemd 配置、网页登录密码和节点数据：

```bash
curl -fsSL https://raw.githubusercontent.com/lszsnd/mini-komari/main/install.sh | bash -s -- update
```

如果仓库名不是 `lszsnd/mini-komari`：

```bash
MINI_KOMARI_REPO=你的用户名/你的仓库 \
curl -fsSL https://raw.githubusercontent.com/你的用户名/你的仓库/main/install.sh | bash -s -- update
```

## 卸载

```bash
curl -fsSL https://raw.githubusercontent.com/lszsnd/mini-komari/main/uninstall.sh | bash
```

或本地：

```bash
bash uninstall.sh
```

## 本地调试

启动主控：

```bash
python3 mini_komari.py master \
  --host 127.0.0.1 \
  --port 6060 \
  --token 测试TOKEN \
  --public-url http://127.0.0.1:6060 \
  --raw-base https://raw.githubusercontent.com/lszsnd/mini-komari/main
```

Agent 上报一次：

```bash
python3 mini_komari.py agent \
  --master http://127.0.0.1:6060 \
  --token 测试TOKEN \
  --name local-test \
  --once
```

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MINI_KOMARI_REPO` | `lszsnd/mini-komari` | GitHub 仓库，格式 `用户名/仓库` |
| `MINI_KOMARI_REF` | `main` | 分支或 tag |
| `MINI_KOMARI_RAW_BASE` | 自动拼接 | 自定义 raw base URL |
| `MINI_KOMARI_PUBLIC_URL` | 自动识别公网 IP | 主控公网访问地址 |
| `MINI_KOMARI_TOKEN` | 自动生成 | 上报签名 token |
| `MINI_KOMARI_PORT` | `6060` | Master/Standalone 端口 |
| `MINI_KOMARI_INTERVAL` | `5` | Agent 上报间隔秒数 |
| `MINI_KOMARI_NODE_NAME` | hostname | Agent 节点名 |
| `MINI_KOMARI_DATA_FILE` | `/opt/mini-komari/nodes.json` | Master/Standalone 节点持久化文件 |
| `MINI_KOMARI_USER_FILE` | `/opt/mini-komari/user.json` | Web 管理员账号文件 |
