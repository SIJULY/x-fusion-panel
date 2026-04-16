# X-Fusion Panel

X-Fusion Panel 是一个面向 **X-UI / VPS / 节点运维场景** 的可视化管理面板，基于 **NiceGUI + FastAPI + Paramiko** 构建。

## 项目亮点

- **多服务器聚合管理**：统一查看服务器、节点、协议、端口、流量与在线状态
- **单机详情视图**：集中呈现单台服务器的系统信息、节点信息、探针状态和常用操作
- **内置 WebSSH + 文件管理**：支持终端连接、目录浏览、文件编辑、上传下载、重命名、删除与权限修改
- **探针体系完整**：支持探针安装、主动注册、状态推送、缓存与 Ping 趋势记录
- **订阅能力完整**：支持原始订阅、分组订阅、短链转换，并可对接 subconverter
- **一键部署辅助**：支持 XHTTP、Hysteria、Snell 等部署操作
- **公共状态页**：可对外展示服务器状态与基础信息
- **支持 Docker / Compose / install.sh 部署**
- **代码已完成模块化拆分**：页面、路由、服务、存储、工具层职责清晰

---

## 当前项目结构

```text README.md
project/
├─ app/                                  # 当前正式运行的主应用目录
│  ├─ __init__.py
│  ├─ main.py                            # 当前运行入口（正式入口）
│  ├─ requirements.txt                   # app 运行依赖
│  │
│  ├─ api/                               # 路由/接口桥接层
│  │  ├─ __init__.py
│  │  ├─ auth.py                         # 登录页、主页路由注册
│  │  ├─ probe.py                        # probe 相关接口桥接
│  │  ├─ status.py                       # /status 页面路由注册
│  │  ├─ subscriptions.py                # 订阅接口与转换接口
│  │  └─ notifications.py                # Telegram 通知发送
│  │
│  ├─ core/                              # 全局配置 / 状态 / 日志等核心层
│  │  ├─ __init__.py
│  │  ├─ config.py                       # 配置常量、路径、脚本模板
│  │  ├─ state.py                        # 全局缓存与 UI 状态容器
│  │  ├─ logging.py                      # logger / 线程池 / scheduler
│  │  └─ security.py                     # 安全相关预留模块
│  │
│  ├─ services/                          # 业务服务层
│  │  ├─ __init__.py
│  │  ├─ ssh.py                          # Paramiko SSH / WebSSH / 远程执行
│  │  ├─ sftp.py                         # 远程文件管理 / 上传下载 / 权限处理
│  │  ├─ probe.py                        # probe/agent 安装、推送、注册、状态
│  │  ├─ xui_api.py                      # X-UI HTTP API 管理器
│  │  ├─ xui_ssh.py                      # X-UI SSH/SQLite 管理器
│  │  ├─ manager_factory.py              # 选择 API 管理器或 SSH 管理器
│  │  ├─ xui_fetch.py                    # 节点/入站安全拉取
│  │  ├─ dashboard.py                    # 仪表盘统计与实时数据
│  │  ├─ server_ops.py                   # 服务器命名、刷新、分组、保存等
│  │  ├─ subscriptions.py                # 订阅相关服务逻辑
│  │  ├─ deployment.py                   # XHTTP/Hysteria/Snell 远程部署
│  │  └─ cloudflare.py                   # Cloudflare 自动解析/配置
│  │
│  ├─ jobs/                              # 启动任务 / 定时任务
│  │  ├─ __init__.py
│  │  ├─ startup.py                      # 启动时初始化后台任务
│  │  ├─ monitor.py                      # 服务器在线状态监控
│  │  ├─ traffic.py                      # 流量/节点周期同步
│  │  └─ geoip.py                        # GeoIP 命名修正任务
│  │
│  ├─ storage/                           # 数据文件读写层
│  │  ├─ __init__.py
│  │  ├─ files.py                        # 底层安全写文件
│  │  ├─ repositories.py                 # 保存/读取仓储封装
│  │  └─ bootstrap.py                    # 启动时加载 data/*
│  │
│  ├─ utils/                             # 工具层
│  │  ├─ __init__.py
│  │  ├─ geo.py                          # IP 地理位置 / 国旗 / 区域识别
│  │  ├─ network.py                      # ping / DNS / origin / 网络辅助
│  │  ├─ encoding.py                     # base64 / 节点链接生成与解析
│  │  ├─ formatters.py                   # 格式化/排序工具
│  │  └─ async_tools.py                  # 后台执行器包装
│  │
│  └─ ui/                                # UI 层
│     ├─ __init__.py
│     ├─ common/                         # UI 公共能力
│     │  ├─ __init__.py
│     │  ├─ notifications.py             # 通知、复制等 UI 公共反馈
│     │  ├─ dialogs_data.py              # 数据/全局设置弹窗
│     │  └─ dialogs_settings.py          # Cloudflare/设置类弹窗
│     │
│     ├─ components/                     # 页面组件
│     │  ├─ __init__.py
│     │  ├─ sidebar.py                   # 左侧导航/分组/服务器树
│     │  ├─ dashboard.py                 # 仪表盘 UI 组件
│     │  ├─ server_rows.py               # 服务器行渲染
│     │  └─ status_cards.py              # 状态卡片渲染
│     │
│     ├─ dialogs/                        # 各类对话框/操作弹窗
│     │  ├─ __init__.py
│     │  ├─ server_dialog.py             # 服务器新增/编辑主弹窗（兼容入口保留）
│     │  ├─ inbound_dialog.py            # 节点(inbound)编辑弹窗
│     │  ├─ sub_dialogs.py               # 订阅编辑弹窗
│     │  ├─ group_dialogs.py             # 分组管理/排序/批量管理弹窗
│     │  ├─ bulk_edit.py                 # 批量编辑
│     │  ├─ batch_ssh.py                 # 批量 SSH 执行
│     │  ├─ ssh_console.py               # WebSSH 控制台弹窗
│     │  ├─ deploy_xhttp.py              # XHTTP 部署弹窗
│     │  ├─ deploy_hysteria.py           # Hysteria 部署弹窗
│     │  └─ deploy_snell.py              # Snell 部署弹窗
│     │
│     └─ pages/                          # 页面级逻辑
│        ├─ __init__.py
│        ├─ login_page.py                # 登录页 + MFA + 会话校验
│        ├─ main_page.py                 # 主页面壳子
│        ├─ content_router.py            # 主内容区路由/切换
│        ├─ probe_page.py                # probe 设置页
│        ├─ subs_page.py                 # 订阅管理页
│        ├─ aggregated_view.py           # 聚合列表页
│        ├─ single_server.py             # 单服务器详情页
│        ├─ single_ssh.py                # 单服务器 SSH / 文件管理页
│        └─ public_status.py             # 公开监控墙/状态页
│
├─ static/                               # 前端静态资源
│  ├─ xterm.js                           # WebSSH 终端库
│  ├─ xterm.css
│  ├─ xterm-addon-fit.js
│  ├─ echarts-gl.min.js                  # 图表/地球视图相关
│  └─ world.json                         # 世界地图数据
│
├─ data/                                 # 运行时数据目录
│  └─ .gitkeep                           # 默认占位，实际运行时会生成 JSON/密钥文件
│
├─ .github/
│  └─ workflows/
│     └─ docker-image.yml                # GitHub Actions 自动构建镜像
│
├─ Dockerfile                            # 镜像构建定义
├─ docker-compose.yml                    # 本地/源码模式 compose 启动
├─ install.sh                            # VPS 一键安装/更新/卸载脚本
├─ README.md                             # 项目说明文档
├─ requirements.txt                      # 根目录依赖文件
├─ main.py                               # 原始单文件版本（历史参考）
├─ main_index.json                       # 原始 main.py 索引
├─ main_slices/                          # 原始 main.py 切片辅助目录
├─ migration_manifest.md                 # 拆分职责清单
├─ migration_state.md                    # 迁移状态记录
├─ slice_main.py                         # main.py 切片辅助脚本
└─ build_index.py                        # main 索引构建脚本
```

