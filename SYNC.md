# 同步指南（自己 GitHub + 公司 GitLab）

本仓库有两个推送目标：

- `origin` → 自己 GitHub：`https://github.com/Yiming280/LangGraph-Course-freeCodeCamp.git`
- `gitlab` → 公司后端仓库：`http://git.cn.agile-robots.com/cn_isa/cn-isa-electrical/ai-assistant/backend.git`

DataAgent 是整个课程仓库的子目录；公司 GitLab 只放 DataAgent 后端，用 subtree 抽取，两边互不影响。

## 每次提交后的操作

在**仓库根目录**执行：

```bash
# 1. 提交改动
git add DataAgent/
git commit -m "改了什么"

# 2. 推到自己 GitHub（照旧，可选）
git push origin main

# 3. 同步到公司 GitLab（一条命令，替代之前的手动 split + push）
git subtree push --prefix=DataAgent gitlab main
```

第 3 步等价于之前手动做的 `git branch -D dataagent-only` + `git subtree split` + `git push`，现在一条搞定。它内部会自动 split，并把 DataAgent 的独立历史推到公司仓库的 `main` 分支。

## 特殊情况

**push 被拒（non-fast-forward）**：通常是你 rebase / amend 过历史。改用手动 force：

```bash
git subtree split --prefix=DataAgent -b dataagent-only
git push gitlab dataagent-only:main --force
```

如果 `dataagent-only` 分支已存在，先 `git branch -D dataagent-only` 再 split。

**只想更新 GitHub、暂不同步 GitLab**：跳过第 3 步即可。

## 一次性配置（已完成；换新机器才需要重做）

```bash
# 1. 加公司远程
git remote add gitlab http://git.cn.agile-robots.com/cn_isa/cn-isa-electrical/ai-assistant/backend.git

# 2. 首次推送（空仓库直接 subtree push 也行）
git subtree push --prefix=DataAgent gitlab main
```

## 记住

- 公司 GitLab 默认分支若是 `master`，去 **Settings → Repository → Default branch** 改成 `main`。
- `.env`、`agent_memory.sqlite`、`settings.json`、`sop.json`、`data/` 等都在 `.gitignore` 里，提交和推送时不会混进去。
- `git subtree push` 会在本地 `main` 上留一个 `Split 'DataAgent/' ...` 的 merge commit（无害，只是 `git log` 多一条）；介意的话就在推 GitHub 前自行判断是否要处理。

## 服务器端部署与更新

> 前提：公司 GitLab 的 `backend` 仓库内容 = **DataAgent 目录本身**（`--prefix=DataAgent` 已把子目录抽成仓库根）。服务器上 clone 下来直接就是 DataAgent 源码（`app.py`、`Dockerfile`、`docker-compose.yml` 等在根目录）。

### 首次部署（服务器上，只需一次）

```bash
# 1. 克隆（目录名自定）
git clone http://git.cn.agile-robots.com/cn_isa/cn-isa-electrical/ai-assistant/backend.git dataagent
cd dataagent

# 2. 准备 .env（gitignore 的，仓库里没有，必须手建）
cp .env.example .env
vim .env
#   必填：LLM_PROVIDER / OLLAMA_HOST / OLLAMA_MODEL（或 openai 后端 + API Key）
#   必填：DATABASES + DB_* 连接参数（数据库）
#   可选：IOTDB_HOST/PORT/USER/PASSWORD
#   日志读取：把 LOG_DIR=/var/log 改成 LOG_DIRS=/logs（配合 - ./logs:/logs 挂载）

# 3. 准备被 gitignore 的运行时数据（想带旧数据才需要；不带则自动空启动）
mkdir -p data logs
#   旧的 agent_memory.sqlite / sop.json 拷进 data/
#   要读的日志文件拷进 logs/

# 4. 首次构建并启动（联网拉基础镜像 + 装依赖）
docker compose up -d --build

# 5. 验证
docker compose ps
docker logs -f dataagent
curl localhost:8001/models
```

### 每次更新代码后（本地 push → 服务器）

```bash
cd /path/to/dataagent
git pull                  # 拉最新代码
docker compose restart    # 代码已挂载（- ./:/app），restart 即加载新代码，不用 rebuild
```

只有改了 `Dockerfile` / `requirements.txt` / `docker-compose.yml` 才需要重建：

```bash
docker compose up -d --build
```

### 单独更新 .env / 数据 / SOP

`.env`、`data/`、`logs/`、`knowledge/` 都在 `.gitignore`，`git pull` 不会带过来，直接在服务器上改：

```bash
vim .env                  # 改配置
docker compose up -d      # 重新读 env

# SOP 知识库：把 knowledge/*.xlsx 拷到服务器后
docker compose exec dataagent python import_sop.py
# 或直接把旧的 data/sop.json 拷进 data/
```

### 排查

```bash
docker compose ps                 # 容器是否在跑
docker logs -f dataagent          # 实时日志
curl localhost:8001/models        # 接口通不通
```

### 记住

- 服务器上**必须有源码目录**：`docker-compose.yml` 里的 `- ./:/app` 挂载的是实时源码，所以更新代码靠 `git pull`，不能用旧的 tarball/`run.sh` 交付方式（那是"不带源码的镜像包"，改代码不生效）。
- 首次 `docker compose up` 后，`docker-entrypoint.sh` 会在源码目录里生成 `agent_memory.sqlite` / `settings.json` / `sop.json` / `error_log.jsonl` 这几个指向 `/data` 的软链（容器内正常，宿主机上悬空）。它们都在 `.gitignore`，不影响 git；服务器只走 Docker 即可无视。
