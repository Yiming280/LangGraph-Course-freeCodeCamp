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
