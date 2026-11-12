#!/usr/bin/env python3
"""
Opg(リボルバー式カートリッジ選択)回帰テスト
=============================================
  python3 tests/opg_regression.py             # build/libmyln.dylib を使う(MYLN_LIB で差し替え可)

確認すること:
  1. 名札の例文(configs/opg/cartridges/*.json の examples)が全部、自分の穴に当たる
  2. 調整に使った文(tests/opg_cases.json の tuning / tuning2)が全部当たる
  3. あいまいな文(ambiguous)は 0番「汎用」に落ちる
  4. 調整に使っていない文(holdout)の正解数が下がっていない(現在 20/40。外れは汎用に落ちるのが大半)
  5. 空撃ちコマンド(stdin JSON → python3 opg.py)が決まった7つのキーだけを返す
  6. 難易度(level)は difficulty.py の判定と同じ
  7. 弾倉に無い答えは絶対に返さない(穴を空にした弾倉・でたらめな入力でも)
  8. 名札を足して弾倉を差し替えるだけで新しいカートリッジが選ばれる(Opg本体は変更なし)
  9. 不正な弾倉・名札はエラーになる
 10. カスケードは出口6つでも動き、既定の5つ(難易度)は従来どおり
"""
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bridge" / "python"))
import opg  # noqa: E402
import difficulty  # noqa: E402
from myln import MylnCascade  # noqa: E402

KEYS = ["cartridge", "conf", "level", "path", "reason", "runner_up", "slot"]
PATHS = {"relay", "full", "embed", "fallback"}
HOLDOUT_MIN = 20
failures = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


def score(mag, cases, label):
    """→ (正解数, 外した例)。1件ずつは表示せず、外れだけ出す。"""
    miss = []
    for text, want in cases:
        r = opg.select(text, mag)
        if r["cartridge"] != want:
            miss.append((text, want, r["cartridge"]))
            print(f"      miss [{label}] {want} -> {r['cartridge']}  {text!r}")
    return len(cases) - len(miss), miss


mag = opg.Magazine()
ids = [c.id for c in mag.slots]
cases = json.loads((ROOT / "tests/opg_cases.json").read_text(encoding="utf-8"))

print("[1] name-tag examples hit their own chamber")
check(ids == ["general", "chat", "code", "research", "doc", "calc"], f"magazine = {ids}")
for slot, cart in enumerate(mag.slots):
    ok, _ = score(mag, [(ex, cart.id) for ex in cart.examples], cart.id)
    check(ok == len(cart.examples) and len(cart.examples) >= 3, f"slot {slot} {cart.id}: {ok}/{len(cart.examples)}")

print("[2] tuning sentences")
for name in ("tuning", "tuning2"):
    ok, _ = score(mag, cases[name], name)
    check(ok == len(cases[name]), f"{name}: {ok}/{len(cases[name])}")

print("[3] ambiguous sentences fall back to chamber 0 (general)")
ok, _ = score(mag, [(t, "general") for t in cases["ambiguous"]], "ambiguous")
check(ok == len(cases["ambiguous"]), f"ambiguous -> general: {ok}/{len(cases['ambiguous'])}")
r = opg.select("このメモを要約して", mag)
check(r["path"] == "fallback" and r["slot"] == 0, f"tie between two specialists -> fallback ({r['reason'][:30]})")

print("[4] holdout (not used for tuning)")
ok, miss = score(mag, cases["holdout"], "holdout")
wrong = sum(1 for _, _, got in miss if got != "general")
print(f"      holdout {ok}/{len(cases['holdout'])}  (misses to general: {len(miss) - wrong}, to a wrong specialist: {wrong})")
check(ok >= HOLDOUT_MIN, f"holdout passes >= {HOLDOUT_MIN}/{len(cases['holdout'])}")
check(wrong <= 1, f"holdout: wrong-specialist misses <= 1 (now {wrong})")

print("[5] dry-fire command: echo '{\"text\":\"...\"}' | python3 opg.py")


