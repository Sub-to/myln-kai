#!/usr/bin/env python3
"""
Opg(リボルバー式カートリッジ選択)回帰テスト
=============================================
  python3 tests/opg_regression.py             # build/libmyln.dylib を使う(MYLN_LIB で差し替え可)

確認すること:
  1. 名札の例文(configs/opg/cartridges/*.json の examples)が全部、自分の穴に当たる
  2. 調整に使った文(tests/opg_cases.json の tuning / tuning2)が全部当たる
  3. あいまいな文(ambiguous)は 0番「汎用」に落ちる
  4. 調整に使っていない文(holdout / holdout2)の正解数が下がっていない(速い道だけで 21/40・16/40。外れは汎用に落ちるのが大半)
  5. 空撃ちコマンド(stdin JSON → python3 opg.py)が決まった7つのキーだけを返す
  6. 難易度(level)は difficulty.py の判定と同じ
  7. 弾倉に無い答えは絶対に返さない(穴を空にした弾倉・でたらめな入力でも)
  8. 名札を足して弾倉を差し替えるだけで新しいカートリッジが選ばれる(Opg本体は変更なし)
  9. 不正な弾倉・名札はエラーになる
 10. カスケードは出口6つでも動き、既定の5つ(難易度)は従来どおり
 11. 丁寧な道(例文との近さ。configs/opg/local.json か OPG_EMBED_MODEL に手元のモデルがあるときだけ実行):
     holdout2 が 29/40 以上(現在 31/40)、あいまいな文は汎用のまま、弾倉に無い答えは返さない。
     モデルが無いときは飛ばす(速い道だけで動くことは 1〜10 で確認済み)
 12. 引き金(opg_fire.py): にせの手元サーバーに対して、選んだ名札の指示書が送られること、
     自分のMac以外の宛先・未設定・空の入力では何も送らないこと。本物のモデルは動かさない
 13. 回しすぎない(opg.hold): あいまいな続きは今のカートリッジを保ち、はっきり別の専門家なら回す
 14. 会話の続き(session): これまでのやり取りが送られ、保存・読み直し・やり直しができ、失敗した回は保存しない

1〜10 は丁寧な道を切って(速い道だけで)確認する。モデルは一切動かさない。
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
HOLDOUT_MIN = {"holdout": 20, "holdout2": 16}     # 速い道だけ
CAREFUL_MIN = 29                                # 丁寧な道ありの holdout2(現在 31/40)
os.environ["OPG_CAREFUL"] = "0"                  # 1〜10 と子プロセスは速い道だけ
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


mag = opg.Magazine(careful=False)
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
for name in ("holdout", "holdout2"):
    ok, miss = score(mag, cases[name], name)
    wrong = sum(1 for _, _, got in miss if got != "general")
    print(f"      {name} {ok}/{len(cases[name])}  (misses to general: {len(miss) - wrong}, to a wrong specialist: {wrong})")
    check(ok >= HOLDOUT_MIN[name], f"{name} passes >= {HOLDOUT_MIN[name]}/{len(cases[name])} (fast path only)")
    check(wrong <= 1, f"{name}: wrong-specialist misses <= 1 (now {wrong})")
ok, _ = score(mag, [(t, "general") for t in cases["ambiguous2"]], "ambiguous2")
check(ok == len(cases["ambiguous2"]), f"ambiguous2 (unseen) -> general: {ok}/{len(cases['ambiguous2'])}")

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
texts = [t for t, _ in cases["tuning"] + cases["holdout"] + cases["holdout2"]] + cases["ambiguous"]
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
    m2 = opg.Magazine(careful=False, path=write_mag("two_empty.json", ["general", "chat", None, "research", "doc", None]))
    left = {"general", "chat", "research", "doc"}
    out = [opg.select(t, m2) for t in junk + texts + [ex for c in mag.slots for ex in c.examples]]
    check(all(r["cartridge"] in left and r["runner_up"] in left for r in out),
          f"empty chambers 2 and 5: {len(out)} inputs never return code/calc")
    check(opg.select("main.pyのバグを直して", m2)["cartridge"] == "general", "code request with code unloaded -> general")
    m3 = opg.Magazine(careful=False, path=write_mag("only_general.json", ["general", None, None, None, None, None]))
    r = opg.select("main.pyのバグを直して", m3)
    check(r["cartridge"] == "general" and r["runner_up"] == "general", "only general loaded -> general")

    print("[8] a new cartridge = one new name-tag file + magazine swap (no change to Opg)")
    (tmp / "opg/cartridges/cook.json").write_text(json.dumps({
        "id": "cook", "name": "料理番", "keywords": ["レシピ", "献立", "煮物", "炒め", "下ごしらえ", "作り方"],
        "hints": ["夕飯", "冷蔵庫", "材料"], "examples": ["肉じゃがのレシピを教えて"]}, ensure_ascii=False), encoding="utf-8")
    m4 = opg.Magazine(careful=False, path=write_mag("with_cook.json", ["general", "chat", "code", "research", "doc", "cook"]))
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
check(opg.careful_path("こんにちは", mag, [0.0] * 6) is None and mag.bank() is None,
      "careful path switched off -> returns None and loads no model")

print("[11] careful path (nearest example sentences, local embedding model)")
import opg_embed  # noqa: E402
if not (opg_embed.find_model(ROOT / "configs" / "opg") and opg_embed.shutil.which("llama-embedding")):
    print("      skipped: no local embedding model (configs/opg/local.json or OPG_EMBED_MODEL) or no llama-embedding")
else:
    cm = opg.Magazine(careful=True)
    check(cm.bank() is not None, f"example bank ready ({len(cm.bank().examples)} example sentences)")
    miss, used = [], 0
    for text, want in cases["holdout2"]:
        r = opg.select(text, cm)
        used += r["path"] == "embed"
        if r["cartridge"] != want:
            miss.append(r["cartridge"])
            print(f"      miss [holdout2+careful] {want} -> {r['cartridge']} ({r['path']})  {text!r}")
    ok, wrong = len(cases["holdout2"]) - len(miss), sum(1 for g in miss if g != "general")
    print(f"      holdout2 with careful path {ok}/{len(cases['holdout2'])}  (decided by careful path: {used}, wrong specialist: {wrong})")
    check(ok >= CAREFUL_MIN, f"holdout2 with careful path >= {CAREFUL_MIN}/{len(cases['holdout2'])}")
    check(wrong <= 4, f"holdout2 with careful path: wrong-specialist misses <= 4 (now {wrong})")
    amb = cases["ambiguous"] + cases["ambiguous2"]
    got = [opg.select(t, cm)["cartridge"] for t in amb]
    check(got.count("general") >= len(amb) - 1, f"ambiguous sentences stay general: {got.count('general')}/{len(amb)}")
    r = opg.select("富士山の高さは?", cm)
    check(sorted(r) == KEYS and r["path"] in PATHS and r["cartridge"] in ids and r["runner_up"] in ids
          and r["runner_up"] != r["cartridge"], f"same 7 keys on the careful path ({r['cartridge']}, {r['path']})")
    check(opg.select("main.pyのバグを直して", cm)["path"] == "relay", "clear requests are still settled by the fast path")
    tmp = Path(tempfile.mkdtemp(prefix="opg-test-"))
    try:
        shutil.copytree(ROOT / "configs" / "opg", tmp / "opg", ignore=shutil.ignore_patterns(".cache"))
        spec = json.loads((tmp / "opg/magazine.json").read_text(encoding="utf-8"))
        (tmp / "opg/m.json").write_text(json.dumps({**spec, "slots": ["general", None, "code", None, "doc", None]},
                                                   ensure_ascii=False), encoding="utf-8")
        m5 = opg.Magazine(tmp / "opg/m.json", careful=True)
        out = [opg.select(t, m5) for t, _ in cases["holdout2"]]
        check(all(r["cartridge"] in ("general", "code", "doc") and r["runner_up"] in ("general", "code", "doc") for r in out),
              "careful path with 3 chambers loaded: never returns an unloaded cartridge")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    p = run_opg("富士山の高さは?", env={**os.environ, "OPG_CAREFUL": "1"})
    print("      ->", p.stdout.strip())
    check(p.returncode == 0 and sorted(json.loads(p.stdout)) == KEYS, "command with the careful path: exit 0 and 7 keys")

print("[12] trigger (opg_fire.py) against a fake local server -- no model is run")
import threading  # noqa: E402
from http.server import BaseHTTPRequestHandler, HTTPServer  # noqa: E402
import opg_fire  # noqa: E402

seen = []


class Fake(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        seen.append((self.path, body))
        reply = {"choices": [{"message": {"content": "" if body["messages"][-1]["content"] == "EMPTY" else " pong "}}]}
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


srv = HTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
cfg = {**opg_fire.DEFAULTS, "endpoint": f"http://127.0.0.1:{srv.server_port}/v1", "model": "body-model", "timeout": 5}
try:
    r = opg_fire.fire("main.pyのバグを直して", mag, cfg)
    path, body = seen[-1]
    code_prompt = mag.slots[2].tag["prompt"]
    check(r.get("answer") == "pong" and r["cartridge"] == "code" and "error" not in r and r["model"] == "body-model",
          "fire: picks code, sends, returns the answer")
    check(path == "/v1/chat/completions" and body["messages"][0] == {"role": "system", "content": code_prompt}
          and body["messages"][-1] == {"role": "user", "content": "main.pyのバグを直して"} and body["model"] == "body-model",
          "fire: the chosen cartridge's prompt is the system message")
    check(all(c.tag.get("prompt") for c in mag.slots), "every loaded cartridge has a prompt")
    check(set(KEYS) <= set(r), "fire: keeps the 7 dry-fire keys")
    r = opg_fire.fire("main.pyのバグを直して", mag, cfg, manual="calc")
    check(r["cartridge"] == "calc" and r["path"] == "manual" and r["runner_up"] == "code"
          and seen[-1][1]["messages"][0]["content"] == mag.slots[5].tag["prompt"], "fire: manual choice uses that cartridge's prompt")
    n = len(seen)
    r = opg_fire.fire("こんにちは", mag, cfg, manual="no-such")
    check("error" in r and "answer" not in r and len(seen) == n, "fire: manual choice not in the magazine -> error, nothing sent")
    r = opg_fire.fire("こんにちは", mag, cfg, dry=True)
    check("request" in r and "answer" not in r and len(seen) == n, "fire --dry: shows the request, sends nothing")
    for bad in ("http://example.com/v1", "http://192.168.1.202:11434/v1", "http://127.0.0.1.example.com/v1", "ftp://127.0.0.1/v1", ""):
        r = opg_fire.fire("こんにちは", mag, {**cfg, "endpoint": bad})
        check("error" in r and "answer" not in r and len(seen) == n, f"fire: endpoint {bad!r} refused, nothing sent")
    r = opg_fire.fire("こんにちは", mag, {**cfg, "model": ""})
    check("error" in r and len(seen) == n, "fire: no model configured -> error, nothing sent")
    r = opg_fire.fire("   ", mag, cfg)
    check("error" in r and len(seen) == n, "fire: empty text -> error, nothing sent")
    r = opg_fire.fire("EMPTY", mag, cfg)
    check("error" in r and "answer" not in r, "fire: empty answer from the model -> error")
    mag.slots[2].tag["base_model"], mag.slots[2].tag["lora"] = "code-model", "x.gguf"
    r = opg_fire.fire("main.pyのバグを直して", mag, {**cfg, "extra": {"reasoning_effort": "none"}})
    check(r["model"] == "code-model" and seen[-1][1]["model"] == "code-model" and seen[-1][1]["reasoning_effort"] == "none"
          and "note" in r, "fire: name tag base_model overrides the model; extra is passed; lora is noted as unused")
    mag.slots[2].tag["base_model"], mag.slots[2].tag["lora"] = "", ""
finally:
    srv.shutdown()
    srv.server_close()
r = opg_fire.fire("こんにちは", mag, cfg)
check("error" in r and "answer" not in r, "fire: server not running -> error (no crash)")

print("[13] not over-rotating (opg.hold) -- fast path only, no model")
def sel(text, current):
    return opg.hold(opg.select(text, mag), current, mag)

r = sel("もっと短くして", "code")
check(r["cartridge"] == "code" and r["slot"] == 2 and r["path"] == "hold" and r["runner_up"] == "general" and sorted(r) == KEYS,
      "hold: a vague follow-up keeps the current cartridge (same 7 keys)")
check(all(sel(t, "calc")["cartridge"] == "calc" for t in ("それで進めて", "うん", "つづけて", "")),
      "hold: short replies keep the current cartridge")
r = sel("お礼のメールを書いて", "code")
check(r["cartridge"] == "doc" and r["path"] == "relay", "hold: clear word cues for another specialist -> rotate")
r = sel("ありがとう", "code")
check(r["cartridge"] == "code" and r["path"] == "hold" and r["runner_up"] == "chat", "hold: a one-word acknowledgement does not rotate to chat")
check(sel("main.pyのバグを直して", "code")["path"] == "relay", "hold: same cartridge again -> unchanged result")
for cur in (None, "", "general", "no-such"):
    check(sel("もっと短くして", cur) == opg.select("もっと短くして", mag), f"hold: current={cur!r} -> plain selection")
fake = {"cartridge": "chat", "slot": 1, "conf": 0.85, "runner_up": "general", "level": 0, "path": "embed", "reason": "x"}
check(opg.hold(dict(fake), "code", mag)["cartridge"] == "code", "hold: careful path below switch_conf -> keep")
check(opg.hold({**fake, "conf": 0.95}, "code", mag)["cartridge"] == "chat", "hold: careful path at/above switch_conf -> rotate")
mag.sticky = False
check(sel("もっと短くして", "code")["cartridge"] == "general", "hold: switched off in the magazine -> plain selection")
mag.sticky = True
m3 = None
tmp = Path(tempfile.mkdtemp(prefix="opg-test-"))
try:
    shutil.copytree(ROOT / "configs" / "opg", tmp / "opg", ignore=shutil.ignore_patterns(".cache", ".sessions", "local.json"))
    spec = json.loads((tmp / "opg/magazine.json").read_text(encoding="utf-8"))
    (tmp / "opg/m.json").write_text(json.dumps({**spec, "slots": ["general", None, None, None, "doc", None]},
                                               ensure_ascii=False), encoding="utf-8")
    m3 = opg.Magazine(tmp / "opg/m.json", careful=False)
    r = opg.hold(opg.select("もっと短くして", m3), "code", m3)
    check(r["cartridge"] in ("general", "doc"), "hold: a current cartridge that is no longer loaded is never returned")

    print("[14] conversation (session) against a fake local server -- no model is run")
    seen.clear()
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cfg = {**opg_fire.DEFAULTS, "endpoint": f"http://127.0.0.1:{srv.server_port}/v1", "model": "body-model", "timeout": 5}
    sdir = tmp / "opg"
    try:
        s1 = opg_fire.Session(sdir, "t1")
        r = opg_fire.fire("main.pyのバグを直して", mag, cfg, session=s1)
        check(r["cartridge"] == "code" and r["turn"] == 1 and len(seen[-1][1]["messages"]) == 2, "session: first turn has no history")
        r = opg_fire.fire("もっと短くして", mag, cfg, session=s1)
        msgs = seen[-1][1]["messages"]
        check(r["cartridge"] == "code" and r["path"] == "hold" and r["turn"] == 2, "session: follow-up stays on the same cartridge")
        check([m["role"] for m in msgs] == ["system", "user", "assistant", "user"] and msgs[0]["content"] == mag.slots[2].tag["prompt"]
              and msgs[1]["content"] == "main.pyのバグを直して" and msgs[2]["content"] == "pong" and msgs[3]["content"] == "もっと短くして",
              "session: earlier turns are sent, with the current cartridge's prompt")
        s2 = opg_fire.Session(sdir, "t1")                       # 別のプロセスから読み直したのと同じ
        check(s2.current == "code" and len(s2.turns) == 2, "session: saved to disk and loaded again")
        r = opg_fire.fire("お礼のメールを書いて", mag, cfg, session=s2)
        msgs = seen[-1][1]["messages"]
        check(r["cartridge"] == "doc" and msgs[0]["content"] == mag.slots[4].tag["prompt"] and len(msgs) == 6
              and sum(m["role"] == "system" for m in msgs) == 1, "session: rotating swaps the prompt, keeps the history")
        check(opg_fire.Session(sdir, "t1").current == "doc", "session: the new cartridge becomes the current one")
        r = opg_fire.fire("それで進めて", mag, cfg, manual="calc", session=s2)
        check(r["cartridge"] == "calc" and opg_fire.Session(sdir, "t1").current == "calc", "session: manual choice becomes the current one")
        n, saved = len(seen), len(opg_fire.Session(sdir, "t1").turns)
        opg_fire.fire("こんにちは", mag, cfg, session=s2, dry=True)
        opg_fire.fire("EMPTY", mag, cfg, session=s2)
        opg_fire.fire("こんにちは", mag, {**cfg, "endpoint": "http://example.com/v1"}, session=s2)
        check(len(opg_fire.Session(sdir, "t1").turns) == saved, "session: dry run / failed turns are not saved")
        check(len(opg_fire.Session(sdir, "t2").turns) == 0 and opg_fire.Session(sdir, "t2").current == "", "session: another name is a separate conversation")
        check(len(opg_fire.Session(sdir, "t1", reset=True).turns) == 0, "session: reset starts over")
        s3 = opg_fire.Session(sdir, "t3")
        for i in range(5):
            opg_fire.fire(f"main.pyのバグを直して {i}", mag, {**cfg, "history_turns": 2}, session=s3)
        msgs = seen[-1][1]["messages"]
        check(len(msgs) == 1 + 2 * 2 + 1 and msgs[1]["content"].endswith(" 2") and len(s3.turns) == 5,
              "session: only the last history_turns turns are sent; all are kept on disk")
        for bad in ("../x", "a/b", "", "a b", "x" * 65, "..", "a.json"):
            try:
                opg_fire.Session(sdir, bad)
                check(False, f"session name {bad!r} should be refused")
            except opg_fire.FireError:
                check(True, f"session name {bad!r} refused")
        check(sorted(p.name for p in (sdir / ".sessions").iterdir()) == ["t1.json", "t3.json"]
              and not list(tmp.glob("*.json")), "session: files only inside .sessions/")
        (sdir / ".sessions" / "broken.json").write_text("{", encoding="utf-8")
        try:
            opg_fire.Session(sdir, "broken")
            check(False, "broken session file should raise")
        except opg_fire.FireError:
            check(True, "broken session file -> error (not silently overwritten)")
        env = {**os.environ, "OPG_FIRE_ENDPOINT": cfg["endpoint"], "OPG_FIRE_MODEL": "body-model"}
        outs = []
        for t in ("main.pyのバグを直して", "もっと短くして"):
            p = subprocess.run([sys.executable, str(ROOT / "bridge/python/opg_fire.py"), "--magazine", str(sdir / "magazine.json")],
                               input=json.dumps({"text": t, "session": "cli"}), capture_output=True, text=True, env=env)
            outs.append(json.loads(p.stdout))
        check(outs[1]["cartridge"] == "code" and outs[1]["path"] == "hold" and outs[1]["turn"] == 2 and outs[1]["answer"] == "pong",
              "command: two runs with the same session continue the conversation")
        check(not (ROOT / "configs/opg/.sessions/cli.json").exists(), "command: the test left nothing in the real .sessions/")
    finally:
        srv.shutdown()
        srv.server_close()
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
if failures:
    print(f"FAILED: {len(failures)} check(s)")
    sys.exit(1)
print("ALL PASSED")
