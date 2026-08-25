// AIlesson 后台。三个视图：教具 / 编排 / 课程计划 / 内容完备度。
// 只读 —— 这里是拿来看和判断的，改配置走代码（编排改了要升 version）。
'use strict';

const app = document.getElementById('app');
const nav = document.getElementById('nav');
const sub = document.getElementById('sub');
const headRight = document.getElementById('head-right');

const S = {
  view: 'scripts',
  scripts: null,
  script: null,       // 当前展开的剧本 id
  chunks: null,       // 换场切出的最小单位
  segPlan: null,      // 切段效果
  segN: null,         // 预览段数（null = 用落盘/自动）
  compare: null,      // 几种段数并排比
  segLines: null,     // 展开的某段台词
  openSeg: null,
  tools: null,
  arrangement: null,
  plan: null,
  cards: {},        // index → 展开的卡序
  openLesson: null,
  completeness: null,
  arrangementOnly: true,
  status: null,
  err: null,
};

// 三个后台，各自的作用域不同 —— 平级铺开会让层级混乱：
//
//   教研内容  作用域 = 一部剧的一集     （剧本、切段、素材）
//   课程      作用域 = 一个学习者       （ta 的自评、探测、课程表）
//   系统      作用域 = 全局             （教具表、编排）
//
// 所以导航分两级，右上角只显示当前后台真正相关的那个作用域。
const BACKENDS = [
  ['research', '教研内容', '按剧集', [
    ['scripts', '剧本与切段'],
    ['assets', '素材完备度'],
  ]],
  ['course', '课程', '按学习者', [
    ['plan', '课程计划'],
  ]],
  ['system', '系统', '全局', [
    ['tools', '教具'],
    ['arrangement', '编排'],
  ]],
];

const PAGE_OF = {};          // 页面 id → 后台 id
const PAGES_OF = {};         // 后台 id → 页面 id 列表
for (const [bid, , , pages] of BACKENDS) {
  PAGES_OF[bid] = pages.map(([pid]) => pid);
  for (const [pid] of pages) PAGE_OF[pid] = bid;
}
const ALL_PAGES = Object.keys(PAGE_OF);
const backendOf = (page) => PAGE_OF[page] || 'research';
const labelOf = (page) => {
  for (const [, , , pages] of BACKENDS) {
    for (const [pid, label] of pages) if (pid === page) return label;
  }
  return page;
};

// ---------- 工具 ----------

const h = (tag, props = {}, kids = []) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.appendChild(typeof kid === 'object' ? kid : document.createTextNode(kid));
  }
  return el;
};

async function get(path) {
  const r = await fetch(path);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `${r.status} ${path}`);
  return body;
}

const pct = (a, b) => (b ? Math.round((a / b) * 100) : 100);

function bar(ok, total) {
  const p = pct(ok, total);
  return h('div', { class: 'bar' + (p < 100 ? ' has-gap' : '') },
           [h('i', { style: `width:${p}%` })]);
}

// 中性的相对大小条。bar() 会给不满 100% 的上橙色（那在别处表示「缺素材」），
// 用来比 chunk 大小会误导
function sizeBar(v, max) {
  return h('div', { class: 'bar neutral' },
           [h('i', { style: `width:${pct(v, max)}%` })]);
}

function panel(title, subtitle, kids) {
  return h('section', { class: 'panel' }, [
    h('h2', {}, [title]),
    subtitle ? h('div', { class: 'sub' }, [subtitle]) : null,
    ...kids,
  ]);
}

// 当前后台的作用域。三个后台各自只关心一样东西
function scopeText(bid) {
  const st = S.status;
  if (bid === 'research') {
    const n = S.scripts ? S.scripts.scripts.length : null;
    return S.script ? `第 ${S.script} 集`
                    : (n ? `${n} 集剧本` : '剧本');
  }
  if (bid === 'course') {
    if (!st) return '';
    return st.user ? `学习者：${st.user.name} · ${st.episode.title.slice(0, 30)}`
                   : '还没有学习者';
  }
  if (bid === 'system' && st && st.arrangement) {
    return `编排 ${st.arrangement.id} v${st.arrangement.version}`;
  }
  return '';
}

function table(headers, rows) {
  return h('table', {}, [
    h('thead', {}, [h('tr', {}, headers.map(
      x => h('th', { class: x.num ? 'num' : '' }, [x.label ?? x])))]),
    h('tbody', {}, rows),
  ]);
}

