# AIlesson — AI 英语课

把影视素材改造成「一集 = N 节课」的 AI 课程。**点选为主 + 语音旁路。**

- 开发规则：**`CLAUDE.md`**（动手前必读）
- 需求文档：**`docs/requirements/PRD-课程引擎.md`**（含被推翻的假设记录）

## 快速开始

```bash
bash scripts/start.sh          # → http://127.0.0.1:8791
```

流程：创建用户 → 打开清单 → 三层分别勾掉已会的 → 安排课程 → 上课。

**多用户**：每个人的词表、掌握度、课堂数据独立。顶部「切换用户」可随时换人。
MVP 无密码。老的单用户 `data/state.json` 会自动迁移到用户「我」。

```bash
.venv/bin/python -m pytest tests/                       # 169 个单元测试（不花钱）
.venv/bin/python -m pytest tests/ -m llm -o addopts=""  # 真实 LLM（慢、花钱）
```

## 核心概念：教学点

**1 个教学点 = 1 个词 / 1 个短语 / 1 个句子。** 一节课 5~10 个点，32 分钟。

短语和句子是**一等教学点**，各有首触 / 反向 / 跟读环节，不是词的配套。
一节课可能一个生词都没有 —— 那是目标用户（CET-6，会词不会用）的常态。

```
一集 E01 = 53 词 + 28 短语 + 16 句 = 97 个教学点
  └─ 三层勾选（勾掉已会的）
  └─ LLM 按场景打包成 N 节课
  └─ 每节 16 环节：复习→抽检→词→短语→中场→句子→混打→重做→盲听→报告
  └─ 课后报告
```

实测：CET-6 用户勾掉 48 词 / 5 短语 / 1 句 → 38 个待学点 → **5 节课**，全是短语句子：

```
1. 下雨天，Peppa介绍小弟弟George      this is X / cannot play outside
2. 雨停了！穿靴子去跳泥水洼            wear your boots / jump in muddy puddles
3. 检查泥水洼，照顾好弟弟George        must check / it's only X
4. 爸爸你猜我们刚才在干嘛              guess what / we've been doing
5. 完蛋！妈妈快看见了赶紧打扫          oh goodness me / look at the mess
```

## 模块

四块分开迭代，靠契约层解耦。依赖方向由 `tests/test_layout.py` 守着。

```
                    contract  ← 谁都读，它谁都不读
                       │
   content ─→ [lesson JSON] ─→ course ─→ [LessonSpec] ─→ classroom
                                   ↘    learner    ↙
                                      session（编排）
```

| 包 | 职责 |
|---|---|
| `contract/` | 素材结构、**教具表**、课程计划结构 |
| `content/` | 教研内容：切段、CEFR 分级、抽短语句子、配图可行性、完备度矩阵 |
| `course/` | 组课：自评、听力探测、动态挑选、按教学点打包 |
| `classroom/` | 教室端：**编排**、课堂运行时、卡片、报告、语音旁路 |
| `learner/` | 学习者数据：用户名册、双向 streak 掌握度、复习调度 |
| `session.py` | 编排层，串起以上四块 |
| `server/` | HTTP：`state.py` 应用状态 + `routers/` 六个关注点 |

两条边界是这个划分的意义所在：

- 教研不知道学习者的存在 —— 内容生产是可复用产物，读了学习者数据就没法预生成。
- 教室端不依赖组课 —— 它只吃 `LessonSpec`，不关心那是 LLM 聚类的还是手工编的。

### 一节课是教具拼起来的

教具声明「交互形态 + 适用哪几层 + **需要什么素材**」。最后一项让内容完备度可计算：
后台能直接告诉你这一集哪些教学点跑不了哪些教具、缺的是什么。

环节 = 一件教具的一次实例化 + 内容来源。编排是带版本号的配置，改它等于换教材 ——
版本不匹配的旧快照不许重建课堂，否则续上会错位到别的卡。

