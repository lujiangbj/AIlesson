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

| 文件 | 职责 |
|---|---|
| `episode.py` | 读素材 JSON（零改动），推导句子↔短语↔词覆盖关系 |
| `assessment.py` | 三层自评分池 |
| `checklist.py` | 三层清单分组（LLM） |
| `packer3.py` | 按教学点打包成 N 节课（LLM 场景聚类） |
| `cards.py` | Card 模型 + 选项构造 |
| `lesson3.py` | 16 环节课程状态机 |
| `progress.py` | 双向 streak 掌握度 + 复习调度 |
| `course3.py` | 集级会话 |
| `voice.py` | 语音旁路：闭嘴规则 + 播放队列 |
| `report.py` | 课后报告 |
| `cache.py` | LLM 结果缓存（全局，不分用户） |
| `users.py` | 用户系统（无密码）+ 课堂数据记录 |
| `server.py` | FastAPI + 素材托管 |

## 目录

```
AIlesson/
├── CLAUDE.md              # ⭐ 开发规则
├── docs/requirements/     # ⭐ PRD
├── docs/                  # 子策划01（多Agent）、02（实时语音流水线）
├── src/ailesson/          # 引擎
├── web/                   # 前端（原生 JS）
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
| 状态 | ✅ 能上课（Peppa E01） | 🚧 在建，未接进引擎 |
| 代码 | `episode/assessment/checklist/packer3/lesson3/course3` | `segment.py` `vocab_cefr.py` `scripts/friends_*.py` |
| 数据 | illit-english-mvp 素材（只读） | `data/friends/` `data/cefr/` |

B 要产出 A 能吃的 lesson JSON。两个待解问题见 PRD §12：Friends 一集要 50 节课
（需多一层「段」），以及逐字稿没时间轴（拿不到原片切片）。

**接口没定之前，别在 A 里为 Friends 加特例。**

## 已知缺口

1. **跟读只有「念好了/念不出来」两个按钮，没有真评分** ← 最大缺口，本地方案已验证可行
2. 打包要 20~40s 干等，没有进度提示
3. TTS 未接（只有答错讲解走 LLM 文本）
4. 引擎只有 Peppa E01；老友记内容线还没产出可用素材
