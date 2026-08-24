#!/bin/bash
# 端到端冒烟：走完一节课并检查课堂数据落盘。
# 用法: bash scripts/smoke.sh [用户名]
set -e
cd "$(dirname "$0")/.."
B=http://127.0.0.1:${AILESSON_PORT:-8791}
NAME=${1:-冒烟测试}
J() { .venv/bin/python -c "import json,sys;$1"; }

post() { curl -sS -X POST "$B$1" -H 'Content-Type: application/json' -d "${2:-\{\}}"; }

echo "① 建用户 $NAME"
UID_=$(post /api/users "{\"name\":\"$NAME\"}" | J "d=json.load(sys.stdin);print([u['id'] for u in d['users'] if u['name']=='$NAME'][-1])")
post "/api/users/$UID_/select" >/dev/null
echo "   uid=$UID_"

echo "② 勾选（词全会，短语句子基本不会 —— 模拟 CET-6）"
WORDS=$(curl -sS "$B/api/checklist" | J "d=json.load(sys.stdin);print(json.dumps([x['id'] for g in d['words']['groups'] for x in g['items']]))")
ST=$(post /api/checklist/submit "{\"words\":$WORDS,\"chunks\":[\"im_peppa\",\"all_right\"],\"sentences\":[]}")
echo "$ST" | J "d=json.load(sys.stdin);a=d['assessment'];print(f\"   待学 {a['total_unknown']} 点 → {len(d['lessons'])} 节\")"

echo "③ 上第 1 节"
CARD=$(post /api/lesson/1/start)
N=0
while true; do
  FIN=$(echo "$CARD" | J "d=json.load(sys.stdin);print(d.get('finished',False))")
  [ "$FIN" = "True" ] && break
  N=$((N+1)); [ $N -gt 300 ] && break
  ACT=$(echo "$CARD" | J "print(json.load(sys.stdin)['interaction'])")
  NEED=$(echo "$CARD" | J "print(json.load(sys.stdin)['needs_answer'])")
  if [ "$ACT" = "assess" ]; then CARD=$(post /api/lesson/assess '{"score":2}')
  elif [ "$NEED" = "True" ]; then
    CID=$(echo "$CARD" | J "print(json.load(sys.stdin)['correct_id'])")
    OK=$([ $((N % 7)) -eq 0 ] && echo false || echo true)
    CARD=$(post /api/lesson/answer "{\"choice\":\"$CID\",\"correct\":$OK}")
  else CARD=$(post /api/lesson/advance); fi
done
echo "   走过 $N 张卡"

echo "④ 报告"
post /api/lesson/finish | J "d=json.load(sys.stdin);print('   '+d['text'].replace(chr(10),chr(10)+'   '))"

echo "⑤ 课堂数据"
curl -sS "$B/api/users/$UID_/history" | J "
d=json.load(sys.stdin)['history']; x=d[-1]
print(f\"   {len(d)} 条; 第{x['lesson_index']}节「{x['theme']}」\")
print(f\"   词/短语/句={x['n_words']}/{x['n_chunks']}/{x['n_sentences']} 题量={x['asked']} 正确率={x['accuracy']}\")
print(f\"   需复习{len(x['review_next'])}项 打回{len(x['demoted'])}项 掌握{len(x['mastered_now'])}项\")"

echo "⑥ 清理"
curl -sS -X DELETE "$B/api/users/$UID_" | J "print('   剩余用户:',[u['name'] for u in json.load(sys.stdin)['users']])"
echo "✓ 冒烟通过"
