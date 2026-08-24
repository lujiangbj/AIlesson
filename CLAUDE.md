# AIlesson 开发规则

AI 英语课程引擎：把影视素材改造成「一集 = N 节课」的课程。
点选为主 + 语音旁路。首个真实用户是项目所有者本人（CET-6，会词但不会用）。

**动手前先读 `docs/requirements/PRD-课程引擎.md`**，特别是 §10 需求变更记录 ——
那里写了已经被实测推翻的假设，别再走回头路。

---

## 1. 铁律

### 1.1 TDD，先写测试

先写测试 → 跑到红 → 写实现 → 跑到绿。不许反过来。

- LLM 相关逻辑用 `FakeLLM` 断言**结构和约束**，不断言具体文案
- 真实 LLM 调用打 `@pytest.mark.llm`，默认跳过（`pyproject.toml` 的 addopts）
- 每次改完跑 `.venv/bin/python -m pytest tests/`，全绿才算完

```bash
.venv/bin/python -m pytest tests/                      # 单元测试（快，不花钱）
.venv/bin/python -m pytest tests/ -m llm -o addopts=""  # 真实 LLM（慢、花钱）
```

### 1.2 需求变了就改 PRD

改了行为、推翻了假设、发现了新约束 —— **同一轮里就更新 PRD**，不要等人来提醒。

- 功能变化 → 改对应 FR
- 推翻假设 → 在 §10 加一条，写清「原假设 / 推翻原因 / 改为什么」
- 验收状态变化 → 更新 §9 的表
- 版本号 + 底部变更记录

PRD 落后于代码 = 缺陷。

### 1.3 素材数据层零改动

素材在 `~/Claude/nowordenglish/illit-english-mvp/`，**只读**。
不改它的 JSON，不改它的 assets，不往那个目录写东西。
需要新字段就在本项目里推导（例：`episode.words_covered_by_sentence()`）。

### 1.4 LLM 该用就用

MVP 阶段追求效果上限，**不考虑成本**。判断类任务（分组、划分、讲解、写文案）
一律走 LLM，别用规则凑合 —— 我们要先知道最好能做到什么程度。

规则兜底只在 LLM 完全不可用时启用，且**不缓存兜底结果**（下次该重试 LLM）。

用 `zgy` provider（Claude 系列）。**`zgy-ds` 是付费 key，不要用。**

### 1.5 删掉死代码，不要留并行路径

需求推翻后，旧模块连同它的测试一起删。留着「带测试的死代码」比删掉更糟 ——
它看起来是活的，会误导下一次改动。

同名撞车尤其危险：`LLMCache.get_or_build_plan` 曾有一个 v0.3 就该删的同名旧版本
定义在后面，把新的静默盖掉，一调就 NameError。

---

## 2. 架构

五个包 + 一个编排层。**依赖只准朝一个方向走**，`tests/test_layout.py` 用 AST 查
import，漏回去就红。

```
                    contract  ← 谁都读，它谁都不读
                       │
   content ─→ [lesson JSON] ─→ course ─→ [LessonSpec] ─→ classroom
                                   ↘    learner    ↙
                                      session（编排）
```

| 包 | 职责 | 允许依赖 |
|---|---|---|
| `contract/` | 素材结构、教具表、课程计划结构 | 无 |
| `content/` | 教研内容：切段、CEFR 分级、抽短语句子、配图可行性、完备度 | contract、infra |
| `course/` | 组课：自评、探测、动态挑选、按教学点打包 | contract、infra、learner |
| `classroom/` | 教室端：教具、编排、课堂运行时、报告、语音旁路 | contract、infra、learner |
| `learner/` | 学习者数据：用户名册、掌握度与复习调度 | contract、infra |
| `infra/` | LLM 客户端 | 无 |
| `session.py` | 编排层，串起以上四块 | 任意 |
| `server/` | HTTP：`state.py` 应用状态 + `routers/` 按关注点分 | 任意 |

两条关键边界：

- **教研不知道学习者的存在**。内容生产是可复用产物，一旦读了学习者数据就没法预生成。
- **教室端不依赖组课**。它只吃 `LessonSpec`，不关心那是 LLM 聚类出来的还是手工编的。
  `LessonSpec` / `CoursePlan` 因此放在 contract，不在 course。

### 文件

