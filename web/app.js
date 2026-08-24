// AIlesson 前端。状态在服务端，这里只渲染 + 上报。
// 主答题路径全本地判定（correct 由 choice === correct_id 得出），零网络等待；
// Tutor 讲解异步取，藏在「看正确答案」的间隙里。

const app = document.getElementById('app');
const au = document.getElementById('au');

// 只勾单词。短语和句子的掌握度改成听力探测实测——认识 crash/on/couch
// 每个词也听不懂 "You gonna crash on the couch?"，难点在习语和连读，
// 拿单词掌握度推断是猜；逐条勾 114 个短语又太累。
const WORD_HINT = '听到就能反应出意思 = 会';

const S = {
  view: 'home',      // users | home | check | probe | lessons | card | report
  users: null,       // {current_id, users[]}
  status: null,
  checklist: null,
  known: { words: new Set() },
  probe: null,       // {items[], total_items}
  probeAt: 0,        // 当前探测到第几条
  probeAns: {},      // id -> 是否听懂
  probeResult: null,
  packing: false,
  card: null,
  report: null,
  picked: null,
  correct: null,
  tutorLine: '',
  narrow: null,
  stuckTimer: null,
  busy: false,
  muted: false,       // 常驻静音开关（FR-6.2）
  lessonIndex: null,  // 当前课堂是课程表的第几节
};

// ---------- 工具 ----------

const h = (tag, props = {}, kids = []) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === 'class') e.className = v;
    else if (k === 'html') e.innerHTML = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2).toLowerCase(), v);
    else if (v !== null && v !== undefined) e.setAttribute(k, v);
  }
  const flat = [];
  (function walk(xs) {
    for (const x of xs) {
      if (Array.isArray(x)) walk(x);
      else if (x !== null && x !== undefined && x !== false) flat.push(x);
    }
  })(kids);
  for (const c of flat) {
    e.appendChild(typeof c === 'object' ? c : document.createTextNode(String(c)));
  }
  return e;
};

// ---------- 图标（描边 SVG，currentColor 继承文字色） ----------
const ICONS = {
  spk: '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M11 5 6.6 8.6H3.2v6.8h3.4L11 19z" fill="currentColor" stroke="none"/><path class="w1" d="M14.8 9.3a4.2 4.2 0 0 1 0 5.4"/><path class="w2" d="M17.3 6.8a8 8 0 0 1 0 10.4"/></svg>',
  spkx: '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M11 5 6.6 8.6H3.2v6.8h3.4L11 19z" fill="currentColor" stroke="none"/><path d="m15.5 9.5 5 5"/><path d="m20.5 9.5-5 5"/></svg>',
  film: '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M8 5v14M16 5v14M3 9.5h5M3 14.5h5M16 9.5h5M16 14.5h5"/></svg>',
  mic: '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="2.5" width="6" height="11" rx="3"/><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0"/><path d="M12 18v3.5"/></svg>',
  slow: '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="13" r="8"/><path d="M12 13l3.8-3.8"/><path d="M9.5 2.5h5"/></svg>',
  rotate: '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="7.5" y="2.5" width="9" height="19" rx="2.5"/><path d="M11 18.5h2"/></svg>',
  cap: '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m2.5 8.8 9.5-4.3 9.5 4.3-9.5 4.3z"/><path d="M6.8 11.4v4.4c0 1.6 2.3 2.9 5.2 2.9s5.2-1.3 5.2-2.9v-4.4"/><path d="M21.5 8.8v5.2"/></svg>',
  flag: '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5.5 21.5v-17"/><path d="M5.5 4.5h12.8l-2.6 3.75 2.6 3.75H5.5"/></svg>',
  check: '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="m4.5 12.5 5 5 10-11"/></svg>',
  cross: '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="m6 6 12 12"/><path d="m18 6-12 12"/></svg>',
  back: '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m14.5 6-6 6 6 6"/></svg>',
};
function ic(name) {
  const t = document.createElement('template');
  t.innerHTML = ICONS[name];
  return t.content.firstChild;
}

function toast(msg, ms = 1600) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove('show'), ms);
}