def run_opg(text, *args, env=None):
    return subprocess.run(["python3", str(ROOT / "bridge/python/opg.py"), *args], input=json.dumps({"text": text}),
                          capture_output=True, text=True, env=env or os.environ)


for text, want in [("main.pyのバグを直して", "code"), ("こんにちは", "chat"), ("今日のニュースを要約して", "research"),
                   ("取引先へのお礼のメールを書いて", "doc"), ("1280円の3割引はいくら?", "calc"), ("これお願い", "general")]:
    p = run_opg(text)
    try:
        d = json.loads(p.stdout)
    except Exception:
        d = {}
    print("      ->", p.stdout.strip())
    check(p.returncode == 0 and sorted(d) == KEYS, f"exit 0 and exactly 7 keys  {text!r}")
    check(d.get("cartridge") == want and d.get("slot") == ids.index(want) and d.get("path") in PATHS
          and d.get("runner_up") in ids and d.get("runner_up") != d.get("cartridge")
          and isinstance(d.get("reason"), str) and d["reason"] and 0.0 <= d.get("conf", -1) <= 1.0,
          f"{want}: slot/path/runner_up/reason/conf are sane")
p = subprocess.run(["python3", str(ROOT / "bridge/python/opg.py")], input="", capture_output=True, text=True, env=os.environ)
check(p.returncode == 0 and json.loads(p.stdout)["cartridge"] == "general", "empty stdin -> general")

print("[6] level is the existing difficulty grade")
texts = [t for t, _ in cases["tuning"] + cases["holdout"]] + cases["ambiguous"]
check(all(opg.select(t, mag)["level"] == difficulty.classify(t)["level"] for t in texts),
      f"level == difficulty.py level for {len(texts)} sentences")

print("[7] never answers outside the magazine")
rng = random.Random(7)
vocab = [k for c in mag.slots for k in c.tag.get("keywords", []) + c.tag.get("hints", [])]
junk = ["".join(rng.choice(vocab + ["あ", "の", " ", "?", "123", "x"]) for _ in range(rng.randint(0, 12))) for _ in range(400)]
bad = [t for t in junk + texts if (lambda r: r["cartridge"] not in ids or r["runner_up"] not in ids
                                   or ids[r["slot"]] != r["cartridge"] or r["path"] not in PATHS)(opg.select(t, mag))]
check(not bad, f"{len(junk) + len(texts)} inputs (incl. random keyword soup): always a loaded cartridge")

