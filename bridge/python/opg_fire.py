"""
Opg 引き金 ── 選んだカートリッジで本体モデルに答えさせる
========================================================
opg.py が「どの穴か」を決め(空撃ち)、ここがその名札の指示書(prompt)を付けて
手元の本体モデルに送り、答えを受け取る。

  入力文 → opg.select(どのカートリッジか) → 名札の prompt を system に付ける
         → 手元のサーバー(OpenAI互換の /v1/chat/completions)へ送る → 答え

  - 送り先は自分のMacの中(127.0.0.1 / localhost)だけ。それ以外の宛先は送らずにエラーにする。
  - サーバーは自動では起動しない。動いていなければ、そう書いたエラーを返すだけ。
  - 本体モデル: 名札の base_model が書いてあればそれ、空なら設定の model。
  - 名札の lora はまだ使わない(書いてあっても読み飛ばし、結果の note に書く)。

設定: configs/opg/local.json の "fire"(Git管理外。見本は local.example.json)
  {"fire": {"endpoint": "http://127.0.0.1:11500/v1", "model": "モデル名",
            "max_tokens": 1024, "timeout": 300, "extra": {}}}
  環境変数 OPG_FIRE_ENDPOINT / OPG_FIRE_MODEL があればそちらを優先。
  extra はリクエストにそのまま足す項目(例: {"reasoning_effort": "none"})。

会話の続き(session): 入力に "session":"名前" を足すと、その名前の会話として続きになる。
  - これまでのやり取りを一緒に本体モデルへ送る(直近 history_turns 往復ぶん)。
  - 会話中のカートリッジを覚えていて、むやみに回さない(opg.hold)。回ったら指示書も入れ替わる。
  - 保存先は configs/opg/.sessions/<名前>.json(Git管理外。自分のMacの中だけ)。答えが返ったときだけ保存する。
  - "reset": true で、その会話を最初からやり直す。session を付けなければ今までどおり1問1答。
  python3 bridge/python/opg_fire.py --chat [名前]   # その場で続けて話す(/新規 でやり直し、/終了 で終わり。複数行は貼り付けてから Enter)

使い方:
  echo '{"text":"main.pyのバグの探し方を教えて"}' | python3 bridge/python/opg_fire.py
    → 空撃ちの7項目 + {"answer":"...","model":"...","seconds":3.2}
  python3 bridge/python/opg_fire.py --dry    # 送らずに、送る内容だけ見せる(モデルは動かさない)
  --fast-only / --magazine は opg.py と同じ。{"cartridge":"code"} を入力に足すと手で選べる(弾倉にあるものだけ)。
"""
import json
import os
import re
import select
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent))
import opg  # noqa: E402

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
DEFAULTS = {"endpoint": "", "model": "", "max_tokens": 1024, "timeout": 300, "extra": {}, "history_turns": 8}
MAX_SAVED_TURNS = 50          # 会話ファイルに残す往復の上限(古いものから消える)
SESSION_NAME = re.compile(r"[\w-]{1,64}")


class FireError(Exception):
    pass


def load_config(config_dir: Path) -> dict:
    """引き金の設定。local.json の "fire" に、環境変数を上書きして返す。"""
    cfg = dict(DEFAULTS)
    local = Path(config_dir) / "local.json"
    if local.exists():
        try:
            fire = json.loads(local.read_text(encoding="utf-8")).get("fire") or {}
        except (ValueError, OSError) as e:
            raise FireError(f"local.json が読めません ({e})")
        cfg.update({k: v for k, v in fire.items() if k in DEFAULTS})
    cfg["endpoint"] = os.environ.get("OPG_FIRE_ENDPOINT") or cfg["endpoint"]
    cfg["model"] = os.environ.get("OPG_FIRE_MODEL") or cfg["model"]
    return cfg


def check_endpoint(endpoint: str) -> str:
    """自分のMacの中の宛先だけ通す。→ chat/completions のURL。"""
    if not endpoint:
        raise FireError("引き金の送り先が未設定です(configs/opg/local.json の fire.endpoint)")
    u = urlparse(endpoint)
    if u.scheme not in ("http", "https") or u.hostname not in LOCAL_HOSTS:
        raise FireError(f"送り先 {endpoint} は自分のMacの中ではないので送りません(127.0.0.1 / localhost のみ)")
    return endpoint.rstrip("/") + "/chat/completions"


