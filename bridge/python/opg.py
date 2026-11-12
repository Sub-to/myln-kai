"""
Opg (Opt + Generative) ── リボルバー式カートリッジ選択
=====================================================
入力文を見て「弾倉の6穴のうち、どのカートリッジ(専門家)で答えるか」を決めるだけの部品。
生成はしない(=空撃ち)。弾倉に無い答えは絶対に返さない。
速い道はモデルを一切動かさない。丁寧な道だけ、手元の小型モデルで文を数字にする(生成はしない・ネット不要)。

  入力文
    ↓ ① 速い道: 名札の言葉の手がかり → 特徴量10個 → MYLN カスケード(configs/opg/cascade.json)
    ↓      リレーが自信あり(80%以上) → 決定 / なければフルで再判定(弱い手がかりも見る)
    ↓ ② 丁寧な道: 手元の小型モデルの埋め込みで、名札の例文との近さを比べる(opg_embed.py)
    ↓      速い道が専門家に決められなかったときだけ動く。モデルが無ければ飛ばす
    ↓ ③ どちらも自信なし → 0番「汎用」に戻す

  特徴量 = [強1..強5, 弱1..弱5]   強 = 名札の keywords / patterns に当たった数
                                   弱 = 名札の hints / hint_patterns に当たった数
  0番(汎用)は手がかりを持たない。カスケード側の「基準点」より点数が高い専門家だけが選ばれる。

弾倉   : configs/opg/magazine.json      (6穴の一覧。環境変数 OPG_MAGAZINE か --magazine で差し替え)
名札   : configs/opg/cartridges/<id>.json(カートリッジ1個ぶん。足す・差し替えるだけで増やせる)
難易度 : difficulty.py の判定(0〜4)をそのまま併記する(別軸)

使い方:
  echo '{"text":"main.pyのバグを直して"}' | python3 bridge/python/opg.py
    → {"cartridge":"code","slot":2,"conf":0.97,"runner_up":"general","level":1,"path":"relay","reason":"..."}
  python3 bridge/python/opg.py --demo      # 名札の例文を全部、速い道だけで空撃ち
  python3 bridge/python/opg.py --debug     # 特徴量と6穴の点数も出す
  python3 bridge/python/opg.py --fast-only # 丁寧な道を使わない(環境変数 OPG_CAREFUL=0 でも同じ)

path の意味: relay=リレーで即決 / full=フルで決定 / embed=丁寧な道で決定 / fallback=自信なしで汎用に戻した
"""
import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
from myln import MylnCascade  # noqa: E402
import difficulty  # noqa: E402
import opg_embed  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAGAZINE = ROOT / "configs" / "opg" / "magazine.json"
N_SLOTS = 6           # 弾倉は6穴(カスケード設定も6穴ぶんで作ってある)
FALLBACK_SLOT = 0     # 戻り先=汎用。0番固定


def norm(s: str) -> str:
    """全角英数→半角、大文字→小文字にそろえる(「Ｐｙｔｈｏｎ」「API」も同じ語として当てる)。"""
    return unicodedata.normalize("NFKC", s).casefold()


def clip(x: float) -> float:
    return max(0.0, min(1.0, x))


class Cartridge:
    """名札1枚。keywords/hints は部分一致、patterns/hint_patterns は正規表現。"""

    def __init__(self, tag: dict, src: str):
        for key in ("id", "name"):
            if not isinstance(tag.get(key), str) or not tag[key]:
                raise ValueError(f"{src}: 名札に '{key}' がありません")
        self.id, self.name, self.tag = tag["id"], tag["name"], tag
        self.examples = list(tag.get("examples", []))
        try:
            self.strong = [(k, re.compile(re.escape(norm(k)))) for k in tag.get("keywords", [])] + \
                          [(p, re.compile(p)) for p in tag.get("patterns", [])]
            self.weak = [(k, re.compile(re.escape(norm(k)))) for k in tag.get("hints", [])] + \
                        [(p, re.compile(p)) for p in tag.get("hint_patterns", [])]
        except re.error as e:
            raise ValueError(f"{src}: 正規表現が不正です ({e})")

    @staticmethod
    def _hits(rules, text):
        out = []
        for _, rx in rules:
            m = rx.search(text)
            if m and m.group(0) not in out:
                out.append(m.group(0))
        # 他の当たりに含まれる短い当たりは数えない(「まとめて」と「まとめ」の二重取りを防ぐ)
        return [h for h in out if not any(h != o and h in o for o in out)]

    def hits(self, text: str):
        strong = self._hits(self.strong, text)
        # 強い手がかりの一部でしかない弱い手がかりは数えない(「csv」と「.csv」の二重取りを防ぐ)
        weak = [w for w in self._hits(self.weak, text) if not any(w in s for s in strong)]
        return strong, weak