| 文件 | 职责 |
|---|---|
| `contract/episode.py` | 读素材 JSON，推导句子↔短语↔词的覆盖关系 |
| `contract/lesson_spec.py` | 一节课的教学点清单 + 一集的课程表 |
| `contract/tools.py` | **教具表**：7 件教具各自的交互形态、适用域、素材需求 |
| `classroom/arrangement.py` | **编排**：16 环节各用什么教具、内容从哪来、计不计分 |
| `classroom/cards.py` | Card 模型（tool + domain + direction）+ 选项构造 |
| `classroom/runtime.py` | 按编排把一节课展开成卡序并推进 |
| `classroom/report.py` | 课后报告 |
| `classroom/voice.py` | 语音旁路：闭嘴规则 + 播放队列 |
| `course/assessment.py` | 三层自评：词/短语/句子各自分池 |
| `course/checklist.py` | 三层清单分组（LLM），供铺开勾选 |
| `course/probe.py` | 听力探测：抽样测短语/句子掌握度，校准后推断其余 |
| `course/selector.py` | 按待学词池动态挑该练的 chunk 和句子 |
| `course/planner.py` | 按教学点打包成 N 节课（LLM 场景聚类） |
| `course/cache.py` | LLM 结果缓存 |
| `learner/progress.py` | 双向 streak 掌握度 + 复习调度 |
| `learner/users.py` | 用户系统（无密码）+ 课堂数据记录 |
| `content/segment.py` | 把一集剧本切成学习段 |
| `content/vocab_cefr.py` | token → CEFR 词表归一 |
| `content/chunker.py` | 从剧本抽教学短语和教学句 |
| `content/pickable.py` | 判断一个词能不能用单张图教 |
| `content/wordsense.py` | 给待配图的词绑定剧中原句，归并词形变体 |
| `content/friends_lesson.py` | Friends 资产 → 引擎认的 lesson JSON |
| `content/completeness.py` | **完备度矩阵**：哪个教学点能跑哪些教具、缺什么素材 |
| `web/` | 学习者端（`/`）；`web/admin/` 后台（`/admin`） |

### 存储布局

```
data/
  users.json                   用户名册 + 当前选中
  users/<uid>/state.json       学习状态（分用户）
  users/<uid>/history.jsonl    课堂数据（分用户，追加写）
  cache/                       LLM 缓存 —— 全局共享，不分用户
```

**LLM 缓存不分用户**：词表分组全集共享；打包结果按待学池哈希，两人勾出同样的池子
可以复用。分用户会白烧钱。

**空会话不许覆盖已有进度**：切用户时会先保存当前会话，如果那是「进程刚起」的空态，
会静默清掉盘上的进度。`AppState._is_blank()` 挡住这种情况。

### 2.1 教学点，不是词

**1 个教学点 = 1 个词 / 1 个短语 / 1 个句子。** 一节课 5~10 个点。

短语和句子是**一等教学点**（各有首触/反向/跟读），不是词的配套。一节课可能一个生词
都没有 —— 那是目标用户的常态，必须能跑通。

### 2.2 一节课是教具拼起来的

教具（`contract/tools.py`）声明三件事：交互形态、适用哪几层、**需要什么素材**。
最后一项是枢纽 —— `content/completeness.py` 靠它算出「这一集哪些教学点能跑哪些
教具」，教研后台的矩阵就是这么来的。

环节（`classroom/arrangement.py`）= 一件教具的一次实例化 + 内容来源。原先散在
运行时里的魔数（`STREAK_SEGMENTS`、`in (3,6,10)`、`== 14`）现在是环节属性
（`scored` / `first_touch` / `source`）。

加一件教具复用已有交互形态的话，前端不用动 —— 渲染注册表按 `interaction` 分派。

### 2.3 改编排 = 换教材

编排带 `id` + `version`，随快照落盘。**版本不匹配就不许重建课堂**
（`arrangement.compatible()`）。

理由见 2.5：卡序是确定性重建的，拿新编排恢复旧快照会得到另一副牌，续上错位且不报错。
改了 `DEFAULT` 的任何一环，记得升 `version`。

### 2.4 Tutor 是演员，不是导演

环节顺序、出哪张卡、什么时候放音频，**全由代码（编排 + 运行时）决定**。
LLM 只负责「这句话怎么说」。让 LLM 判断「该进下一环节了」会跑偏且不可复现。

### 2.5 卡序必须确定性可重建

课程可中断续上，所以**卡序不能依赖会变的状态**。

`pick_review`（读 last_at/streak）和 `weaker_direction`（读 streak）都是可变的 ——
它们的结果必须在建课时定死并随快照落盘（`review_picked`/`spot_picked`/`dir_picked`）。
否则 restore 时重算会得到另一副牌，续上错位到别的卡。

