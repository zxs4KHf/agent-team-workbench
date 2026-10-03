# 0005 运行数据移出 git

**情况**：`log/`（378 个文件）、`projects/`、`peers/`、`inbox/`、`outbox/`、`console/`、`attachments/` 都被提交在 git 里。后果：
- 每次切分支、回滚代码，任务历史跟着变；
- `bus_build` 在施工副本里 `git add -A`，会把运行数据带进提交；
- `console/mcp_url.txt`（含 MCP 地址密钥）进入过 git 历史；
- 测试悄悄依赖真实数据（`join_batch1_checks` 读真实 `peers/`），换台机器就失败。

**决定**：以上目录与 `events/ intake/ feedback/ .runtime/ plugins.local.json` 全部 `git rm --cached` 并写入 `.gitignore`；磁盘上的文件保留。测试一律用临时目录夹具。

**为什么**：代码和数据的生命周期不同。代码要版本管理、能回滚；数据要持续累积、不能被回滚冲掉。

**放弃了什么**：任务历史不再有 git 版本记录。需要备份时整目录复制（以后可做成定时备份脚本）。

**合入注意**：这些文件在主目录里一直被服务改写，`git merge --ff-only` 会以"本地改动将被覆盖"直接拒绝；就算先让它们变干净，合入也会把它们从磁盘删掉。所以 v0.4.0 用 `scripts/migrate_v040.py` 合入：先确认目标是 main 的后代，`reset --soft` 移动分支指针，紧接着 `reset`（即 --mixed）让索引等于新提交，再只检出目标版本的代码文件，并删除目标版本已删除的代码文件（`--no-renames` 口径，改名的旧路径也算）。运行数据完全不碰。合并前仍然整目录备份。如果中途被 `.git/index.lock` 打断，再跑一次 `apply` 会从刷新索引那一步续做。

**后续动作**：`console/mcp_url.txt` 曾进入 git 历史，应轮换 MCP 地址密钥。

**回滚**：先把 log/inbox/outbox/peers/attachments/console/projects 移到别处，再 `git revert d93a355`（它会写回 9/21 的旧内容），然后把移走的目录拷回覆盖并 `git add`。直接 revert 会和磁盘上的同名文件冲突，git 会拒绝。