class Magazine:
    """弾倉。6穴ぶんの名札を読み込み、カスケードを設定する。"""

    def __init__(self, path=None, careful: Optional[bool] = None):
        """careful: 丁寧な道を使うか。None=弾倉ファイルの設定と環境変数 OPG_CAREFUL(0で切る)に従う。"""
        self.path = Path(path or os.environ.get("OPG_MAGAZINE") or DEFAULT_MAGAZINE).expanduser().resolve()
        spec = json.loads(self.path.read_text(encoding="utf-8"))
        ids = spec.get("slots")
        if not isinstance(ids, list) or len(ids) != N_SLOTS:
            raise ValueError(f"{self.path.name}: 'slots' はちょうど{N_SLOTS}個にしてください")
        if not ids[FALLBACK_SLOT]:
            raise ValueError(f"{self.path.name}: 0番(戻り先の汎用)は空にできません")
        loaded = [i for i in ids if i]
        if len(set(loaded)) != len(loaded):
            raise ValueError(f"{self.path.name}: 同じカートリッジが2つの穴に入っています")
        cdir = self.path.parent / spec.get("cartridge_dir", "cartridges")
        self.slots = []                      # 穴ごとの Cartridge(空の穴は None)
        for cid in ids:
            if not cid:
                self.slots.append(None)
                continue
            f = cdir / f"{cid}.json"
            if not f.exists():
                raise ValueError(f"{self.path.name}: 名札 {f} が見つかりません")
            cart = Cartridge(json.loads(f.read_text(encoding="utf-8")), f.name)
            if cart.id != cid:
                raise ValueError(f"{f.name}: id が '{cart.id}' です(ファイル名と同じ '{cid}' にしてください)")
            self.slots.append(cart)
        self.strong_per = float(spec.get("strong_per", 3))   # 強い手がかり何個で満点か
        self.weak_per = float(spec.get("weak_per", 2))       # 弱い手がかり何個で満点か
        self.min_conf = float(spec.get("min_conf", 0.55))    # これ未満の自信なら汎用に戻す
        self.cascade = MylnCascade(threshold=0.80, n_classes=N_SLOTS).tune_custom(
            self.path.parent / spec.get("cascade", "cascade.json"))
        # ② 丁寧な道の設定(実際にモデルを読むのは最初に必要になったとき)
        cf = spec.get("careful") or {}
        if careful is None:
            careful = bool(cf.get("enabled", False)) and os.environ.get("OPG_CAREFUL", "1") != "0"
        self.careful = careful
        self.careful_cfg = {"pooling": str(cf.get("pooling", "mean")), "top_k": int(cf.get("top_k", 2)),
                            "min_score": float(cf.get("min_score", 0.3)), "min_margin": float(cf.get("min_margin", 0.05))}
        self._bank = None

    def bank(self):
        """例文の埋め込み。丁寧な道が使えないとき(切ってある・モデルが無い)は None。"""
        if not self.careful:
            return None
        if self._bank is None:
            self._bank = False
            model = opg_embed.find_model(self.path.parent)
            if model:
                emb = opg_embed.Embedder(model, self.careful_cfg["pooling"])
                examples = [(ex, i) for i, c in enumerate(self.slots) if c for ex in c.examples]
                bank = opg_embed.ExampleBank(emb, examples, self.path.parent / ".cache")
                if bank.ready:
                    self._bank = bank
        return self._bank or None

    def features(self, text: str):
        """→ (特徴量10個, 穴ごとの (強い当たり, 弱い当たり))。空の穴と0番は常に 0。"""
        t = norm(text)
        hits = [((), ())] * N_SLOTS
        strong, weak = [0.0] * (N_SLOTS - 1), [0.0] * (N_SLOTS - 1)
        for slot in range(1, N_SLOTS):
            cart = self.slots[slot]
            if cart is None:
                continue
            s, w = cart.hits(t)
            hits[slot] = (s, w)
            strong[slot - 1] = clip(len(s) / self.strong_per)
            weak[slot - 1] = clip(len(w) / self.weak_per)
        return strong + weak, hits