## 两个入口

| | 地址 | 给谁 |
|---|---|---|
| 学习者端 | `/` | 上课。横屏 iPad 课堂布局 |
| 后台 | `/admin` | 教具表 / 编排 / 课程计划检查器 / 内容完备度 |

## 目录

```
AIlesson/
├── CLAUDE.md              # ⭐ 开发规则
├── docs/requirements/     # ⭐ PRD
├── docs/                  # 子策划01（多Agent）、02（实时语音流水线）
├── src/ailesson/
│   ├── contract/          # 素材 / 教具表 / 课程计划
│   ├── content/           # 教研内容线
│   ├── course/            # 组课
│   ├── classroom/         # 教室端（编排 + 运行时）
│   ├── learner/           # 学习者数据
│   ├── session.py         # 编排层
│   └── server/            # state + routers
├── web/                   # 学习者端（原生 JS，无框架）
├── web/admin/             # 后台
├── tests/                 # TDD 测试
├── scripts/
│   ├── start.sh           # 启动
│   ├── setup.sh           # 环境安装
│   ├── pron_score.py      # 发音评分（本地 wav2vec2，音素级）
│   ├── e2e_bench.py       # ASR+LLM+TTS 端到端测速
│   └── minimax_tts.py     # Minimax 流式 TTS
└── data/
    ├── cache/             # LLM 缓存（贵，别删；全局共享）
    ├── users.json         # 用户名册
    └── users/<uid>/       # 每人的 state.json + history.jsonl
```

## 语音链路实测（M3 Pro，2026-08）

端到端（学生说完 → Tutor 开口）**1.65s**：

| 环节 | 耗时 | 方案 |
|---|---|---|
| ASR | 0.34s | whisper-base 本地，0 元 |
| LLM 首 token | 0.56~0.94s | deepseek-flash + **关思考**（默认思考 3.4s，快 6 倍）|
| TTS 首包 | 0.37s | Minimax speech-2.6-turbo 流式直连 |

**本地免费发音评测可行**：wav2vec2 音素模型 + espeak 参考音素比对，能定位到具体错误
音素（如缺 ɹ 音），10s 音频 361ms，0 成本替代腾讯智聆。

⚠️ **whisper 不能做发音评分** —— 它是语义识别，会把 mawning 自动纠正成 morning，
文本比对恒得满分。发音质量必须音素级模型。

⚠️ **公司代理坑**：`proxy-aws-us`（美国 AWS）+ DNS fake-ip → TLS 硬吃 2s，
外部 API 慢 10 倍。解决：`requests.Session().trust_env = False`。
**线上 TTS 慢先查网络路径，别先怀疑服务商。**

## 两条并行线

| | A · 课程引擎 | B · 老友记内容生产 |
|---|---|---|
| 状态 | ✅ 能上课（Peppa E01） | 🚧 素材已产出，缺原片切片 |
| 代码 | `contract/` `course/` `classroom/` `learner/` | `content/` `scripts/friends_*.py` |
| 数据 | illit-english-mvp 素材（只读） | `data/friends/` `data/cefr/` |

B 要产出 A 能吃的 lesson JSON。两个待解问题见 PRD §12：Friends 一集要 50 节课
（需多一层「段」），以及逐字稿没时间轴（拿不到原片切片）。

**接口没定之前，别在 A 里为 Friends 加特例。**

## 已知缺口

1. **跟读只有「念好了/念不出来」两个按钮，没有真评分** ← 最大缺口，本地方案已验证可行
2. 打包要 20~40s 干等，没有进度提示
3. TTS 未接（只有答错讲解走 LLM 文本）
4. **Friends 的 76 个句子全部缺原片切片** —— 逐字稿没时间轴，`audio_clip` 现在填的
   是 TTS。后台完备度矩阵会把这条报出来。句子原声是产品核心，这是接通两条线的主要障碍
5. 三个后台都是只读的，改教具和编排还要动代码
