# BillManage 部署指南（Docker Compose + Tailscale）

推荐架构：**Docker Compose 部署应用，Tailscale 提供组内域名与 HTTPS**。
域名只解析在 Tailscale 组内，不出公网，因此**无需 ICP 备案**，腾讯云也不需要开放任何公网端口。

## 一、你需要准备什么

1. 腾讯云服务器一台（建议 Ubuntu 22.04/24.04，2 核 2G 起，系统盘 40G+）；
2. 一个 Tailscale 账号（组内成员共享的同一账号/企业版）；
3. 本仓库代码（可通过 `git clone` 或 `scp` 上传到服务器）；
4. （可选，第二步才需要）自己的域名，例如 `app.example.com`；
5. 无需备案、无需域名解析到公网。

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
- Python 依赖默认使用阿里云 PyPI 镜像；
- uv 下载目录使用 BuildKit cache mount。即使 `uv.lock` 更新，也能复用已下载的 wheel；
- 仅业务代码发生变化时，依赖安装层会直接命中 Docker 缓存。

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
  --progress plain
sudo docker compose up -d
```

说明：

- 代理走境外节点时，可将 Python 依赖源临时切回官方 PyPI；
- 下载结果会保存在名为 `billmanage-uv` 的 BuildKit 缓存中；
- 前提是 Clash 已允许局域网访问，且监听地址不是仅限 `127.0.0.1`。

## 四、通过 Tailscale 提供 HTTPS 访问

```bash
# 设置机器名（例如 billmanage），会显示为 billmanage.<tailnet>.ts.net
sudo tailscale set --hostname billmanage

# 把本机 8000 端口通过 HTTPS 提供给组内（自动签发证书）
sudo tailscale serve --bg 8000

# 验证
sudo tailscale serve status
```

组内成员现在可以访问：

```
https://billmanage.<你的tailnet名>.ts.net
```

## 五、（可选）绑定自己的域名

1. 打开 Tailscale 控制台 → **DNS** → MagicDNS 区域，添加自定义后缀 `example.com`；
2. 控制台 → **Machines** → 选中服务器 → 把 MagicDNS 名称改为 `app.example.com`；
3. 若控制台要求验证域名，按提示在 `example.com` 的 DNS 处添加 TXT/CNAME 记录（仅用于证书验证，不解析到公网）；
4. 控制台 → **DNS → HTTPS Certificates** 确认已启用；
5. 组内成员访问 `https://app.example.com`。

> 如果控制台没有“自定义后缀”入口，说明该功能未开放到你的套餐，退回使用
> `https://billmanage.<tailnet>.ts.net` 即可，功能完全一致。

## 六、日常运维

```bash
# 查看日志
sudo docker compose logs -f

# 更新版本
git pull
sudo docker compose up -d --build

# 备份数据（最重要的一步）
sudo tar -czf billmanage-backup-$(date +%F).tar.gz data/
# 建议定期把 data 目录备份到其他机器
```

## 七、（备用）公网访问：Cloudflare Tunnel

如果未来有“不装 Tailscale 也要访问”的需求，可以叠加 Cloudflare Tunnel：

- 在海外/香港 VPS 上运行 cloudflared，回源 `http://100.x.x.x:8000`（该 VPS 需加入同一 tailnet）；
- 或直接在服务器上运行 cloudflared，回源 `http://127.0.0.1:8000`；
- 域名 `app.example.com` 的 DNS 设在 Cloudflare，CNAME 到 `<tunnel-id>.cfargotunnel.com`。

注意：公网暴露后必须加登录保护（Caddy Basic Auth 或应用登录页），否则任何人可访问。

## 端口与安全说明

- Docker 端口映射 `127.0.0.1:8000:8000` 的意思是：**宿主机的 8000 端口 → 容器内的 8000 端口**，且只绑定本机回环地址（`127.0.0.1`）。因此公网网卡不监听 8000，安全组不要放行 8000；
- 组内访问路径：成员 → `https://billmanage.<tailnet>.ts.net:443` → Tailscale 的 `serve` 转发 → `http://127.0.0.1:8000`（本机回环）→ Docker 容器；
- 数据持久化在宿主机 `data/` 目录（SQLite 数据库 + 全部票据原件）；
- 容器内时区已设为 `Asia/Shanghai`，归档月份与报销日期与本地一致。