---

## 运行方式

### 本地运行

```bash README.md
python3 -m venv .venv
source .venv/bin/activate
pip install -r app/requirements.txt
python app/main.py
```

默认监听：
- `0.0.0.0:8080`

### Docker Compose

```bash README.md
docker compose up -d --build
```

默认映射：
- 面板：`8081 -> 8080`
- 数据卷：`./data:/app/data`

### install.sh 一键安装

```bash README.md
bash <(curl -Ls https://raw.githubusercontent.com/SIJULY/x-fusion-panel-pro/main/install.sh)
```

或：

```bash README.md
wget -O install.sh https://raw.githubusercontent.com/SIJULY/x-fusion-panel-pro/main/install.sh
chmod +x install.sh
./install.sh
```

---

## 运行数据与静态资源

### `data/`
运行时数据目录。

本地开发默认使用：
- `./data`

Docker 运行默认使用：
- `/app/data`

实际运行时通常会生成或使用：
- `servers.json`
- `subscriptions.json`
- `admin_config.json`
- `nodes_cache.json`
- `global_ssh_key`

### `static/`
前端静态资源目录，主要用于：
- WebSSH 终端
- 图表与地图展示

---

## 默认登录信息

默认账号密码来自环境变量，未设置时默认：

- 用户名：`admin`
- 密码：`admin`

相关环境变量：
- `XUI_USERNAME`
- `XUI_PASSWORD`
- `XUI_SECRET_KEY`

---

## 安全提示

- 首次部署后请立即修改默认账号密码
- 请修改自动注册密钥 `XUI_SECRET_KEY`
- 不要把真实 `data/` 目录内容提交到仓库
- 不要提交真实服务器密码、SSH 私钥、TG Token、Cloudflare Token
- 生产环境建议使用 HTTPS 或反向代理
- 请定期备份 `data/`

---

## 说明

- 当前正式运行代码位于 `app/`
- 根目录 `main.py`、`main_slices/`、`main_index.json` 属于历史迁移参考产物
- 当前仓库已经完成从单文件结构向模块化结构的迁移

