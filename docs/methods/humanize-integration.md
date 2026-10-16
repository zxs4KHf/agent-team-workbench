# Humanize 方法的本地化接入

## 结论

优先吸收方法到已有链路，而不是先装一套 `hmz` 再为它新建任务看板。当前采用“原生方法模块＋保留可选运行器接缝”。本批没有注册一个无实际执行能力的插件，也不把未安装的上游说成已经接入。

原生改动解决具体缺口：施工者和审查者共用原任务目标；审查分清目标覆盖与代码正确性；返修不丢前轮意见；历史教训按任务上下文与实际改动选择，而非每轮灌入所有文档。

## 实际调用链

`bus_core.dispatch(build)` → `freeze_brief()` → `bus_build.open_worktrees(review_brief=...)`。

原目标和有限上下文摘要保存在既有 `console/builds/<task>.json`，与施工单共同存在。原始来源正文不再次复制，目标有完整文本摘要和截断标记。

`bus_core.render_task(build)` → `builder_guidance()`：先明确验收条件与非目标，按显式上下文文件选择相关经验。没有相关路径时不给不相干经验，不用关键词猜项目权限。

`bus_build.dispatch_review()` → `bus_build_safety.review_pack()` → `bus_review_context.render()`：带原目标、目标摘要、历史反馈、按精确差异文件选择的最多三条经验，再接既有差异和门禁结果。

`bus_build_safety.set_review()`：先验证不同作者、当前精确提交，再在原写锁内保存最后审查及有界历史。下一轮读取原施工单，不另开任务数据库。

`bus_build_safety.merge()` 完全保留原检查；本批没有增加绕过 PASS、测试或发布授权的入口。

## 从上游借什么，怎样调整

| 上游方法 | 本项目做法 | 不照搬的部分 |
|---|---|---|
| RLCR 分开实施进度检查和代码审查 | 实施提示中列 GOAL_COVERAGE，审查包分开目标覆盖和 CODE_REVIEW | 不凭目标表打勾直接 PASS，也不让同一模型自审 |
| 保留目标以避免迭代走偏 | 派发时冻结原问题、上下文指纹与项目来源 | 不另建与业务任务竞争的 Goal Tracker 数据库 |
| 审查反馈进入下一轮 | 原施工记录中保留最多8条，送审显示最近3条，并标明省略 | 旧 PASS 只是历史，不授权新提交；不自动无限重试 |
| BitLesson 按相关性加载经验 | 从版本管理的 `engineering-lessons.json` 按显式路径匹配，最多3条 | 不自动接受模型新写的经验；不复制个人笔记或上游知识库 |
| 程序化执行与恢复 | 保留为未来可选 `hmz` 执行适配器 | 本次没有安装、执行、跨进程恢复或预算系统 |

## 经验目录怎样维护

只保留可复用、与代码有关的短经验。当前六条来自已经验证的本项目故障与回归，不来自用户个人资料。

每条需要唯一ID、accepted状态、有限路径范围、短摘要、对应本仓库测试路径和来源说明。`accepted` 是经正常源码审查维护的标记，不是运行时核实出来的测试结果。代码只检查格式和测试文件存在，输出始终注明 `evidence_executed=false`。

新增经验时先提出条目和证据，再按原 PR 流程审查目录；不得直接将 Agent 本轮猜想加入已接受经验。每次送审记录目录内容 SHA-256，防止不同目录版本被当作同一份指导。目录不存在、损坏或太大时输出明确诊断，既不伪造经验，也不阻断旧调用的基本审查能力。

## 怎样验证本批效果

不需要启动新服务或录入生产任务。在隔离工作副本中运行：

```powershell
py -3 -X utf8 tests/test_review_methods_20261015.py
py -3 -X utf8 tests/test_verified_build_e2e.py
py -3 -X utf8 tests/test_dispatch_project_contract_20261013.py
py -3 -X utf8 scripts/run_tests.py --fast -j 2 --timeout 90
```

回归使用临时 Git 仓库，演示原任务→施工→RETURN→新提交→再次审查。新审查包应看得到原要求和前次缺陷，不能把前次结论当新提交授权。没有真实模型调用、通知或生产写入。

## 将来确有收益时，再接 Humanize 运行器

它应是可替换执行插件，不是第二个总控。插件输入：ATW任务ID、固定目标/许可指纹、明确工作副本、已授权工具、预算和停止条件。输出：运行ID、候选提交、测试证据、真实停止原因和未解决项。

ATW保留任务归属、本人验收、总预算和合入/发布权；只有一方管理同一工作副本。预算耗尽、阶段 result、CLI exit 0、恢复新run均不能直接变成已完成。运行器故障必须可暂停，不能为了恢复改凭据或扩大工具权限。

现阶段不接的具体理由：现有工作台原生Windows/Python3.11链路可直接吸收方法；所核对的新上游元数据要求Python>=3.12、标注Alpha、平台分类为macOS/Linux。必须先用临时历史任务回放验证收益，再引入隔离运行环境和实际适配器。这里不是断言它绝对无法在Windows运行。

## 来源、版本和不确定性

参考上游不是本地产品的关联方，也不表示上游审阅或认可本实现。

- 原版RLCR概览：https://github.com/PolyArch/humanize
- 原版经验方法（固定参考）：https://github.com/PolyArch/humanize/blob/4eeb392cd2450c3df99cb898bd6b24839967bbc3/docs/bitlesson.md
- 新版许可证：https://github.com/humanfia/humanize/blob/e30c7191ccd77701bafc13a06fb647848f86b083/LICENSE
- 新版平台/依赖信息：https://github.com/humanfia/humanize/blob/e30c7191ccd77701bafc13a06fb647848f86b083/pyproject.toml
- 运行器CLI与恢复文档：https://docs.humanfia.ai/humanize/reference/cli 、https://docs.humanfia.ai/humanize/user/resuming

核对日期：2026-10-15。GitHub固定文件核对与网页缓存是不同证据；本轮部分新站页面获取失败，不以缓存或上轮概览证明当前运行器协议完全兼容。本批不实现其协议、不复制其源码或长提示词。原版README声明MIT，但独立LICENSE读取未成功；新版LICENSE已读取为Apache-2.0。直接引用代码的许可保留与通知义务需在真正引入时单独核对，本次只是方法借鉴和自主实现。

状态以本次测试、独立审查、部署回执为准；本文不是上线证明。ADR0025记录了兼容和回退边界。