// ---------- 剧本与切段 ----------
//
// 第一步（逐字稿来源 + 切段规则）走代码，规则由人给。这里只读 ——
// 职责是把「切成了什么样」摊开到能判断规则对不对。

function viewScripts() {
  const d = S.scripts;
  if (!d) return [h('div', { class: 'empty' }, ['加载中…'])];
  if (!d.scripts.length) {
    return [panel('剧本', '', [
      h('div', { class: 'note' },
        ['还没有解析好的剧本。先跑 scripts/friends_parse.py']),
    ])];
  }

  const out = [
    panel('剧本', `切段规则：${d.rule}`, [
      table(
        ['集', '标题', { label: '句', num: true }, { label: '换场', num: true },
         { label: '人物', num: true }, '词表', '切段', 'lesson', ''],
        d.scripts.map(s => h('tr', {}, [
          h('td', {}, [h('b', {}, [s.episode_id])]),
          h('td', {}, [s.title || '—']),
          h('td', { class: 'num' }, [String(s.lines)]),
          h('td', { class: 'num' }, [String(s.scenes)]),
          h('td', { class: 'num' }, [String(s.speakers)]),
          h('td', {}, [step(s.has_vocab)]),
          h('td', {}, [s.has_segments
            ? h('span', { class: 'tag ok' }, [`${s.n_segments} 段`])
            : step(false)]),
          h('td', {}, [step(s.has_lesson)]),
          h('td', {}, [h('button', {
            class: 'act' + (S.script === s.episode_id ? ' on' : ''),
            onclick: () => openScript(s.episode_id),
          }, [S.script === s.episode_id ? '收起' : '看切段'])]),
        ]))
      ),
    ]),
  ];

  if (S.script) {
    out.push(...chunkPanels());
    out.push(...segPanels());
  }
  return out;
}

function step(done) {
  return done ? h('span', { class: 'tag ok' }, ['已有'])
              : h('span', { class: 'tag' }, ['未做']);
}

// CEFR 等级配色：越高越显眼（越可能是要教的重点）
const LEVEL_CLS = { A1: '', A2: '', B1: 'warn', B2: 'warn', C1: 'bad', C2: 'bad' };

// 一段的详细情况：规模 → 场景 → 生词清单 → 台词原文
function segDetail(d) {
  const byLevel = {};
  for (const w of d.new_words) byLevel[w.level] = (byLevel[w.level] || 0) + 1;

  return panel(`第 ${d.index} 段`,
    `${d.lines} 句 / ${d.words} 词 / 约 ${d.minutes} 分钟 · `
    + `${d.new_words.length} 个生词（${d.known_level} 及以下算已会）`, [

    // 场景走向
    h('div', { class: 'row', style: 'margin-bottom:10px' }, [
      h('span', { class: 'dim' }, ['场景：']),
      ...d.locations.map(x => h('span', { class: 'tag' }, [x])),
    ]),

    // 生词清单 —— 这段教什么，看这里
    d.new_words.length
      ? h('div', {}, [
          h('div', { class: 'row', style: 'margin:14px 0 8px' }, [
            h('b', {}, ['生词']),
            ...Object.entries(byLevel).sort()
              .map(([lv, n]) => h('span', {
                class: 'tag ' + (LEVEL_CLS[lv] || ''),
              }, [`${lv} ${n}`])),
          ]),
          h('div', { class: 'wordlist' }, d.new_words.map(w => h('span', {
            class: 'word-chip',
            title: `${w.level} · 出现 ${w.count} 次`,
          }, [
            w.token,
            h('i', { class: 'lv ' + (LEVEL_CLS[w.level] || '') }, [w.level]),
            w.count > 1 ? h('i', { class: 'ct' }, ['×' + w.count]) : null,
          ]))),
        ])
      : h('div', { class: 'note' }, [
          '还没有词表，看不到生词清单。先跑 '
          + 'scripts/friends_cefr.py <集> --llm --json',
        ]),

    // 台词原文 —— 核对切点切得对不对
    h('div', { style: 'margin-top:16px' }, [
      h('b', {}, ['台词']),
      h('div', { class: 'script-box' },
        d.items.map(it => it.type === 'scene'
          ? h('div', { class: 'scene-line' }, ['◆ ' + it.text])
          : h('div', { class: 'say' }, [
              h('span', { class: 'who' }, [it.speaker + '：']),
              it.text,
            ]))),
    ]),
  ]);
}

