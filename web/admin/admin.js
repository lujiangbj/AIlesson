// AIlesson 后台。三个视图：教具 / 编排 / 课程计划 / 内容完备度。
// 只读 —— 这里是拿来看和判断的，改配置走代码（编排改了要升 version）。
'use strict';

const app = document.getElementById('app');
const nav = document.getElementById('nav');
const headRight = document.getElementById('head-right');

const S = {
  view: 'tools',
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

const VIEWS = [
  ['tools', '教具'],
  ['arrangement', '编排'],
  ['plan', '课程计划'],
  ['content', '内容完备度'],
];

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

function panel(title, sub, kids) {
  return h('section', { class: 'panel' }, [
    h('h2', {}, [title]),
    sub ? h('div', { class: 'sub' }, [sub]) : null,
    ...kids,
  ]);
}

function table(headers, rows) {
  return h('table', {}, [
    h('thead', {}, [h('tr', {}, headers.map(
      x => h('th', { class: x.num ? 'num' : '' }, [x.label ?? x])))]),
    h('tbody', {}, rows),
  ]);
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

function viewContent() {
  const d = S.completeness;
  if (!d) return [h('div', { class: 'empty' }, ['加载中…'])];

  const out = [
    panel(`${d.title}`,
      `教具声明需要什么素材，这里对上这一集实际有什么。` +
      (d.arrangement_only ? '当前只审编排 ' + d.arrangement + ' 真正用到的教具。' : ''), [
      h('div', { class: 'row' }, [
        h('button', {
          class: 'act' + (S.arrangementOnly ? ' on' : ''),
          onclick: () => { S.arrangementOnly = !S.arrangementOnly; loadContent(); },
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
  nav.innerHTML = '';
  for (const [id, label] of VIEWS) {
    nav.appendChild(h('button', {
      class: S.view === id ? 'on' : '',
      onclick: () => go(id),
    }, [label]));
  }

  headRight.innerHTML = '';
  if (S.status) {
    const bits = [S.status.episode.title];
    if (S.status.user) bits.push(S.status.user.name);
    if (S.status.arrangement) {
      bits.push(`${S.status.arrangement.id} v${S.status.arrangement.version}`);
    }
    headRight.appendChild(h('span', {}, [bits.join(' · ')]));
  }

  app.innerHTML = '';
  if (S.err) {
    app.appendChild(h('section', { class: 'panel' }, [
      h('div', { class: 'note bad' }, ['出错了：' + S.err]),
    ]));
    return;
  }
  const v = { tools: viewTools, arrangement: viewArrangement,
              plan: viewPlan, content: viewContent }[S.view];
  for (const node of v()) app.appendChild(node);
}

async function go(view, fromHash = false) {
  S.view = view;
  S.err = null;
  // 每块要能直达和分享，所以视图进 hash（#tools / #arrangement / #plan / #content）
  if (!fromHash && location.hash.slice(1) !== view) location.hash = view;
  render();
  try {
    if (view === 'tools' && !S.tools) S.tools = await get('/api/admin/tools');
    if (view === 'arrangement' && !S.arrangement) {
      S.arrangement = await get('/api/admin/arrangement');
    }
    if (view === 'plan') S.plan = await get('/api/admin/plan');
    if (view === 'content' && !S.completeness) await loadContent();
  } catch (e) {
    // 没选用户时 plan 返回 409，那不是错误，是「还没到那一步」
    S.err = e.message.includes('还没有用户') ? null : e.message;
    if (!S.err && view === 'plan') S.plan = { user: null };
  }
  render();
}

async function loadContent() {
  S.completeness = null;
  render();
  S.completeness = await get(
    '/api/content/completeness?arrangement_only=' + S.arrangementOnly);
  if (!S.tools) S.tools = await get('/api/admin/tools');
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

const VIEW_IDS = VIEWS.map(([id]) => id);

function viewFromHash() {
  const h = location.hash.slice(1);
  return VIEW_IDS.includes(h) ? h : 'tools';
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
