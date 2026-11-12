#!/usr/bin/env python3
"""
MYLN-FRAME 回帰テスト
=====================
  python3 tests/regression.py                 # build/libmyln.dylib(本番)を使う
  MYLN_LIB=build-dev/libmyln.dylib python3 tests/regression.py   # 開発ビルドを使う

確認すること:
  1. difficulty.py(Pi拡張と同じ呼び出し)の出力が
       a. 期待レンジ(tests/baseline/difficulty_cases.json の tuning 全件)に入る
       b. 整理後の基準 tests/baseline/difficulty_baseline_v1.json と level / relay / features / conf が一致
     ※ tests/baseline/difficulty_baseline.json は 2026-10-03 の整理前の記録。履歴として保存のみ。
  2. holdout(調整に使っていない文)の合格数が下がっていない(現在 12/15)
  3. C++ チューナー ⇔ JSON 設定 が同じ確率を返す(difficulty, Frame / Cascade)
  5. 不正な設定はエラーになり、frame は変更されない(部分適用なし)
  6. Pi 拡張と同じ呼び出し(stdin JSON → python3 difficulty.py)が従来のキーで返る
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bridge" / "python"))
from myln import MylnFrame, MylnCascade, DIFFICULTY_CLASSES  # noqa: E402

failures = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


def maxdiff(a, b):
    return max(abs(x - y) for x, y in zip(a, b))


# ── 1,2,6: difficulty.py(Pi拡張と同じ呼び出し方)────────────
def run_difficulty(text):
    r = subprocess.run(["python3", str(ROOT / "bridge/python/difficulty.py")],
                       input=json.dumps({"text": text}), capture_output=True, text=True, env=os.environ)
    try:
        return json.loads(r.stdout)
    except Exception:
        return None


print("[1] difficulty.py vs expected ranges and v1 baseline (subprocess, same as Pi extension)")
cases = json.loads((ROOT / "tests/baseline/difficulty_cases.json").read_text(encoding="utf-8"))
for text, lo, hi in cases["tuning"]:
    d = run_difficulty(text)
    check(d is not None and lo <= d["level"] <= hi, f"level {d and d['level']} in [{lo},{hi}]  {text[:34]!r}")
base = json.loads((ROOT / "tests/baseline/difficulty_baseline_v1.json").read_text(encoding="utf-8"))
for b in base:
    d = run_difficulty(b["text"])
    check(d is not None and sorted(d.keys()) == ["conf", "features", "level", "relay"], f"keys {b['text'][:20]!r}")
    if d:
        check(d["level"] == b["level"] and d["relay"] == b["relay"] and d["features"] == b["features"]
              and d["conf"] == b["conf"], f"v1 baseline same  {b['text'][:30]!r}")

print("[2] holdout (not used for tuning)")
hold_ok = 0
for text, lo, hi in cases["holdout"]:
    d = run_difficulty(text)
    hold_ok += bool(d and lo <= d["level"] <= hi)
print(f"      holdout {hold_ok}/{len(cases['holdout'])}")
check(hold_ok >= 12, "holdout passes >= 12/15 (known misses: investigate-type requests)")

print("[6] Pi extension call: echo '{\"text\":\"こんにちは\"}' | python3 difficulty.py")
r = subprocess.run(["python3", str(ROOT / "bridge/python/difficulty.py")],
                   input='{"text":"こんにちは"}', capture_output=True, text=True, env=os.environ)
print("      ->", r.stdout.strip())
check(r.returncode == 0 and set(json.loads(r.stdout)) == {"level", "conf", "relay", "features"}, "exit 0 and keys")

# ── 3: C++ チューナー ⇔ JSON 設定 ────────────────────────────
print("[3] C++ tuner == JSON config")
probe = [[0, 0.05, 0.01, 0, 0.1], [0.1, 0.3, 0.4, 0.05, 0.25], [0.2, 0.2, 0.9, 0.1, 0.2],
         [0.5, 0.7, 0.3, 0.95, 0.6], [0.9, 0.95, 0.8, 0.99, 0.85], [0, 0, 0, 0, 0], [1, 1, 1, 1, 1],
         [0.33, 0, 0, 0, 0.67], [0.67, 0.14, 1, 1, 1], [1, 0.1, 0, 0.5, 1], [0.5, 0.5, 0.5, 0.5, 0.5]]
cfgdir = ROOT / "configs"
for size in ("T", "SS"):
    for cpp, js in (("tune_difficulty", "difficulty.json"),):
        a = getattr(MylnFrame(size, 5), cpp)()
        b = MylnFrame(size, 5).tune_custom(cfgdir / js)
        d = max(maxdiff(a.infer(x), b.infer(x)) for x in probe)
        check(d == 0.0, f"Frame {size} {cpp} vs {js}: max diff {d}")
for cpp, js in (("tune_difficulty", "difficulty_cascade.json"),):
    a = getattr(MylnCascade(0.8), cpp)()
    b = MylnCascade(0.8).tune_custom(cfgdir / js)
    d, same = 0.0, True
    for x in probe:
        pa, ra = a.infer(x); pb, rb = b.infer(x)
        d = max(d, maxdiff(pa, pb)); same &= (ra == rb)
    check(d == 0.0 and same, f"Cascade {cpp} vs {js}: max diff {d}, same relay path={same}")

# ── 5: 不正な設定 ─────────────────────────────────────────
print("[5] invalid configs are rejected without partial application")
f = MylnFrame("T", 5).tune_difficulty()
before = [f.infer(x) for x in probe]
good_router = {"normalize": False, "slots": [{"terms": [[0, 0, 50.0]]}, {}, {}, {}]}
bad_cfgs = {
    "bad cls class index": {"in_dim": 5, "router": good_router,
                            "center": {"cls": {"terms": [[9, 0, 1.0]]}}},
    "bad in index": {"in_dim": 5, "router": {"slots": [{"terms": [[0, 7, 1.0]]}, {}, {}, {}]}},
    "wrong slot count": {"in_dim": 5, "router": {"slots": [{}]}},
    "unknown key": {"in_dim": 5, "routr": {}},
    "unknown head": {"heads": ["passthrough", "zero", "nope", "zero"]},
    "size mismatch": {"size": "SS"},
    "n_classes mismatch": {"n_classes": 3},
    "missing in_dim": {"router": good_router},
    "not json": "{ nope",
}
for name, cfg in bad_cfgs.items():
    try:
        f.tune_custom(cfg)
        check(False, f"{name}: should have raised ValueError")
    except ValueError as e:
        check(True, f"{name}: ValueError ({str(e)[:60]})")
check([f.infer(x) for x in probe] == before, "frame unchanged after rejected configs")
try:
    MylnFrame("T", 5).tune_custom(str(ROOT / "configs" / "no_such_file.json"))
    check(False, "missing file should raise")
except ValueError:
    check(True, "missing file: ValueError")
f2 = MylnFrame("T", 5).tune_custom(cfgdir / "difficulty.json")
check(f2.classes == DIFFICULTY_CLASSES and f2.predict([0, 0, 0, 0, 0]) == "CHAT", "classes from config -> predict label")

print()
if failures:
    print(f"FAILED: {len(failures)} check(s)")
    sys.exit(1)
print("ALL PASSED")