class Session:
    """会話1つぶんの記録: 今のカートリッジ(current)と、これまでのやり取り(turns)。"""

    def __init__(self, config_dir: Path, name: str, reset: bool = False):
        if not SESSION_NAME.fullmatch(name):
            raise FireError("会話の名前に使えるのは文字・数字・_・- だけです(64文字まで)")
        self.path = Path(config_dir) / ".sessions" / f"{name}.json"
        self.current, self.turns = "", []
        if self.path.exists() and not reset:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.current = str(data.get("current") or "")
                self.turns = [t for t in data.get("turns", [])
                              if isinstance(t, dict) and isinstance(t.get("user"), str) and isinstance(t.get("assistant"), str)]
            except (ValueError, OSError, AttributeError) as e:
                raise FireError(f"会話の記録 {self.path.name} が読めません ({e})")

    def messages(self, n_turns: int) -> list:
        out = []
        for t in self.turns[-n_turns:] if n_turns > 0 else []:
            out += [{"role": "user", "content": t["user"]}, {"role": "assistant", "content": t["assistant"]}]
        return out

    def add(self, user: str, assistant: str, cartridge: str):
        self.current = cartridge
        self.turns = (self.turns + [{"user": user, "assistant": assistant, "cartridge": cartridge}])[-MAX_SAVED_TURNS:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"current": self.current, "turns": self.turns}, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)


def build_request(text: str, cart: "opg.Cartridge", cfg: dict, history: Optional[list] = None) -> dict:
    """本体モデルに送る中身。名札の prompt が指示書(system)、history がこれまでのやり取り。"""
    model = cart.tag.get("base_model") or cfg["model"]
    if not model:
        raise FireError("本体モデルが未設定です(名札の base_model か local.json の fire.model)")
    messages = []
    prompt = str(cart.tag.get("prompt") or "").strip()
    if prompt:
        messages.append({"role": "system", "content": prompt})
    messages += history or []
    messages.append({"role": "user", "content": text})
    body = {"model": model, "messages": messages, "max_tokens": int(cfg["max_tokens"]), "stream": False}
    body.update(cfg.get("extra") or {})
    return body


def send(url: str, body: dict, timeout: float) -> str:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    # 手元の宛先だけなので、プロキシ設定は使わない
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as res:
            data = json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise FireError(f"本体モデルのサーバーがエラーを返しました(HTTP {e.code}): {detail}")
    except (urllib.error.URLError, OSError) as e:
        raise FireError(f"本体モデルのサーバーにつながりません({url})。起動しているか確認してください: {e}")
    except ValueError:
        raise FireError("本体モデルのサーバーの返事が読めませんでした")
    try:
        answer = data["choices"][0]["message"].get("content")
    except (KeyError, IndexError, TypeError, AttributeError):
        raise FireError("本体モデルの返事に答えが入っていませんでした")
    if not isinstance(answer, str) or not answer.strip():
        raise FireError("本体モデルの答えが空でした")
    return answer.strip()