// 换场切出的 chunk 是切段的最小单位 —— 最大的那个决定了段大小的下限
function chunkPanels() {
  const c = S.chunks;
  if (!c) return [h('section', { class: 'panel' }, ['算 chunk…'])];
  const floorPct = Math.round(c.floor_words / c.total_words * 100);
  return [
    panel(`${c.episode_id} · 换场切出 ${c.n_chunks} 个 chunk`,
      '切段的最小单位。任何一段至少包含一个完整 chunk —— 所以最大的 chunk '
      + '就是段大小的下限。', [
      floorPct >= 25
        ? h('div', { class: 'note bad' }, [
            `第 ${c.floor_at} 个 chunk 有 ${c.floor_words} 词，占全集 ${floorPct}%，`
            + '切不开。任何包含它的段都至少这么大 —— 段数切得再多，这一段也不会变小，'
            + '只会让其它段更碎。要更均就得允许在场景内部切（用舞台提示当次级边界）。',
          ])
        : h('div', { class: 'note' }, [
            `最大 chunk ${c.floor_words} 词（占 ${floorPct}%），不构成瓶颈。`,
          ]),
      table(['#', '地点', { label: '句', num: true }, { label: '词', num: true },
             { label: '占比', num: true }, ''],
        c.chunks.map(x => h('tr', {}, [
          h('td', { class: 'dim' }, [String(x.index)]),
          h('td', {}, [x.location]),
          h('td', { class: 'num' }, [String(x.lines)]),
          h('td', { class: 'num' }, [
            String(x.words),
            x.index === c.floor_at
              ? h('span', { class: 'tag bad' }, ['下限']) : null,
          ]),
          h('td', { class: 'num dim' }, [`${x.share}%`]),
          h('td', { style: 'width:140px' }, [sizeBar(x.words, c.floor_words)]),
        ]))),
    ]),
  ];
}

function segPanels() {
  const p = S.segPlan;
  if (!p) return [h('section', { class: 'panel' }, ['算切段…'])];
  const out = [];

  // 切段效果（主视图）
  out.push(panel(
    `切成 ${p.n} 段` + (p.auto ? '（自动选的段数）' : ''),
    `${p.total_lines} 句 / ${p.total_words} 词 · 不均衡度 ${p.spread}`
    + (p.saved ? ' · 已落盘' : ' · 预览，未落盘'), [
    table(
      ['段', { label: '场', num: true }, { label: '句', num: true },
       { label: '词', num: true }, { label: '时长', num: true },
       { label: '生词', num: true }, { label: '≈课', num: true },
       '地点', ''],
      p.segments.map(s => h('tr', {}, [
        h('td', {}, [h('b', {}, [String(s.index)])]),
        h('td', { class: 'num dim' }, [String(s.scenes.length)]),
        h('td', { class: 'num' }, [String(s.lines)]),
        h('td', { class: 'num' }, [String(s.words)]),
        h('td', { class: 'num dim' }, [`${s.minutes}'`]),
        h('td', { class: 'num' }, [
          s.new_words === null ? '—' : String(s.new_words)]),
        h('td', { class: 'num dim' }, [
          s.est_lessons === null ? '—' : String(s.est_lessons)]),
        h('td', { class: 'dim' }, [s.locations.slice(0, 2).join(' · ')]),
        h('td', {}, [h('button', {
          class: 'act' + (S.openSeg === s.index ? ' on' : ''),
          onclick: () => openSegLines(s.index),
        }, [S.openSeg === s.index ? '收起' : '看详情'])]),
      ]))
    ),
    p.segments[0] && p.segments[0].new_words === null
      ? h('div', { class: 'note' }, [
          '生词量要先跑 scripts/friends_cefr.py <集> --llm --json',
        ])
      : h('div', { class: 'note' }, [
          `≈课 = 生词数 / ${p.words_per_lesson}（每节课的重点词数）。`
          + '这一栏是「这段能出几节课」的估算。',
        ]),
  ]));

  // 某一段的详细情况
  if (S.openSeg !== null) {
    out.push(S.segLines ? segDetail(S.segLines)
                        : h('section', { class: 'panel' }, ['读这一段…']));
  }

  // 每段起止：不展开台词也能核对切点
  out.push(panel('切点', '每段从哪句起、到哪句止。', [
    table(['段', '起', '止'], p.segments.map(s => h('tr', {}, [
      h('td', {}, [String(s.index)]),
      h('td', { class: 'dim' }, [s.first_line
        ? `${s.first_line.speaker}：${s.first_line.text}` : '—']),
      h('td', { class: 'dim' }, [s.last_line
        ? `${s.last_line.speaker}：${s.last_line.text}` : '—']),
    ]))),
  ]));

  // 段数对比是调参工具，收进折叠 —— 规则已经定了，它不该抢主位
  const cmp = S.compare;
  if (cmp && cmp.options.length) {
    out.push(panel('换个段数看看', '调参用。当前方案已定，这里只是对比。', [
      h('details', {}, [
        h('summary', {}, [`试切其它段数（当前 ${p.n} 段）`]),
        h('div', { class: 'row', style: 'margin:10px 0' }, [
          h('button', {
            class: 'act' + (S.segN === null ? ' on' : ''),
            onclick: () => loadSeg(S.script, null),
          }, [`当前方案（${p.saved ? '已定死' : '自动'}）`]),
          ...cmp.options.map(o => h('button', {
            class: 'act' + (S.segN === o.n ? ' on' : ''),
            onclick: () => loadSeg(S.script, o.n),
          }, [`${o.n} 段`])),
        ]),
        table(['段数', { label: '不均衡度', num: true }, '各段词数'],
          cmp.options.map(o => h('tr', {}, [
            h('td', {}, [`${o.n} 段`]),
            h('td', { class: 'num' }, [
              o.spread.toFixed(3),
              o.spread > 1.5 ? h('span', { class: 'tag bad' }, ['偏']) : null,
            ]),
            h('td', { class: 'dim' }, [o.words.join(' / ')]),
          ]))),
        h('div', { class: 'note' }, [
          '不均衡度 = 最大段词数 / 平均段词数，1.0 是完美均分。'
          + '这里试切不会改掉定死的方案。',
        ]),
      ]),
    ]));
  }

  out.push(panel('规则', '', [
    h('div', { class: 'note' }, [
      p.rule,
      h('br', {}),
      '逐字稿来源和切段规则由人给、走代码改；这一页只读。',
    ]),
  ]));
  return out;
}

