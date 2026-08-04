# BillManage 部署指南（Docker Compose + Tailscale）

推荐架构：**Docker Compose 部署应用，Tailscale 提供私网连接，Caddy 为自定义域名提供 HTTPS**。
`bill.getnet.store` 只解析到服务器的 Tailscale IP，不向公网提供服务，腾讯云安全组不需要开放任何公网端口。

## 一、你需要准备什么

1. 腾讯云服务器一台（建议 Ubuntu 22.04/24.04，2 核 2G 起，系统盘 40G+）；
2. 一个 Tailscale 账号（组内成员共享的同一账号/企业版）；
3. 本仓库代码（可通过 `git clone` 或 `scp` 上传到服务器）；
4. 自定义域名 `bill.getnet.store`，A 记录指向 `100.117.187.60`；
5. Cloudflare API Token，用于 Caddy 通过 DNS-01 自动申请和续期 HTTPS 证书。

## 二、服务器初始化

```bash
# 安装 Docker（官方脚本）
curl -fsSL https://get.docker.com | sh
sudo systemctl enable --now docker
sudo usermod -aG docker $USER

# 配置 Docker 国内镜像加速（拉镜像更快；腾讯云内网地址，仅腾讯云服务器可用）
sudo tee /etc/docker/daemon.json > /dev/null <<'EOF'
{
  "registry-mirrors": [
    "https://mirror.ccs.tencentyun.com",
    "https://docker.m.daocloud.io"
  ]
}
EOF
sudo systemctl restart docker

# 安装 Tailscale
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

`tailscale up` 会输出一条登录链接，在浏览器中确认并加入你的 tailnet。
确认组内其他设备能 ping 通本机：

```bash
tailscale ip -4   # 记下 100.x.x.x
```

## 三、部署应用

```bash
# 进入项目目录（含 Dockerfile / docker-compose.yml）
cd /opt/billmanage

# 准备数据目录（数据库、待确认、归档、报销单都在这里）
mkdir -p data

# 配置 HTTPS 证书所需的 Cloudflare API Token；不要把 .env 提交到 Git。
cp .env.example .env
nano .env

# 构建并启动
sudo docker compose up -d --build

# 检查状态
sudo docker compose ps
curl -s http://127.0.0.1:8000/ | head -n 5   # 应返回页面
```

Dockerfile / Compose 已针对国内构建做了以下优化：

- Python 基础镜像走 DaoCloud 代理；
- `uv` 二进制从 DaoCloud 代理的官方 uv 镜像复制，不再执行 `pip install uv`；
- apt 软件源使用腾讯云镜像 `mirrors.cloud.tencent.com`；
- Python 依赖及 lock 中的 wheel URL 均使用腾讯云 PyPI 镜像；
- uv 下载目录使用 BuildKit cache mount。即使 `uv.lock` 更新，也能复用已下载的 wheel；
- 仅业务代码发生变化时，依赖安装层会直接命中 Docker 缓存。
- Caddy 的 Cloudflare DNS 插件使用 `goproxy.cn` 下载，并使用独立的 `sum.golang.google.cn` 校验模块；

首次构建仍需下载基础镜像、uv 镜像、Tesseract 和 Python 依赖；从第二次构建开始通常会明显加快。
可用下面的命令查看每一层的真实耗时：

```bash
sudo docker compose build --progress plain
```

### 构建卡在依赖下载时的备用方法（Clash 代理）

如果 `uv sync` 下载 Python 依赖仍然很慢，可以让构建走服务器上已运行的 Clash：

```bash
# 容器内访问不到 127.0.0.1（那是容器自己），要使用宿主机网关地址。
GW=$(docker network inspect bridge -f '{{(index .IPAM.Config 0).Gateway}}')

sudo docker compose build \
  --build-arg HTTP_PROXY=http://$GW:7890 \
  --build-arg HTTPS_PROXY=http://$GW:7890 \
  --build-arg UV_DEFAULT_INDEX=https://pypi.org/simple \
  --build-arg UV_FILES_BASE=https://files.pythonhosted.org \
  --progress plain