tmp = Path(tempfile.mkdtemp(prefix="opg-test-"))
try:
    shutil.copytree(ROOT / "configs" / "opg", tmp / "opg")
    spec = json.loads((tmp / "opg/magazine.json").read_text(encoding="utf-8"))

    def write_mag(name, slots):
        (tmp / "opg" / name).write_text(json.dumps({**spec, "slots": slots}, ensure_ascii=False), encoding="utf-8")
        return tmp / "opg" / name

    # code と calc の穴を空にする → その2つは絶対に返らない
    m2 = opg.Magazine(write_mag("two_empty.json", ["general", "chat", None, "research", "doc", None]))
    left = {"general", "chat", "research", "doc"}
    out = [opg.select(t, m2) for t in junk + texts + [ex for c in mag.slots for ex in c.examples]]
    check(all(r["cartridge"] in left and r["runner_up"] in left for r in out),
          f"empty chambers 2 and 5: {len(out)} inputs never return code/calc")
    check(opg.select("main.pyのバグを直して", m2)["cartridge"] == "general", "code request with code unloaded -> general")
    m3 = opg.Magazine(write_mag("only_general.json", ["general", None, None, None, None, None]))
    r = opg.select("main.pyのバグを直して", m3)
    check(r["cartridge"] == "general" and r["runner_up"] == "general", "only general loaded -> general")

    print("[8] a new cartridge = one new name-tag file + magazine swap (no change to Opg)")
    (tmp / "opg/cartridges/cook.json").write_text(json.dumps({
        "id": "cook", "name": "料理番", "keywords": ["レシピ", "献立", "煮物", "炒め", "下ごしらえ", "作り方"],
        "hints": ["夕飯", "冷蔵庫", "材料"], "examples": ["肉じゃがのレシピを教えて"]}, ensure_ascii=False), encoding="utf-8")
    m4 = opg.Magazine(write_mag("with_cook.json", ["general", "chat", "code", "research", "doc", "cook"]))
    r = opg.select("冷蔵庫の残り物で作れる夕飯の献立とレシピを考えて", m4)
    check(r["cartridge"] == "cook" and r["slot"] == 5, f"new cartridge chosen: {r['cartridge']} slot {r['slot']} ({r['reason'][:24]})")
    check(opg.select("1280円の3割引はいくら?", m4)["cartridge"] != "calc", "unloaded calc is no longer returned")
    check(opg.select("main.pyのバグを直して", m4)["cartridge"] == "code", "other chambers unaffected")
    p = run_opg("肉じゃがのレシピと作り方", "--magazine", str(tmp / "opg/with_cook.json"))
    check(p.returncode == 0 and json.loads(p.stdout)["cartridge"] == "cook", "--magazine on the command line")
    p = run_opg("肉じゃがのレシピと作り方", env={**os.environ, "OPG_MAGAZINE": str(tmp / "opg/with_cook.json")})
    check(p.returncode == 0 and json.loads(p.stdout)["cartridge"] == "cook", "OPG_MAGAZINE environment variable")

    print("[9] invalid magazines / name tags are rejected")
    (tmp / "opg/cartridges/broken.json").write_text('{"id": "broken", "name": "x", "patterns": ["(("]}', encoding="utf-8")
    (tmp / "opg/cartridges/wrongid.json").write_text('{"id": "other", "name": "x"}', encoding="utf-8")
    for name, slots in {
        "5 chambers": ["general", "chat", "code", "research", "doc"],
        "7 chambers": ["general", "chat", "code", "research", "doc", "calc", "cook"],
        "chamber 0 empty": [None, "chat", "code", "research", "doc", "calc"],
        "same cartridge twice": ["general", "chat", "chat", "research", "doc", "calc"],
        "missing name tag": ["general", "chat", "code", "research", "doc", "nope"],
        "bad regex in name tag": ["general", "chat", "code", "research", "doc", "broken"],
        "id differs from file name": ["general", "chat", "code", "research", "doc", "wrongid"],
    }.items():
        try:
            opg.Magazine(write_mag("bad.json", slots))
            check(False, f"{name}: should have raised ValueError")
        except ValueError as e:
            check(True, f"{name}: ValueError ({str(e)[:40]})")
    p = run_opg("こんにちは", "--magazine", str(tmp / "opg/bad.json"))
    check(p.returncode == 1 and "error" in json.loads(p.stdout), "command exits 1 with an error message")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("[10] cascade with 6 outputs; the default 5 is unchanged")
c6 = MylnCascade(0.8, n_classes=6).tune_custom(ROOT / "configs/opg/cascade.json")
probs, _ = c6.infer([0, 1, 0, 0, 0, 0, 0, 0, 0, 0])
check(len(probs) == 6 and probs.index(max(probs)) == 2 and abs(sum(probs) - 1) < 1e-4, "6 probabilities, strong2 -> slot 2")
probs, relay = c6.infer([0] * 10)
check(probs.index(max(probs)) == 0 and relay, "no cues -> slot 0 (general), settled by the relay")
c5 = MylnCascade(0.8).tune_difficulty()
check(len(c5.infer([0, 0, 0, 0, 0])[0]) == 5, "default cascade still has 5 outputs")
try:
    MylnCascade(0.8).tune_custom(ROOT / "configs/opg/cascade.json")
    check(False, "6-chamber config on a 5-output cascade should raise")
except ValueError:
    check(True, "6-chamber config on a 5-output cascade: ValueError")
check(opg.careful_path("こんにちは", mag, [0.0] * 6) is None, "careful path is a stub (returns None, loads no model)")

print()
if failures:
    print(f"FAILED: {len(failures)} check(s)")
    sys.exit(1)
print("ALL PASSED")