// ---------- 教具 ----------

const INTERACTION_ZH = {
  quiz: '答题', shadow: '跟读', passive: '被动播放',
  assess: '自评', report: '报告',
};
const DIRECTION_ZH = { a2i: '音 → 意', i2a: '意 → 音', none: '—' };
const DOMAIN_ZH = { words: '词', chunks: '短语', sentences: '句子' };
const ASSET_ZH = {
  audio: '音频', audio_slow: '慢速音频', audio_clip: '原片切片',
  image: '配图', meaning_zh: '中文释义', text: '文本',
};

const domainsZh = ds => (ds.length ? ds.map(d => DOMAIN_ZH[d] || d).join(' / ') : '—');
const assetsZh = as => (as.length ? as.map(a => ASSET_ZH[a] || a).join('、') : '—');

function viewTools() {
  const tools = (S.tools && S.tools.tools) || [];
  return [
    panel('教具表', '一节课是由这些拼起来的。「需要素材」决定了内容完备度矩阵怎么算。', [
      table(
        ['教具', '交互', '方向', '适用', '需要素材', '计分', '说明'],
        tools.map(t => h('tr', {}, [
          h('td', {}, [h('b', {}, [t.name]), h('div', { class: 'dim' },
                        [h('code', {}, [t.id])])]),
          h('td', {}, [INTERACTION_ZH[t.interaction] || t.interaction]),
          h('td', {}, [DIRECTION_ZH[t.direction] || t.direction]),
          h('td', {}, [domainsZh(t.domains)]),
          h('td', {}, [assetsZh(t.needs)]),
          h('td', {}, [t.scored
            ? h('span', { class: 'tag ok' }, ['计 streak'])
            : h('span', { class: 'tag' }, ['不计'])]),
          h('td', { class: 'dim' }, [t.note || '']),
        ]))
      ),
    ]),
    panel('学习者看到的提示', '不露内部 id（§4）。这些文案由教具声明，前端只渲染。', [
      table(['教具', '提示'], tools.filter(t => t.hint).map(t => h('tr', {}, [
        h('td', {}, [t.name]),
        h('td', {}, [t.hint]),
      ]))),
    ]),
  ];
}

// ---------- 编排 ----------

const SOURCE_ZH = {
  focus: '本节正课', review: '跨节复习', spot: '抽检已会',
  mixed: '本节 + 复习', redo: '本节错题', single: '本节首句',
  all: '本节全部句子', none: '不需要教学点',
};