编排本身也是重建输入的一部分，见 2.3。

这类 bug 只有端到端才会暴露，写回归测试钉死。

### 2.6 语音是旁路

答题永远本地判定、零延迟（`correct = choice === correct_id` 在前端算）。
Tutor 的话藏在「学生看正确答案」的 1~2s 间隙里。

语音层任何失败（TTS 超时 / LLM 挂 / 评分异常）**都不得阻塞答题**。

### 2.7 内容线还没接进引擎

Peppa 一集 5 分钟 / 53 词；Friends 一集 22 分钟 / 300+ 句 / 300+ 生词，
按引擎的容量算要 50 节课，「一集 = N 节课」的映射会爆掉。所以要先切段 + 分级。

**接口没定之前，别在 course/ 或 classroom/ 里为 Friends 加特例。**

---

## 3. 诚实原则

这些是刻意的设计，不要为了数字好看改掉：

- **课内不产生「已掌握」** —— 每方向只练 1 次，streak 只到 1。掌握要靠后续复习确认。
- **报告不说「掌握」** —— 说「学了 X 个 / 一次答对 Y 个 / Z 个下次要复习」。
- **报告按三层分别说** —— 3 词 + 5 短语 + 2 句，不是「10 个词」。
- **巩固环节答错仍清零** —— 不计 streak 不等于把错误藏起来。
- **自评不可靠靠抽检兜底** —— 不追求自评本身准确。

---

## 3.1 多用户注意

- 用户 id 要能安全当目录名（名字含 `/`、`..` 都不能污染路径）
- **没选用户时，学习类接口返回 409**，不许静默写到别处
- 删用户 = 删他的 state + history，不可恢复，前端必须确认
- 加了新的持久化数据？想清楚它该分用户还是全局

## 4. 用户界面约定

- **不露内部 id**。`s16` / `guess_what` 用户看不懂，用 `label_of()` 转成原文。
- **三层的「会」标准要写明**。词是「听到能反应意思」，短语句子是「能张口说出来」。
- 面向成人学习者：帮助提示别太急（卡住收窄 20s，不是 8s）。
- 长列表逐行列出，别挤成一行。

---

## 5. 环境

```bash
bash scripts/start.sh          # 启动，→ http://127.0.0.1:8791
```

- Python 3.12（`uv venv --python 3.12 .venv`），依赖走阿里云镜像：
  `UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple/`
- 运行要 `PYTHONPATH=src`（editable install 在这个 venv 里不生效）
- **端口 8791**。8770 在 macOS 被 `sharingd`(dpap) 占用。
- `data/cache/` 是 LLM 缓存（贵，别随便删）；`data/state.json` 是学习进度（可删重来）
- 公司代理会让外部 API 慢 10 倍，`requests` 要 `trust_env = False`

### 5.1 排查顺序

外部 API 慢 → **先查网络路径/代理**，别先怀疑服务商（TLS 硬吃 2s 的坑踩过）。

---

## 6. 验证要求

**改完必须实跑，不能只跑单元测试。**

单元测试过 ≠ 能用。以下问题都是单元测试全绿、实跑才发现的：

- 报告口径错（「10 个词」）
- 报告露内部 id
- 「写小结」按钮点了 400
- 续上错位到别的卡
- 素材路径重复 `assets/assets/`

实跑手段：起服务 + 浏览器点，或写脚本打完整流程 API。

---

## 7. 已知缺口

引擎线：

1. **跟读只有「念好了/念不出来」两个按钮，没有真评分** ← 当前最大缺口。
   本地 wav2vec2 音素模型已验证可行（0 成本、定位具体错音、10s 音频 361ms）。
2. 打包要 20~40s 干等，没有进度提示。
3. TTS 未接（只有答错讲解走 LLM 文本）。

内容线：

4. **Friends 的 76 个句子全部缺原片切片**。逐字稿没时间轴，`audio_clip` 现在填的
   是 TTS —— 后台完备度矩阵会把这条报出来。而句子原声是 FR-4.5 的硬要求（听懂真实
   语流是产品核心），这是接通两条线的主要障碍。可选：强制对齐（whisper + 原片音轨）
   ／找带时间轴的字幕源。
5. Friends 一集 = 多段，每段 = N 节课。现有三层结构（集 → 课）要多加一层「段」，
   或把「段」当成引擎眼里的「集」。后者改动小，倾向后者。

后台：

6. 三个后台都是**只读**的。教具表和编排改起来还要动代码 ——
   改完编排记得升 `version`（见 2.3）。