def fire(text: str, magazine: "opg.Magazine", cfg: dict, manual: str = "", dry: bool = False,
         session: Optional[Session] = None) -> dict:
    """選んで、撃つ。→ 空撃ちの7項目 + answer / model / seconds(dry なら request)。失敗時は error。

    session があれば会話の続き: 今のカートリッジをむやみに回さず、これまでのやり取りも送り、答えが返ったら保存する。
    """
    out = opg.select(text, magazine)
    if session is not None:
        out = opg.hold(out, session.current, magazine)
        out["turn"] = len(session.turns) + 1
    if manual:                                                   # 手で選ぶ(弾倉に入っているものだけ)
        slot = next((i for i, c in enumerate(magazine.slots) if c and c.id == manual), None)
        if slot is None:
            out["error"] = f"カートリッジ '{manual}' は弾倉に入っていません"
            return out
        if slot != out["slot"]:
            out.update({"runner_up": out["cartridge"], "cartridge": manual, "slot": slot,
                        "conf": 1.0, "path": "manual", "reason": "手で指定"})
        else:
            out.update({"conf": 1.0, "path": "manual", "reason": "手で指定"})
    cart = magazine.slots[out["slot"]]
    try:
        if not text.strip():
            raise FireError("入力文が空です")
        url = check_endpoint(cfg["endpoint"])
        history = session.messages(int(cfg["history_turns"])) if session is not None else None
        body = build_request(text, cart, cfg, history)
        out["model"] = body["model"]
        if cart.tag.get("lora"):
            out["note"] = "名札の lora はまだ使っていません(指示書だけで答えています)"
        if dry:
            out["request"] = {"url": url, **body}
            return out
        t0 = time.time()
        out["answer"] = send(url, body, float(cfg["timeout"]))
        out["seconds"] = round(time.time() - t0, 1)
        if session is not None:
            session.add(text, out["answer"], out["cartridge"])
    except FireError as e:
        out["error"] = str(e)
    return out


def _pasted_rest() -> list:
    """貼り付けの残りの行(Enter を待たずにもう届いている行)を全部受け取る。画面から打っているときだけ働く。"""
    lines = []
    try:
        if not sys.stdin.isatty():
            return lines
        while select.select([sys.stdin], [], [], 0.05)[0]:
            line = sys.stdin.readline()
            if not line:
                break
            lines.append(line.rstrip("\n"))
    except (OSError, ValueError):
        pass
    return lines


def read_message(prompt: str = "あなた> ") -> str:
    """1回ぶんの発言を読む。複数行を貼り付けたときは、まとめて1つの発言にする(送るのは Enter を押してから)。"""
    first = input(prompt)
    rest = _pasted_rest()
    if rest:                                   # 貼り付けだった。最後の行は改行なしで残っていることがあるので Enter を待つ
        rest.append(input(f"({len(rest) + 1}行を受け取りました。Enter で送ります。続きを書き足してもかまいません)> "))
        rest += _pasted_rest()
    return "\n".join([first] + rest).strip()


def chat(magazine: "opg.Magazine", cfg: dict, name: str):
    """その場で続けて話す。1回の発言ごとに、選んだカートリッジの名前と答えを出す。"""
    session = Session(magazine.path.parent, name)
    print(f"会話「{name}」({len(session.turns)}往復ぶんの続きから)。/新規 でやり直し、/終了 で終わり。"
          "複数行のコードなどは、そのまま貼り付けてから Enter。")
    while True:
        try:
            text = read_message()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:                           # 何も書かずに Enter → 何もしない(貼り付けの中の空行で終わらないように)
            continue
        if text == "/終了":
            break
        if text == "/新規":
            session = Session(magazine.path.parent, name, reset=True)
            print("(最初からやり直します)")
            continue
        r = fire(text, magazine, cfg, session=session)
        mark = "(そのまま)" if r["path"] == "hold" else ""
        print(f"【{magazine.slots[r['slot']].name}{mark}】" + (r.get("answer") or "エラー: " + r.get("error", "")))


if __name__ == "__main__":
    args = sys.argv[1:]
    try:
        mag_path = args[args.index("--magazine") + 1] if "--magazine" in args else None
        mag = opg.Magazine(mag_path, careful=False if "--fast-only" in args else None)
        config = load_config(mag.path.parent)
        if "--chat" in args:
            i = args.index("--chat")
            chat(mag, config, args[i + 1] if len(args) > i + 1 and not args[i + 1].startswith("--") else "main")
            sys.exit(0)
        req = json.loads(sys.stdin.read() or "{}")
        sess = Session(mag.path.parent, str(req["session"]), reset=bool(req.get("reset"))) if req.get("session") else None
    except (ValueError, OSError, IndexError, FireError) as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        sys.exit(1)
    result = fire(str(req.get("text", "")), mag, config, manual=str(req.get("cartridge") or ""), dry="--dry" in args,
                  session=sess)
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(2 if "error" in result else 0)