function viewArrangement() {
  const a = S.arrangement;
  if (!a) return [h('div', { class: 'empty' }, ['加载中…'])];
  return [
    panel(`${a.title || a.id}`,
      `${a.steps.length} 环节 · 合计 ${a.minutes} 分钟 · 版本 v${a.version}`, [
      a.note ? h('div', { class: 'note' }, [a.note]) : null,
      h('div', { class: 'step-list' }, a.steps.map(s => h('div', { class: 'step' }, [
        h('div', { class: 'idx' }, [String(s.index)]),
        h('div', {}, [
          h('div', { class: 'ttl' }, [s.title]),
          h('div', { class: 'meta' }, [
            `${s.tool_name} · ${SOURCE_ZH[s.source] || s.source}`,
            s.domains.length ? ` · ${domainsZh(s.domains)}` : '',
            s.needs.length ? ` · 需要 ${assetsZh(s.needs)}` : '',
          ]),
          s.note ? h('div', { class: 'meta' }, ['— ' + s.note]) : null,
        ]),
        h('div', { class: 'row' }, [
          s.first_touch ? h('span', { class: 'tag warn' }, ['首触']) : null,
          s.scored ? h('span', { class: 'tag ok' }, ['计分']) : null,
          h('span', { class: 'dim' }, [`${s.minutes} 分`]),
        ]),
      ]))),
    ]),
    panel('为什么编排带版本号', '', [
      h('div', { class: 'note' }, [
        '卡序是按编排确定性重建的：快照不存卡，只存重建输入。' +
        '改了编排再拿旧快照恢复，会重建出另一副牌 —— 续上错位，而且不报错。' +
        '所以快照记 id + version，不匹配就不许重建，前端提示重开。',
      ]),
    ]),
  ];
}

// ---------- 课程计划检查器 ----------

function viewPlan() {
  const p = S.plan;
  if (!p) return [h('div', { class: 'empty' }, ['加载中…'])];
  if (!p.user) {
    return [h('div', { class: 'empty' },
              ['还没有用户。先去学习者端建一个并勾选自评。'])];
  }

  const out = [];

  // 输入 1：自评
  if (p.assessment) {
    const c = p.assessment.counts;
    out.push(panel('输入 · 自评',
      '待学池是打包的唯一输入。这里数字不对，后面全不对。', [
      h('div', { class: 'grid3' }, ['words', 'chunks', 'sentences'].map(d =>
        h('div', {}, [
          h('div', { class: 'dim' }, [DOMAIN_ZH[d]]),
          h('div', { class: 'stat' }, [
            String(c[d].unknown),
            h('small', {}, [` / ${c[d].known + c[d].unknown} 待学`]),
          ]),
        ]))),
      h('div', { class: 'note' }, [
        `合计待学 ${p.assessment.total_unknown} 个教学点`,
      ]),
    ]));
  } else {
    out.push(panel('输入 · 自评', '', [
      h('div', { class: 'note bad' }, ['还没提交自评，plan 无从产生。']),
    ]));
  }

  // 输入 2：探测 + 动态挑选
  if (p.probe) {
    const cal = p.probe.calibration || {};
    const ans = p.probe.answers || {};
    const got = Object.values(ans).filter(Boolean).length;
    out.push(panel('输入 · 听力探测',
      'chunk / 句子的难度靠实测，不靠单词量推断。', [
      h('div', { class: 'row' }, [
        h('span', {}, [`抽测 ${Object.keys(ans).length} 条，听懂 ${got} 条`]),
        cal.confident === false
          ? h('span', { class: 'tag warn' }, ['样本不足，用了保守阈值'])
          : h('span', { class: 'tag ok' }, ['阈值可信']),
      ]),
      h('details', {}, [
        h('summary', {}, ['校准细节']),
        h('pre', { class: 'dim' }, [JSON.stringify(cal, null, 1)]),
      ]),
    ]));
  }
  if (p.selection) {
    out.push(panel('输入 · 动态挑选',
      `来源：${p.selection.source === 'probe' ? '探测结果' : '按词池启发式'}` +
      ' —— chunk / 句子不由用户逐条勾，按各人的待学词池现算。', [
      table(['层', '挑出', '样例'], ['chunks', 'sentences'].map(d => {
        const xs = p.selection[d] || [];
        return h('tr', {}, [
          h('td', {}, [DOMAIN_ZH[d]]),
          h('td', { class: 'num' }, [String(xs.length)]),
          h('td', { class: 'dim' }, [
            xs.slice(0, 3).map(x => x.text || x.id).join(' / ') || '—',
          ]),
        ]);
      })),
    ]));
  }

  // 输出：N 节课
  if (!p.plan) {
    out.push(panel('输出 · 课程表', '', [
      h('div', { class: 'note' }, ['还没打包。'])]));
    return out;
  }

  out.push(panel('输出 · 课程表',
    `${p.plan.n_lessons} 节`, [
    p.plan.fallback
      ? h('div', { class: 'note bad' }, [
          '⚠ ' + p.plan.fallback_hint +
          '。主题名会是「第N组 / 补充N」这种机械划分，需要重新打包。',
        ])
      : h('div', { class: 'note' }, ['LLM 场景聚类正常出结果。']),
    table(
      ['#', '主题', { label: '点数', num: true }, '词', '短语', '句子', '', ''],
      p.plan.lessons.map(l => h('tr', {}, [
        h('td', {}, [String(l.index)]),
        h('td', {}, [h('b', {}, [l.theme])]),
        h('td', { class: 'num' }, [
          String(l.n_points),
          l.n_points > 10 ? h('span', { class: 'tag bad' }, ['超'])
            : l.n_points < 5 ? h('span', { class: 'tag warn' }, ['少']) : null,
        ]),
        h('td', { class: 'dim' }, [l.labels.words.join(' ') || '—']),
        h('td', { class: 'dim' }, [l.labels.chunks.join(' / ') || '—']),
        h('td', { class: 'dim' }, [l.labels.sentences.join(' / ') || '—']),
        h('td', {}, [l.done ? h('span', { class: 'tag ok' }, ['已上']) : null]),
        h('td', {}, [h('button', {
          class: 'act', onclick: () => openCards(l.index),
        }, [S.openLesson === l.index ? '收起' : '看卡序'])]),
      ]))
    ),
  ]));

  if (S.openLesson !== null && S.cards[S.openLesson]) {
    out.push(cardsPanel(S.cards[S.openLesson]));
  }
  return out;
}