def careful_path(text: str, magazine: Magazine, probs: list) -> Optional[dict]:
    """② 丁寧な道: 手元の小型モデルの埋め込みで、入力文と各名札の examples との近さを比べる。

    自信があれば {"slot": 穴番号, "runner_up": 次点の穴, "conf": 0.5〜1.0, "reason": "例文「…」に近い"} を返し、
    自信がない・使えない(切ってある、モデルが無い)ときは None を返す(None なら ③ 汎用に戻る)。
    slot は弾倉に入っている穴だけ(例文は入っているカートリッジの名札からしか取らない)。
    中身は opg_embed.py。probs(速い道の6穴の点数)はいまは使っていない。
    """
    bank = magazine.bank()
    if bank is None:
        return None
    c = magazine.careful_cfg
    return bank.judge(text, c["top_k"], c["min_score"], c["min_margin"])


def _quote(words) -> str:
    return "".join(f"「{w}」" for w in list(words)[:4])


def select(text: str, magazine: Magazine, debug: bool = False) -> dict:
    """空撃ち: どの穴を使うかだけ決めて返す。返すのは必ず弾倉に入っているカートリッジ。"""
    feats, hits = magazine.features(text)
    probs, used_relay = magazine.cascade.infer(feats)
    loaded = [i for i in range(N_SLOTS) if magazine.slots[i] is not None]
    order = sorted(loaded, key=lambda i: -probs[i])          # 空の穴は最初から候補に入れない
    top = order[0]
    conf = probs[top]

    def why(slot):
        s, w = hits[slot]
        if not (s or w):
            return "当てはまる手がかりなし"
        parts = ([_quote(s) + " の語"] if s else []) + ([_quote(w) + " の言い回し"] if w else [])
        return magazine.slots[slot].name + ": " + "、".join(parts)

    sure = used_relay or conf >= magazine.min_conf
    runner = None
    if sure and top != FALLBACK_SLOT:                        # ① 速い道が専門家に決めた
        slot, path, reason = top, ("relay" if used_relay else "full"), why(top)
    else:
        hit = careful_path(text, magazine, probs)            # ② 丁寧な道(使えないときは None)
        if hit and hit.get("slot") in loaded:
            slot, path, conf = hit["slot"], "embed", float(hit.get("conf", 0.0))
            reason = str(hit.get("reason") or "例文に近い")
            runner = hit.get("runner_up") if hit.get("runner_up") in loaded else None
        elif sure:                                           # 速い道が「手がかりなし」と判断 → 汎用
            slot, path, reason = FALLBACK_SLOT, ("relay" if used_relay else "full"), "専門家の手がかりが足りないので汎用"
        else:                                                # ③ どちらも自信なし → 汎用
            slot, path, conf = FALLBACK_SLOT, "fallback", probs[FALLBACK_SLOT]
            near = [i for i in order if i != FALLBACK_SLOT and (hits[i][0] or hits[i][1])][:2]
            reason = "決め手に欠けるので汎用(候補: " + " / ".join(why(i) for i in near) + ")" if near \
                else "自信が低いので汎用"
    if runner is None or runner == slot:
        runner = next((i for i in order if i != slot), slot)
    out = {
        "cartridge": magazine.slots[slot].id,
        "slot": slot,
        "conf": round(conf, 3),
        "runner_up": magazine.slots[runner].id,
        "level": difficulty.classify(text)["level"],          # 難易度は既存の判定をそのまま併記
        "path": path,
        "reason": reason,
    }
    if debug:
        out["debug"] = {"features": [round(v, 2) for v in feats], "probs": [round(p, 3) for p in probs]}
    return out


if __name__ == "__main__":
    args = sys.argv[1:]
    mag_path = args[args.index("--magazine") + 1] if "--magazine" in args else None
    try:
        mag = Magazine(mag_path, careful=False if ("--fast-only" in args or "--demo" in args) else None)
    except (ValueError, OSError, IndexError) as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        sys.exit(1)
    if "--demo" in args:
        for cart in mag.slots:
            for ex in (cart.examples if cart else []):
                r = select(ex, mag)
                print(("○" if r["cartridge"] == cart.id else "×"), f"{r['slot']} {r['cartridge']:<9}{r['conf']:.2f} {r['path']:<9}", ex)
        sys.exit(0)
    req = json.loads(sys.stdin.read() or "{}")
    print(json.dumps(select(str(req.get("text", "")), mag, debug="--debug" in args), ensure_ascii=False))