async function api(path, body, method) {
  const opt = { method: method || (body !== undefined ? 'POST' : 'GET') };
  if (body !== undefined) {
    opt.headers = { 'Content-Type': 'application/json' };
    opt.body = JSON.stringify(body);
  }
  const r = await fetch(path, opt);
  if (!r.ok) {
    let msg = 'HTTP ' + r.status;
    try { msg = (await r.json()).detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  return r.json();
}

let playing = null;
function play(src, btn) {
  if (!src) return;
  au.pause();
  if (playing) playing.classList.remove('is-playing');
  au.src = src;
  au.currentTime = 0;
  if (btn) { btn.classList.add('is-playing'); playing = btn; }
  au.onended = () => btn && btn.classList.remove('is-playing');
  au.play().catch(() => btn && btn.classList.remove('is-playing'));
}

function toggleMute() {
  S.muted = !S.muted;
  au.muted = S.muted;
  try { localStorage.setItem('ailesson.muted', S.muted ? '1' : '0'); } catch (_) {}
  render();
  toast(S.muted ? '已静音，课程照常进行' : '已恢复声音');
}

async function playSeq(srcs, btn) {
  if (btn) btn.classList.add('is-playing');
  for (const s of srcs) {
    await new Promise(res => {
      au.src = s; au.currentTime = 0;
      au.onended = res; au.onerror = res;
      au.play().catch(res);
    });
  }
  if (btn) btn.classList.remove('is-playing');
}

// ---------- 渲染 ----------

function render() {
  const y = window.scrollY;
  app.innerHTML = '';
  if (S.status && S.status.episode && S.status.episode.title) {
    document.title = 'AIlesson · ' + S.status.episode.title;
  }
  const v = { users: viewUsers, home: viewHome, check: viewCheck,
              probe: viewProbe, lessons: viewLessons, card: viewCard,
              report: viewReport }[S.view];
  app.appendChild(v());
  document.body.classList.toggle('checking', S.view === 'check' && !S.packing);
  document.body.classList.toggle('classing', S.view === 'card');
  if (S.view === 'check') window.scrollTo(0, y);
}

// 当前用户条：除了用户页和上课中，各页顶部都显示，方便随时切
function userBar() {
  const cur = S.status && S.status.user;
  if (!cur) return null;
  return h('div', { class: 'ubar' }, [
    h('span', { class: 'who' }, [h('span', { class: 'ava' }, [cur.name.slice(0, 1)]), cur.name]),
    h('button', { class: 'mini ghost', onclick: openUsers }, ['切换用户']),
  ]);
}

function viewUsers() {
  const u = S.users;
  if (!u) return h('div', { class: 'card' }, ['加载中…']);

  const box = h('div', {}, [
    h('h1', {}, ['选择用户']),
    h('div', { class: 'dim' }, [
      '每个人的词表、掌握度、课堂数据都是独立的。MVP 阶段不设密码。',
    ]),
  ]);

  if (u.users.length === 0) {
    box.appendChild(h('div', { class: 'dim', style: 'margin:16px 0' },
      ['还没有用户，先创建一个 ↓']));
  }

  for (const x of u.users) {
    const isCur = x.id === u.current_id;
    box.appendChild(h('div', {
      class: 'lesson user' + (isCur ? ' cur' : ''),
      onclick: () => selectUser(x.id),
    }, [
      h('div', { class: 'n' }, [x.name.slice(0, 1)]),
      h('div', { class: 'body' }, [
        h('div', { class: 'theme' }, [x.name, isCur ? h('span', { class: 'chip ok', style: 'margin-left:8px' }, ['当前']) : null]),
        h('div', { class: 'words' }, [
          `已上 ${x.lessons_done} 节`,
          x.last_active ? ` · 最近 ${new Date(x.last_active * 1000).toLocaleDateString()}` : '',
        ]),
      ]),
      h('button', {
        class: 'mini danger',
        onclick: e => { e.stopPropagation(); deleteUser(x); },
      }, ['删除']),
    ]));
  }

  box.appendChild(h('div', { class: 'card', style: 'margin-top:14px' }, [
    h('h2', {}, ['新建用户']),
    h('div', { class: 'row', style: 'gap:8px' }, [
      h('input', { id: 'newname', class: 'inp', placeholder: '名字',
                   onkeydown: e => { if (e.key === 'Enter') createUser(); } }),
      h('button', { class: 'primary', onclick: createUser }, ['创建']),
    ]),
  ]));

  if (u.current_id) {
    box.appendChild(h('button', {
      class: 'ghost wide', style: 'margin-top:10px',
      onclick: async () => { await refreshStatus(); gotoMain(); },
    }, ['进入学习 →']));
  }
  return box;
}

function viewHome() {
  const st = S.status;
  if (!st) return h('div', { class: 'card' }, ['加载中…']);
  const ep = st.episode;

  const box = h('div', {}, [
    userBar(),
    h('div', { class: 'hero' }, [
      h('div', { class: 'eyebrow' }, ['影视英语课 · Episode']),
      h('h1', {}, [`《${ep.title}》`]),
      h('div', { class: 'dim' },
        [`${ep.words} 个词 · ${ep.chunks} 个短语 · ${ep.sentences} 个句子`]),
    ]),
  ]);

  if (!st.assessed) {
    box.appendChild(h('div', { class: 'card', style: 'margin-top:14px' }, [
      h('h2', {}, ['先勾掉你已经会的']),
      h('div', { class: 'dim' }, [
        '词、短语、句子三层分别勾。剩下的才是这几节课要教的。',
      ]),
      h('div', { class: 'fb', style: 'margin-top:12px' }, [
        h('div', {}, ['⚠️ 关键：三层的「会」标准不一样']),
        h('div', { class: 'dim', style: 'margin-top:6px' }, [
          '单词认识 ≠ 短语会说。你可能认识 only / mess / goodness 每个词，' +
          '但未必能张口说出 "it\'s only mud" 或 "look at the mess you\'re in"。' +
          '**能说出来才勾**，否则就留着学。',
        ]),
      ]),
      h('button', { class: 'primary big wide', style: 'margin-top:14px',
                    onclick: openChecklist }, ['打开清单 →']),
    ]));
  } else {
    const a = st.assessment;
    box.appendChild(h('div', { class: 'card', style: 'margin-top:14px' }, [
      h('h2', {}, ['勾选结果']),
      h('table', { class: 'tbl' }, [
        h('tr', {}, [h('th', {}, ['']), h('th', {}, ['已会']), h('th', {}, ['要学'])]),
        ...[['words', '词'], ['chunks', '短语'], ['sentences', '句子']]
          .map(([k, name]) => h('tr', {}, [
            h('td', {}, [name]),
            h('td', { class: 'ok' }, [String(a.known[k] || 0)]),
            h('td', { class: 'bad' }, [String(a.unknown[k] || 0)]),
          ])),
      ]),
      h('div', { class: 'dim', style: 'margin-top:10px' }, [
        `共 ${a.total_unknown} 个教学点 → ${(st.lessons || []).length} 节课`,
      ]),
      h('div', { class: 'row', style: 'margin-top:12px;gap:8px' }, [
        h('button', { class: 'primary', style: 'flex:1',
                      onclick: () => { S.view = 'lessons'; render(); } }, ['看课程表']),
        h('button', { class: 'ghost', onclick: openChecklist }, ['重新勾']),
      ]),
    ]));
  }

  box.appendChild(h('button', {
    class: 'ghost wide', style: 'margin-top:8px',
    onclick: async () => {
      if (!confirm('清掉学习进度？（LLM 缓存保留）')) return;
      S.status = await api('/api/reset', {});
      S.known = { words: new Set(), chunks: new Set(), sentences: new Set() };
      S.view = 'home'; render(); toast('已重置');
    },
  }, ['重置进度']));
  return box;
}

function viewCheck() {
  const cl = S.checklist;
  if (!cl) return h('div', { class: 'card' },
    [h('span', { class: 'spin' }), ' 正在给单词分组…']);

  const total = cl.words.total;
  const unknown = total - S.known.words.size;

  const box = h('div', {}, [
    h('div', { class: 'row between' }, [
      h('h1', {}, ['勾掉你会的单词']),
      h('button', { class: 'ghost', onclick: () => { S.view = 'home'; render(); } }, ['←']),
    ]),
    h('div', { class: 'dim', style: 'margin:10px 0 4px' }, [
      WORD_HINT, ' · 勾完下一步做听力测试，短语和句子不用手动勾',
    ]),
  ]);

  for (const g of cl.words.groups) {
    const allOn = g.items.every(x => S.known.words.has(x.id));
    box.appendChild(h('div', { class: 'grp-head' }, [
      h('span', {}, [g.title, h('span', { class: 'dim' }, [` ${g.items.length}`])]),
      h('button', {
        class: 'mini',
        onclick: () => {
          for (const x of g.items) {
            if (allOn) S.known.words.delete(x.id); else S.known.words.add(x.id);
          }
          render();
        },
      }, [allOn ? '全不会' : '整组都会']),
    ]));
    box.appendChild(h('div', { class: 'wgrid' },
      g.items.map(x => h('div', {
        class: 'wcard' + (S.known.words.has(x.id) ? ' on' : ''),
        onclick: e => {
          if (e.target.closest('.spk')) return;
          const set = S.known.words;
          if (set.has(x.id)) set.delete(x.id); else set.add(x.id);
          render();
        },
      }, [
        h('div', { class: 'wtop' }, [
          h('span', { class: 'lemma' }, [x.label]),
          h('span', { class: 'spk', onclick: () => play(x.audio) }, [ic('spk')]),
        ]),
        h('div', { class: 'zh2' }, [x.zh]),
        h('span', { class: 'tick' }, ['✓']),
      ]))));
  }

  box.appendChild(h('div', { class: 'sticky' }, [
    h('div', { class: 'row between', style: 'margin-bottom:8px;font-size:13px' }, [
      h('span', {}, ['不会的单词 ', h('b', {}, [String(unknown)])]),
      h('span', { class: 'dim' }, [`已会 ${S.known.words.size}/${total}`]),
    ]),
    h('button', { class: 'primary wide big', onclick: startProbe },
      ['下一步：听力测试（约 16 题）→']),
  ]));
  return box;
}

// ---- 听力探测：实测短语和句子的掌握度 ----

function viewProbe() {
  if (S.packing) {
    return h('div', { class: 'card' }, [
      h('h2', {}, ['正在安排课程']),
      h('div', { style: 'margin-top:12px' }, [
        h('span', { class: 'spin' }),
        ' AI 在按场景把待学内容分成几节课，首次要 1~3 分钟',
      ]),
    ]);
  }
  if (!S.probe) return h('div', { class: 'card' },
    [h('span', { class: 'spin' }), ' 正在挑测试题…']);

  const items = S.probe.items;
  const i = S.probeAt;

  if (i >= items.length) {
    const r = S.probeResult;
    return h('div', {}, [
      h('h1', {}, ['听力测试完成']),
      h('div', { class: 'card' }, [
        h('div', {}, [`听懂 ${Object.values(S.probeAns).filter(Boolean).length}`
          + ` / ${items.length} 题`]),
        r ? h('div', { class: 'dim', style: 'margin-top:8px' }, [
          `按你的水平，要练 ${r.unknown_chunks} 个短语、`
          + `${r.unknown_sentences} 个句子`,
        ]) : h('div', { style: 'margin-top:8px' },
          [h('span', { class: 'spin' }), ' 正在推算…']),
      ]),
      r ? h('button', { class: 'primary wide big', onclick: submitChecklist },
        ['安排课程 →']) : null,
    ]);
  }

  const it = items[i];
  const kindName = it.kind === 'chunk' ? '短语' : '句子';
  const answer = ok => {
    S.probeAns[it.id] = ok;
    S.probeAt += 1;
    render();
    if (S.probeAt < items.length) play(items[S.probeAt].audio);
    else finishProbe();
  };

  return h('div', {}, [
    h('div', { class: 'row between' }, [
      h('h1', {}, ['听力测试']),
      h('span', { class: 'dim' }, [`${i + 1} / ${items.length}`]),
    ]),
    h('div', { class: 'dim', style: 'margin:4px 0 12px' }, [
      '听一遍，判断自己听懂了没有。不显示文字——看到字就成了阅读题',
    ]),
    h('div', { class: 'card', style: 'text-align:center;padding:26px 16px 22px' }, [
      h('div', { class: 'eyebrow', style: 'margin-bottom:0' }, [kindName]),
      h('button', {
        class: 'disc', title: '再听一次',
        onclick: () => play(it.audio),
      }, [ic('spk')]),
      h('div', { class: 'dim' }, ['没听清就再点一次']),
    ]),
    h('div', { class: 'row', style: 'gap:10px;margin-top:14px' }, [
      h('button', { class: 'primary wide big', onclick: () => answer(true) },
        ['听懂了']),
      h('button', { class: 'ghost wide big', onclick: () => answer(false) },
        ['没听懂']),
    ]),
  ]);
}

async function startProbe() {
  S.view = 'probe';
  S.probe = null;
  S.probeAt = 0;
  S.probeAns = {};
  S.probeResult = null;
  render();
  S.probe = await api('/api/probe/start',
    { words: [...S.known.words], n: 16 });
  render();
  if (S.probe.items.length) play(S.probe.items[0].audio);
}

async function finishProbe() {
  S.probeResult = await api('/api/probe/submit', { answers: S.probeAns });
  render();
}

function viewLessons() {
  const st = S.status;
  const lessons = st.lessons || [];
  return h('div', {}, [
    userBar(),
    h('div', { class: 'row between' }, [
      h('h1', {}, ['课程表']),
      h('button', { class: 'ghost', onclick: () => { S.view = 'home'; render(); } }, ['←']),
    ]),
    h('div', { class: 'dim', style: 'margin-bottom:12px' }, [
      `${lessons.length} 节 · 每节约 30 分钟 · 已完成 ${st.completed_lessons.length} 节`,
    ]),
    // LLM 分组失败时明说，别让人对着"第1组/补充N"的机械课表纳闷
    (st.plan && st.plan.fallback) ? h('div', { class: 'card' }, [
      h('div', {}, ['⚠ AI 分组失败，这份课表是机械划分的（主题名是占位符）']),
      h('button', { class: 'mini', style: 'margin-top:8px', onclick: repack },
        ['重新安排']),
    ]) : null,
    ...lessons.map(l => {
      const paused = st.lesson_progress && st.lesson_progress.index === l.index;
      return h('div', {
        class: 'lesson' + (l.done ? ' done' : ''),
        onclick: () => startLesson(l.index),
      }, [
        h('div', { class: 'n' }, [l.done ? '✓' : l.index]),
        h('div', { class: 'body' }, [
          h('div', { class: 'theme' }, [
            l.theme,
            // total 只在课上着的时候有（快照里不存牌数，牌是恢复时重建的）
            paused && !l.done ? h('span', { class: 'resume-hint' }, [
              ` 进行到第 ${st.lesson_progress.cursor + 1} 张`
              + (st.lesson_progress.total ? `/${st.lesson_progress.total}` : '')
              + '，点这里继续',
            ]) : null,
          ]),
          l.words.length ? h('div', { class: 'words' },
            ['词 ', l.words.join(' · ')]) : null,
          ...l.chunks.map(c => h('div', { class: 'words q' }, ['“', c, '”'])),
          ...l.sentences.map(s => h('div', { class: 'words q' }, ['“', s, '”'])),
        ]),
      ]);
    }),
  ]);
}

async function repack() {
  S.view = 'probe'; S.packing = true; render();
  try {
    S.status = await api('/api/checklist/submit',
      { words: [...S.known.words], chunks: [], sentences: [] });
    S.packing = false; S.view = 'lessons'; render();
  } catch (e) {
    S.packing = false; S.view = 'lessons'; render();
    toast('重排失败：' + e.message, 3000);
  }
}

// 渲染注册表：按**交互形态**分派，不按教具。教室框架（顶栏 / 进度条 /
// Stage / Dock）恒定，加一件教具只要它复用已有形态就不用动前端；
// 只有新增形态才需要在这里注册。
// 教具名和操作提示由后端教具表给（c.tool_name / c.step.hint），前端不再自己拼。
const CARDS = {
  quiz: cardQuiz, shadow: cardShadow, passive: cardPassive,
  assess: cardAssess, report: cardReport,
};

function lessonTitle() {
  const idx = S.lessonIndex;
  const lesson = S.status && S.status.lessons
    && S.status.lessons.find(l => l.index === idx);
  return (lesson && lesson.theme) ? lesson.theme : '';
}

// ---- 教室框架：三区恒定，卡型只管填 Stage 和 Dock ----

function clsTop(c) {
  const idx = S.lessonIndex;
  const stats = c.stats || { correct: 0, wrong: 0 };
  const seg = c.step || { index: 16, title: '收尾报告', minutes: 1 };
  return h('header', { class: 'cls-top' }, [
    h('button', {
      class: 'exit-btn', title: '保存进度并退出课堂',
      onclick: pauseLesson,
    }, [ic('back'), '退出']),
    h('div', { class: 'cls-title' }, [
      h('div', { class: 'cls-lesson' }, [
        idx ? `第 ${idx} 节` : '课堂',
        lessonTitle() ? ' · ' + lessonTitle() : '',
      ]),
      h('div', { class: 'cls-seg' }, [
        seg.title, ` · 环节 ${seg.index}/16 · 约 ${seg.minutes} 分钟`,
      ]),
    ]),
    h('div', { class: 'cls-right' }, [
      h('span', { class: 'cls-stats' }, [
        h('b', { class: 'ok' }, ['✓ ' + stats.correct]),
        h('b', { class: 'bad' }, ['✗ ' + stats.wrong]),
      ]),
      h('button', {
        class: 'icon-btn', title: S.muted ? '取消静音' : '静音',
        onclick: toggleMute,
      }, [ic(S.muted ? 'spkx' : 'spk')]),
    ]),
  ]);
}

function clsProgress(c) {
  const cursor = c.cursor || 0;
  const total = c.total || cursor + 1;
  const pct = total ? Math.min(100, Math.round(cursor / total * 100)) : 0;
  return h('div', { class: 'cls-progress' }, [
    h('div', { class: 'cls-bar' }, [h('i', { style: `width:${pct}%` })]),
    h('span', { class: 'cls-count' }, [`${Math.min(cursor + 1, total)}/${total}`]),
  ]);
}

// 底部 Dock：恒定第三区。未答 = 操作提示；已答 = 反馈 + 「继续」。
// 主行动按钮永远在右下角同一个位置，不随卡型漂移。
function dockHint(text) {
  return h('div', { class: 'cls-dock' }, [h('div', { class: 'dock-hint' }, [text])]);
}

function dockFeedback(label) {
  return h('div', { class: 'cls-dock ' + (S.correct ? 'ok' : 'bad') }, [
    h('div', { class: 'dock-fb' }, [
      h('div', { class: 'dock-fb-icon' }, [ic(S.correct ? 'check' : 'cross')]),
      h('div', { class: 'dock-fb-body' }, [
        h('div', { class: 'dock-fb-title' }, [S.correct ? '对了' : '正确答案']),
        h('div', { class: 'dock-fb-line' }, [label]),
        h('div', { class: 'dock-fb-tutor' },
          [S.tutorLine || h('span', {}, [h('span', { class: 'spin' }), ' 老师在说…'])]),
      ]),
    ]),
    h('button', { class: 'primary dock-next', onclick: submit }, ['继续 →']),
  ]);
}

function dockActions(buttons) {
  return h('div', { class: 'cls-dock' }, [h('div', { class: 'dock-actions' }, buttons)]);
}

function lessonShell(c, parts) {
  return h('div', { class: 'lesson-stage' }, [
    clsTop(c),
    clsProgress(c),
    h('main', { class: 'cls-stage' }, [parts.stage]),
    parts.dock,
    // 竖屏 iPad 给旋转提示，而不是硬塞一个长条页面
    h('div', { class: 'rotate-guard' }, [
      h('div', {}, [
        h('div', { class: 'rotate-ico' }, [ic('rotate')]),
        h('div', { class: 'rotate-title' }, ['请把设备横过来']),
        h('div', { class: 'rotate-sub' }, ['课程是横屏课堂界面，横过来上课更舒服']),
      ]),
    ]),
  ]);
}

async function backToLessons() {
  try { S.status = await api('/api/status'); } catch (_) {}
  S.card = null; resetCardState(); S.lessonIndex = null;
  S.view = 'lessons'; render();
}

function finishedParts() {
  return {
    stage: h('section', { class: 'panel report-card' }, [
      h('div', { class: 'big-emoji' }, [ic('cap')]),
      h('h2', {}, ['本节结束']),
      h('div', { class: 'dim' }, ['这节课已经全部走完。']),
    ]),
    dock: dockActions([
      h('button', { class: 'primary dock-next', onclick: backToLessons }, ['回课程表 →']),
    ]),
  };
}

function viewCard() {
  const c = S.card;
  if (!c) return h('div', { class: 'card' }, ['加载中…']);
  const parts = c.finished ? finishedParts() : (CARDS[c.interaction] || cardQuiz)(c);
  return lessonShell(c, parts);
}

function promptCard(c, isI2A, answered) {
  // a2i（听音选图）作答前只放声音：图就是答案，提前放出来等于剧透。
  // i2a 的图是题干本身；短语/句子的图是场景教具，不是答案。
  const showImage = c.image && (isI2A || answered || c.domain !== 'words');
  return h('section', { class: 'panel prompt-panel' }, [
    h('div', { class: 'panel-tag' }, [c.tool_name || c.tool]),
    c.is_bonus ? h('div', { class: 'bonus-chip' }, ['顺带点']) : null,
    showImage
      ? h('div', { class: 'panel-media' }, [h('img', { src: c.image, alt: '' })])
      : h('div', { class: 'panel-media' }, [
          h('div', { class: 'prompt-echo' }, [ic('spk')]),
        ]),
    isI2A && c.meaning_zh
      ? h('div', { class: 'prompt-meaning' }, [c.meaning_zh])
      : null,
    isI2A ? null : h('div', { class: 'prompt-actions' }, [
      h('button', {
        class: 'play', onclick: e => play(c.prompt_audio, e.currentTarget),
      }, [ic('spk'), '再听一次']),
      // 没有独立的慢速音频就别放个假按钮（Friends 词卡的 slow == 常速）
      (c.prompt_audio_slow && c.prompt_audio_slow !== c.prompt_audio)
        ? h('button', {
            class: 'play small ghost',
            onclick: e => play(c.prompt_audio_slow, e.currentTarget),
          }, [ic('slow'), '慢速'])
        : null,
    ]),
  ]);
}

function cardQuiz(c) {
  const answered = S.picked !== null;
  const isI2A = c.direction === 'i2a';
  const long = c.domain !== 'words';   // 短语句子的选项用文字列表

  // 选项：词用图片格；短语句子用文字行（图片区分度不够）
  const opts = c.choices.map(ch => {
    const cls = ['opt'];
    if (long || isI2A) cls.push('text');
    if (answered) {
      if (ch.id === c.correct_id) cls.push('right');
      else if (ch.id === S.picked) cls.push('wrong');
    } else if (S.narrow && !S.narrow.includes(ch.id)) cls.push('faded');

    let kids;
    if (isI2A) {
      kids = [
        h('span', {
          class: 'opt-play', title: '试听',
          onclick: e => { e.stopPropagation(); play(ch.audio, e.currentTarget); },
        }, [ic('spk')]),
        h('span', {}, [answered ? ch.zh : '听一听']),
      ];
    } else if (long) {
      kids = [
        h('span', { class: 'zh3' }, [ch.zh]),
        answered ? h('span', { class: 'opt-en' }, [ch.label]) : null,
      ];
    } else {
      kids = [
        ch.image ? h('img', { src: ch.image, alt: '' })
                 : h('span', { class: 'dim' }, [ch.label]),
        // 答完把每个选项的词义都标出来（不只正确那个）：
        // 只标正确答案的话，蒙对的人不知道自己蒙对了，
        // 也不知道排除掉的选项各是什么意思
        answered ? h('span', { class: 'zh' }, [ch.label, ' ', ch.zh]) : null,
      ];
    }
    return h('button', {
      class: cls.join(' '),
      onclick: () => {
        // i2a 试听走选项里的 🔊 按钮，点选项本体 = 作答
        if (!answered) pick(ch.id, c);
      },
    }, kids);
  });

  const label = (c.text || c.item_id) + (c.meaning_zh ? `（${c.meaning_zh}）` : '');
  return {
    stage: h('div', { class: 'stage-split' }, [
      promptCard(c, isI2A, answered),
      h('section', { class: 'panel answer-panel' }, [
        h('div', { class: (long || isI2A) ? 'olist' : 'grid' }, opts),
      ]),
    ]),
    // 答对也要显示词义：可能是蒙对的，不确认一遍等于没学到
    dock: answered ? dockFeedback(label) : dockHint((c.step && c.step.hint) || ''),
  };
}

function cardShadow(c) {
  return {
    stage: h('section', { class: 'panel stage-solo-card' }, [
      h('div', { class: 'shadow-layout' }, [
        c.image
          ? h('div', { class: 'shadow-media' },
              [h('img', { src: c.image, alt: '' })])
          : h('div', { class: 'shadow-media' },
              [h('div', { class: 'prompt-echo' }, [ic('mic')])]),
        h('div', { class: 'shadow-target' }, [
          h('div', { class: 'shadow-text' }, [c.text]),
          c.meaning_zh ? h('div', { class: 'dim' }, [c.meaning_zh]) : null,
        ]),
      ]),
      h('div', { class: 'prompt-actions center' }, [
        h('button', {
          class: 'play', style: 'max-width:320px',
          onclick: e => play(c.prompt_audio, e.currentTarget),
        }, [ic('spk'), '先听一遍']),
      ]),
      h('div', { class: 'shadow-note' },
        ['跟读评分还没接（第二批）。自己念一遍，然后点下面。']),
    ]),
    dock: dockActions([
      h('button', { class: 'big ghost', onclick: () => answerCard(false) }, ['念不出来']),
      h('button', { class: 'primary big dock-next',
                    onclick: () => answerCard(true) }, ['念好了 →']),
    ]),
  };
}

function cardPassive(c) {
  return {
    stage: h('section', { class: 'panel stage-solo-card' }, [
      c.image
        ? h('div', { class: 'shadow-media', style: 'flex:1;min-height:0;width:100%' },
            [h('img', { src: c.image, alt: '' })])
        : h('div', { class: 'shadow-media', style: 'flex:1;min-height:0;width:100%' },
            [h('div', { class: 'prompt-echo' }, [ic('film')])]),
      h('div', { class: 'lead', style: 'margin-top:14px' },
        ['歇一下，看画面听原片。']),
      h('div', { class: 'prompt-actions center', style: 'width:100%' }, [
        h('button', {
          class: 'play', style: 'max-width:340px',
          onclick: e => playSeq(c.audio_clips, e.currentTarget),
        }, [ic('film'), '播放原片片段']),
      ]),
    ]),
    dock: dockActions([
      h('button', { class: 'primary dock-next', onclick: advance }, ['继续 →']),
    ]),
  };
}

function cardAssess(c) {
  return {
    stage: h('section', { class: 'panel stage-solo-card' }, [
      h('div', { class: 'lead' },
        ['刚才学的这几句，现在听原速原声 —— 听懂多少？']),
      h('div', { class: 'dim', style: 'margin-top:6px' },
        [`${c.audio_clips.length} 句原声连放`]),
      h('div', { class: 'prompt-actions center', style: 'margin-top:18px;width:100%' }, [
        h('button', {
          class: 'play', style: 'max-width:340px',
          onclick: e => playSeq(c.audio_clips, e.currentTarget),
        }, [ic('spk'), '播放原声']),
      ]),
    ]),
    dock: dockActions([
      h('button', { class: 'big ghost', onclick: () => assess(1) }, ['听懂一点']),
      h('button', { class: 'big ghost', onclick: () => assess(2) }, ['一半']),
      h('button', { class: 'primary big', onclick: () => assess(3) }, ['大部分']),
    ]),
  };
}

function cardReport() {
  return {
    stage: h('section', { class: 'panel report-card' }, [
      h('div', { class: 'big-emoji' }, [ic('flag')]),
      h('h2', {}, ['这节课上完了']),
      h('div', { class: 'dim' }, ['去看看这节课学了多少、下次要复习什么。']),
    ]),
    dock: dockActions([
      h('button', { class: 'primary dock-next', onclick: finishLesson }, ['看课后报告 →']),
    ]),
  };
}

function viewReport() {
  const r = S.report;
  if (!r) return h('div', { class: 'card' }, ['加载中…']);
  return h('div', {}, [
    h('h1', {}, ['课后报告']),
    h('div', { class: 'card', style: 'margin-top:12px' },
      [h('pre', { class: 'report' }, [r.text])]),
    r.mastered_now && r.mastered_now.length
      ? h('div', { class: 'card' }, [
          h('h2', {}, ['已掌握']),
          h('div', { class: 'chips' },
            r.mastered_now.map(w => h('span', { class: 'chip ok' }, [w]))),
        ])
      : h('div', { class: 'card' }, [
          h('div', { class: 'dim' }, [
            '本节没有产生「已掌握」—— 设计如此：一节课每个方向只练 1 次，' +
            '掌握要靠后面几节的复习环节确认。',
          ]),
        ]),
    r.narration
      ? h('div', { class: 'card' }, [h('h2', {}, ['小结']), h('div', {}, [r.narration])])
      : h('button', { class: 'ghost wide', onclick: narrate }, ['让 AI 写一段小结 (~6s)']),
    h('button', { class: 'primary wide big', style: 'margin-top:10px',
                  onclick: async () => {
                    S.status = await api('/api/status');
                    S.view = 'lessons'; S.report = null; render();
                  } }, ['回课程表']),
  ]);
}

// ---------- 用户动作 ----------

async function openUsers() {
  S.view = 'users'; S.users = null; render();
  S.users = await api('/api/users');
  render();
}

async function createUser() {
  const el = document.getElementById('newname');
  const name = (el && el.value || '').trim();
  if (!name) { toast('先起个名字'); return; }
  try {
    S.users = await api('/api/users', { name });
    await refreshStatus();
    render();
    toast(`创建了「${name}」`);
  } catch (e) { toast('创建失败：' + e.message, 3000); }
}

async function selectUser(uid) {
  if (S.users && uid === S.users.current_id) { await refreshStatus(); gotoMain(); return; }
  const r = await api(`/api/users/${uid}/select`, {});
  S.users = { current_id: r.current_id, users: r.users };
  S.status = r.status;
  gotoMain();
  toast(`切到「${S.status.user.name}」`);
}

async function deleteUser(x) {
  if (!confirm(`删除「${x.name}」？他的学习进度和课堂数据会一起删掉，不可恢复。`)) return;
  S.users = await api(`/api/users/${x.id}`, undefined, 'DELETE');
  await refreshStatus();
  render();
  toast(`删除了「${x.name}」`);
}

async function refreshStatus() {
  S.status = await api('/api/status');
}

function gotoMain() {
  // 有进行中的课就直接续上，否则看有没有课程表
  if (S.status && S.status.in_lesson) {
    const p = S.status.lesson_progress || {};
    S.lessonIndex = p.index != null ? p.index : null;
    api('/api/lesson/current').then(c => {
      S.card = c; resetCardState(); S.view = 'card'; render(); autoPlay();
    });
    return;
  }
  S.view = (S.status && S.status.assessed) ? 'lessons' : 'home';
  render();
}

// ---------- 动作 ----------

async function openChecklist() {
  S.view = 'check'; S.checklist = null; S.packing = false; render();
  try {
    S.checklist = await api('/api/checklist');
    render();
  } catch (e) { toast('清单加载失败：' + e.message, 3000); }
}

async function submitChecklist() {
  S.packing = true; render();
  try {
    // 只报单词。短语和句子由服务端按听力探测结果动态挑——
    // 传空数组是刻意的，一旦传了值就会走"尊重手动勾选"分支
    S.status = await api('/api/checklist/submit', {
      words: [...S.known.words], chunks: [], sentences: [],
    });
    S.packing = false;
    S.view = 'lessons'; render();
    const n = (S.status.lessons || []).length;
    const plan = S.status.plan || {};
    if (plan.fallback) {
      toast(`安排好了 ${n} 节课，但 AI 分组失败用了机械划分，建议重排`, 5000);
    } else {
      toast(`安排好了：${n} 节课`, 2500);
    }
  } catch (e) {
    S.packing = false; render();
    toast('打包失败：' + e.message, 4000);
  }
}

async function pauseLesson() {
  clearTimeout(S.stuckTimer);
  try {
    S.status = await api('/api/lesson/pause', {});
    S.card = null; S.lessonIndex = null; resetCardState();
    S.view = 'lessons'; render();
    toast('已保存进度，下次从这张卡继续', 2500);
  } catch (e) { toast('退出失败：' + e.message, 3000); }
}

async function startLesson(index) {
  try {
    S.card = await api(`/api/lesson/${index}/start`, {});
    S.lessonIndex = index;
    resetCardState();
    S.view = 'card'; render();
    autoPlay();
  } catch (e) { toast('失败：' + e.message, 3000); }
}

function resetCardState() {
  S.picked = null; S.correct = null; S.tutorLine = ''; S.narrow = null;
  clearTimeout(S.stuckTimer);
}

function autoPlay() {
  const c = S.card;
  if (!c || c.finished) return;
  if (c.interaction === 'quiz' && c.direction === 'a2i') {
    setTimeout(() => play(c.prompt_audio), 250);
    // 卡住才帮忙收窄。8 秒对成人学习者太急（听两遍原声就超了），给到 20 秒。
    S.stuckTimer = setTimeout(() => {
      if (S.picked === null && c.choices.length > 2) {
        const others = c.choices.map(x => x.id).filter(x => x !== c.correct_id);
        S.narrow = [c.correct_id, others[0]];
        toast('帮你去掉两个');
        render();
      }
    }, 20000);
  }
}

function pick(id, c) {
  clearTimeout(S.stuckTimer);
  S.picked = id;
  S.correct = id === c.correct_id;
  render();
  // 答对也要讲解：可能是蒙对的，而且答对时讲用法/搭配才是"课"，
  // 不然对的题等于白过
  api('/api/tutor/explain',
      { target: c.correct_id, chosen: id, domain: c.domain,
        correct: S.correct })
    .then(r => { S.tutorLine = r.line; if (S.picked !== null) render(); })
    .catch(() => {
      S.tutorLine = `${c.text || c.item_id}${c.meaning_zh ? '，' + c.meaning_zh : ''}。`;
      render();
    });
  if (!S.correct) play(c.prompt_audio);
}

async function submit() {
  if (S.busy) return;
  S.busy = true;
  try {
    S.card = await api('/api/lesson/answer', { choice: S.picked, correct: S.correct });
    resetCardState();
    render(); autoPlay();
  } finally { S.busy = false; }
}

async function answerCard(correct) {
  S.card = await api('/api/lesson/answer', { choice: '', correct });
  resetCardState(); render(); autoPlay();
}

async function advance() {
  S.card = await api('/api/lesson/advance', {});
  resetCardState(); render(); autoPlay();
}

async function assess(score) {
  S.card = await api('/api/lesson/assess', { score });
  resetCardState(); render(); autoPlay();
}

async function finishLesson() {
  S.report = await api('/api/lesson/finish', {});
  S.view = 'report'; render();
}

async function narrate() {
  toast('AI 正在写…');
  try {
    const r = await api('/api/lesson/finish?narrate=true', {});
    if (r && r.narration) { S.report.narration = r.narration; render(); }
  } catch (_) { toast('本节已收，小结要在课末即时生成'); }
}

// ---------- 键盘 ----------
document.addEventListener('keydown', e => {
  if (S.view === 'card' && S.card && !S.card.finished) {
    if (e.key === ' ') { e.preventDefault(); play(S.card.prompt_audio); }
    if (e.key === 'Enter' && S.picked !== null) submit();
    const n = parseInt(e.key, 10);
    if (n >= 1 && n <= 4 && S.picked === null && S.card.choices[n - 1]) {
      pick(S.card.choices[n - 1].id, S.card);
    }
  }
});

// ---------- 启动 ----------
(async () => {
  try {
    try { S.muted = localStorage.getItem('ailesson.muted') === '1'; } catch (_) {}
    au.muted = S.muted;
    S.users = await api('/api/users');
    // 没有用户先建一个；有了直接进学习
    if (!S.users.current_id) {
      S.view = 'users'; render();
      return;
    }
    S.status = await api('/api/status');
    if (S.status.in_lesson) {
      S.card = await api('/api/lesson/current');
      resetCardState();
      S.view = 'card'; render(); autoPlay();
      toast('接着上次继续');
      return;
    }
    gotoMain();
  } catch (e) {
    app.appendChild(h('div', { class: 'card' }, ['起不来：' + e.message]));
  }
})();