function cardsPanel(d) {
  const dirs = Object.entries(d.picked.directions || {});
  return panel(`第 ${d.index} 节展开 · ${d.theme}`,
    `${d.n_points} 个教学点 → ${d.total_cards} 张卡 · ` +
    `编排 ${d.arrangement.id} v${d.arrangement.version}`, [
    h('details', {}, [
      h('summary', {}, ['卡序的可变输入（这三项落盘，保证续上时能重建同一副牌）']),
      h('div', { class: 'note' }, [
        `复习：${JSON.stringify(d.picked.review)}`,
        h('br', {}),
        `抽检：${JSON.stringify(d.picked.spot)}`,
        h('br', {}),
        `方向：${dirs.length} 项已定死`,
      ]),
    ]),
    ...d.groups.filter(g => g.n_cards > 0).map(g => h('div', {}, [
      h('div', { class: 'row', style: 'margin:14px 0 6px' }, [
        h('b', {}, [`${g.step.index}. ${g.step.title}`]),
        h('span', { class: 'tag' }, [g.step.tool_name]),
        h('span', { class: 'dim' }, [`${g.n_cards} 张`]),
        g.step.scored ? h('span', { class: 'tag ok' }, ['计分']) : null,
      ]),
      table(['卡', '内容', '层', '方向', '选项', '素材'],
        g.cards.map(c => h('tr', {}, [
          h('td', {}, [h('code', {}, [c.card_id])]),
          h('td', {}, [
            c.label || h('span', { class: 'dim' }, ['—']),
            c.is_bonus ? h('span', { class: 'tag warn' }, ['顺带']) : null,
          ]),
          h('td', { class: 'dim' }, [DOMAIN_ZH[c.domain] || c.domain]),
          h('td', { class: 'dim' }, [DIRECTION_ZH[c.direction] || '—']),
          h('td', { class: 'num dim' }, [c.n_choices ? String(c.n_choices) : '—']),
          h('td', { class: 'dim' }, [
            [c.has_audio ? '音' : null, c.has_image ? '图' : null]
              .filter(Boolean).join(' + ') || '—',
          ]),
        ]))),
    ])),
  ]);
}

// ---------- 内容完备度 ----------