sudo docker compose up -d
```

说明：

- 代理走境外节点时，可将 Python 依赖源临时切回官方 PyPI；
- 下载结果会保存在名为 `billmanage-uv` 的 BuildKit 缓存中；
- 前提是 Clash 已允许局域网访问，且监听地址不是仅限 `127.0.0.1`。

如果 Caddy 构建报 `goproxy.cn/sumdb/sum.golang.org` `504 Gateway Timeout`，可切换到官方 Go 代理（前提是服务器或 Docker 构建阶段能访问外网）：

```bash
sudo docker compose build \
  --build-arg GOPROXY=https://proxy.golang.org,direct \
  --build-arg GOSUMDB=sum.golang.google.cn \
  --no-cache caddy
sudo docker compose up -d
```

`.env` 内容如下：

```dotenv
ACME_EMAIL=你的有效邮箱
CLOUDFLARE_API_TOKEN=你的Cloudflare API Token
```

创建 Cloudflare Token 时使用自定义令牌，并限制为：

- 权限：`Zone / Zone / Read`；
- 权限：`Zone / DNS / Edit`；
- 区域资源：仅包含 `getnet.store`。

不要使用 Global API Key，也不要把 Token 发到聊天、截图或提交到 Git。

如果服务器上原来的 `.env` 仍包含 `TENCENTCLOUD_SECRET_ID` 和
`TENCENTCLOUD_SECRET_KEY`，请删除这两行并改成上面的
`CLOUDFLARE_API_TOKEN`。修改后必须重新构建 Caddy 镜像，单纯重启旧镜像不会切换 DNS 插件。

## 四、通过自定义域名访问

确认服务器当前 Tailscale IP 与 DNS 记录一致：

```bash
tailscale ip -4
# 应输出 100.117.187.60
```

Cloudflare DNS 需要存在以下记录，并保持 **仅 DNS（灰色云朵）**：

```text
bill.getnet.store  A  100.117.187.60
```

旧的 Tailscale Serve 不再负责 HTTPS，可以清除旧配置：

```bash
sudo tailscale serve reset
sudo docker compose up -d --build
sudo docker compose logs -f caddy
```

Caddy 会通过 Cloudflare DNS-01 验证域名并自动申请证书。日志出现证书获取成功后，Tailnet 内成员访问：

```text
https://bill.getnet.store
```

验证 DNS、HTTPS 和反向代理：

```bash
nslookup bill.getnet.store
curl -I https://bill.getnet.store
```

DNS 应返回 `100.117.187.60`，HTTP 状态应为 `200` 或应用正常返回的跳转状态。

## 五、日常运维

```bash
# 查看日志
sudo docker compose logs -f

# 只查看 HTTPS 和证书日志
sudo docker compose logs -f caddy

# 更新版本
git pull
sudo docker compose up -d --build

# 备份数据（最重要的一步）
sudo tar -czf billmanage-backup-$(date +%F).tar.gz data/
# 建议定期把 data 目录备份到其他机器
```

## 六、（备用）公网访问：Cloudflare Tunnel

如果未来有“不装 Tailscale 也要访问”的需求，可以叠加 Cloudflare Tunnel：

- 在海外/香港 VPS 上运行 cloudflared，回源 `http://100.x.x.x:8000`（该 VPS 需加入同一 tailnet）；
- 或直接在服务器上运行 cloudflared，回源 `http://127.0.0.1:8000`；
- 公网域名的 DNS 设在 Cloudflare，CNAME 到 `<tunnel-id>.cfargotunnel.com`。

注意：公网暴露后必须加登录保护（Caddy Basic Auth 或应用登录页），否则任何人可访问。

## 端口与安全说明

- Docker 端口映射 `127.0.0.1:8000:8000` 的意思是：**宿主机的 8000 端口 → 容器内的 8000 端口**，且只绑定本机回环地址（`127.0.0.1`）。因此公网网卡不监听 8000，安全组不要放行 8000；
- Caddy 使用宿主机网络，并通过 `bind 100.117.187.60` 只监听 Tailscale 地址的 `443` 端口；
- 组内访问路径：成员 → `https://bill.getnet.store:443` → Caddy → `http://127.0.0.1:8000`（本机回环）→ Docker 容器；
- 腾讯云安全组不要开放公网 `80`、`443` 或 `8000`；证书验证通过 Cloudflare API 完成，不依赖公网入站端口；
- 数据持久化在宿主机 `data/` 目录（SQLite 数据库 + 全部票据原件）；
- 容器内时区已设为 `Asia/Shanghai`，归档月份与报销日期与本地一致。