function viewAssets() {
  const d = S.completeness;
  if (!d) return [h('div', { class: 'empty' }, ['加载中…'])];

  const out = [
    panel(`${d.title}`,
      `教具声明需要什么素材，这里对上这一集实际有什么。` +
      (d.arrangement_only ? '当前只审编排 ' + d.arrangement + ' 真正用到的教具。' : ''), [
      h('div', { class: 'row' }, [
        h('button', {
          class: 'act' + (S.arrangementOnly ? ' on' : ''),
          onclick: () => { S.arrangementOnly = !S.arrangementOnly; loadAssets(); },
        }, [S.arrangementOnly ? '只看编排用到的教具' : '看全部教具']),
        h('span', { class: 'dim' }, [
          '报「短语缺配图」没意义 —— 编排里短语走听音选义，本来不用图。',
        ]),
      ]),
    ]),
  ];

  // 缺口优先：直接告诉教研下一步该生产什么
  out.push(panel('缺口',
    d.blockers.length ? '按缺得最多排序。这是内容生产的待办清单。' : '', [
    d.blockers.length
      ? table(['缺什么', { label: '影响条目', num: true }, '涉及层'],
          d.blockers.map(b => h('tr', {}, [
            h('td', {}, [h('b', {}, [b.label]), ' ', h('code', {}, [b.asset])]),
            h('td', { class: 'num' }, [String(b.count)]),
            h('td', { class: 'dim' }, [domainsZh(b.domains)]),
          ])))
      : h('div', { class: 'note' }, ['素材齐全，所有教具都能跑。']),
  ]));

  // 每层的可教比例
  out.push(panel('各层完备度', '', [
    h('div', { class: 'grid3' }, ['words', 'chunks', 'sentences'].map(dom => {
      const x = d.domains[dom];
      if (!x) return null;
      const bad = x.ready < x.total;
      return h('div', {}, [
        h('div', { class: 'dim' }, [DOMAIN_ZH[dom]]),
        h('div', { class: 'stat' + (bad ? ' bad' : '') }, [
          `${x.ready}`, h('small', {}, [` / ${x.total} 可教`]),
        ]),
        bar(x.ready, x.total),
      ]);
    })),
  ]));

  // 矩阵：教具 × 层
  const tools = d.tools;
  out.push(panel('矩阵', '每件教具在每层上能跑多少条。', [
    table(['教具', ...['words', 'chunks', 'sentences'].map(x => DOMAIN_ZH[x])],
      tools.map(t => h('tr', {}, [
        h('td', {}, [h('b', {}, [t.name])]),
        ...['words', 'chunks', 'sentences'].map(dom => {
          const bt = d.domains[dom] && d.domains[dom].by_tool[t.id];
          if (!bt) return h('td', { class: 'dim' }, ['—']);
          const total = bt.ok + bt.missing;
          return h('td', {}, [
            h('span', { class: 'tag ' + (bt.missing ? 'bad' : 'ok') },
              [`${bt.ok} / ${total}`]),
          ]);
        }),
      ]))),
  ]));

  // 缺素材的条目明细
  for (const dom of ['words', 'chunks', 'sentences']) {
    const x = d.domains[dom];
    if (!x) continue;
    const bad = x.items.filter(i => !i.ready);
    if (!bad.length) continue;
    out.push(panel(`${DOMAIN_ZH[dom]} · ${bad.length} 条缺素材`, '', [
      h('details', {}, [
        h('summary', {}, ['展开明细']),
        table(['条目', '缺什么', '仍能跑'],
          bad.slice(0, 200).map(i => h('tr', {}, [
            h('td', {}, [i.label, i.skip_image
              ? h('span', { class: 'tag' }, ['有意不配图']) : null]),
            h('td', {}, Object.entries(i.blocked_zh).map(
              ([tid, zh]) => h('div', { class: 'dim' }, [
                `${toolName(tid)}：缺 ${zh}`,
              ]))),
            h('td', { class: 'dim' }, [
              i.runnable.map(toolName).join('、') || '—',
            ]),
          ]))),
      ]),
    ]));
  }
  return out;
}

function toolName(tid) {
  const t = ((S.completeness && S.completeness.tools) || [])
    .concat((S.tools && S.tools.tools) || []).find(x => x.id === tid);
  return t ? t.name : tid;
}

// ---------- 装配 ----------

function render() {
  const cur = backendOf(S.view);

  // 一级：三个后台
  nav.innerHTML = '';
  for (const [bid, label] of BACKENDS) {
    nav.appendChild(h('button', {
      class: cur === bid ? 'on' : '',
      onclick: () => go(PAGES_OF[bid][0]),
    }, [label]));
  }

  // 右上角只显示当前后台的作用域 —— 看剧本时不该挂着学习者和编排版本
  headRight.innerHTML = '';
  headRight.appendChild(h('span', {}, [scopeText(cur)]));

  // 二级：当前后台里的页面。只有一页就不铺
  sub.innerHTML = '';
  const pages = (BACKENDS.find(b => b[0] === cur) || [])[3] || [];
  if (pages.length > 1) {
    for (const [pid, label] of pages) {
      sub.appendChild(h('button', {
        class: S.view === pid ? 'on' : '',
        onclick: () => go(pid),
      }, [label]));
    }
  }
  sub.classList.toggle('empty-row', pages.length <= 1);

  app.innerHTML = '';
  if (S.err) {
    app.appendChild(h('section', { class: 'panel' }, [
      h('div', { class: 'note bad' }, ['出错了：' + S.err]),
    ]));
    return;
  }
  const v = { scripts: viewScripts, assets: viewAssets, plan: viewPlan,
              tools: viewTools, arrangement: viewArrangement }[S.view];
  for (const node of v()) app.appendChild(node);
}

async function go(view, fromHash = false) {
  S.view = view;
  S.err = null;
  // 每块要能直达和分享，所以视图进 hash（#tools / #arrangement / #plan / #content）
  const path = `${backendOf(view)}/${view}`;
  if (!fromHash && decodeURIComponent(location.hash.slice(1)) !== path) {
    location.hash = path;
  }
  render();
  try {
    if (view === 'scripts' && !S.scripts) {
      S.scripts = await get('/api/content/scripts');
    }
    if (view === 'tools' && !S.tools) S.tools = await get('/api/admin/tools');
    if (view === 'arrangement' && !S.arrangement) {
      S.arrangement = await get('/api/admin/arrangement');
    }
    if (view === 'plan') S.plan = await get('/api/admin/plan');
    if (view === 'assets' && !S.completeness) await loadAssets();
  } catch (e) {
    // 没选用户时 plan 返回 409，那不是错误，是「还没到那一步」
    S.err = e.message.includes('还没有用户') ? null : e.message;
    if (!S.err && view === 'plan') S.plan = { user: null };
  }
  render();
}

async function loadAssets() {
  S.completeness = null;
  render();
  S.completeness = await get(
    '/api/content/completeness?arrangement_only=' + S.arrangementOnly);
  if (!S.tools) S.tools = await get('/api/admin/tools');
  render();
}

async function openScript(id) {
  if (S.script === id) {
    S.script = S.chunks = S.segPlan = S.compare = S.segLines = null;
    S.openSeg = null;
    render();
    return;
  }
  S.script = id;
  S.chunks = S.segPlan = S.compare = S.segLines = null;
  S.openSeg = null;
  S.segN = null;
  render();
  try {
    S.chunks = await get(`/api/content/scripts/${id}/chunks`);
    render();
    S.compare = await get(`/api/content/scripts/${id}/compare?lo=3&hi=7`);
    await loadSeg(id, null);
  } catch (e) {
    S.err = e.message;
    render();
  }
}

async function loadSeg(id, n) {
  S.segN = n;
  S.segPlan = null;
  S.segLines = null;
  S.openSeg = null;
  render();
  const q = n === null ? '' : `?n=${n}`;
  S.segPlan = await get(`/api/content/scripts/${id}/segments${q}`);
  render();
}

async function openSegLines(index) {
  if (S.openSeg === index) { S.openSeg = null; S.segLines = null; render(); return; }
  S.openSeg = index;
  S.segLines = null;
  render();
  S.segLines = await get(
    `/api/content/scripts/${S.script}/segments/${index}/lines`);
  render();
}

async function openCards(index) {
  if (S.openLesson === index) { S.openLesson = null; render(); return; }
  S.openLesson = index;
  render();
  if (!S.cards[index]) {
    S.cards[index] = await get(`/api/admin/plan/${index}/cards`);
  }
  render();
}

function viewFromHash() {
  // 支持 #research/scripts 和 #scripts 两种写法
  const raw = decodeURIComponent(location.hash.slice(1));
  const page = raw.includes('/') ? raw.split('/')[1] : raw;
  if (ALL_PAGES.includes(page)) return page;
  // 只给了后台名就进它的第一页
  if (PAGES_OF[page]) return PAGES_OF[page][0];
  return 'scripts';
}

window.addEventListener('hashchange', () => {
  const v = viewFromHash();
  if (v !== S.view) go(v, true);
});

(async () => {
  try {
    S.status = await get('/api/status');
  } catch (e) {
    S.err = e.message;
  }
  await go(viewFromHash(), true);
})();
