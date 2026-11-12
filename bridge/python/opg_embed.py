"""
Opg ② 丁寧な道 ── 例文との近さで比べる
========================================
入力文と、弾倉に入っている各カートリッジの名札の examples(例文)を、手元の小型モデルの
「埋め込み」(文を数字の並びにしたもの)で比べ、いちばん近い例文を持つカートリッジを返す。

  - モデルは手元の GGUF ファイルを llama.cpp の `llama-embedding` コマンドで読むだけ。
    ネットワークには一切つながない。ダウンロードもしない。
  - モデルの場所: 環境変数 OPG_EMBED_MODEL → configs/opg/local.json の "embed_model" の順に探す。
    どちらも無い・ファイルが無い・llama-embedding が無い → 丁寧な道は「使えない」となり、速い道だけで動く。
  - 例文の埋め込みは configs/opg/.cache/ に保存する(2回目からは入力文1つだけ計算する)。
  - 名札に例文を足すだけで賢くなる。学習し直しは不要。

点数の付け方:
  全例文の平均を引いてから(=どの文にも共通する成分を消してから)コサイン類似度を取り、
  カートリッジごとに「近い順に top_k 個の平均」を点数にする。
  1位の点数が min_score 未満、または 2位との差が min_margin 未満なら「自信なし」(None)。
"""
import hashlib
import json
import math
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

MAX_CHARS = 400     # 長い入力は先頭だけ比べる


def _unit(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def find_model(config_dir: Path) -> Optional[str]:
    """埋め込みに使う GGUF の場所。見つからなければ None。"""
    path = os.environ.get("OPG_EMBED_MODEL")
    if not path:
        local = config_dir / "local.json"
        if local.exists():
            try:
                path = json.loads(local.read_text(encoding="utf-8")).get("embed_model")
            except (ValueError, OSError):
                path = None
    if not path:
        return None
    p = Path(path).expanduser()
    return str(p) if p.is_file() else None


class Embedder:
    """llama-embedding を1回呼んで、文のリスト → 埋め込みのリスト にする。"""

    def __init__(self, model: str, pooling: str = "mean", binary: Optional[str] = None, timeout: float = 120.0):
        self.model, self.pooling, self.timeout = model, pooling, timeout
        self.binary = binary or os.environ.get("OPG_EMBED_BIN") or shutil.which("llama-embedding")
        st = os.stat(model)
        self.key = hashlib.sha1(f"{model}|{st.st_size}|{int(st.st_mtime)}|{pooling}".encode()).hexdigest()[:16]

    def embed(self, texts: list) -> Optional[list]:
        if not self.binary or not texts:
            return None
        # llama-embedding は1行=1文として読む
        prompt = "\n".join((t.replace("\r", " ").replace("\n", " ").strip() or "。")[:MAX_CHARS] for t in texts) + "\n"
        tmp = Path(os.environ.get("TMPDIR", "/tmp")) / f"opg-embed-{os.getpid()}.txt"
        try:
            tmp.write_text(prompt, encoding="utf-8")
            r = subprocess.run([self.binary, "-m", self.model, "-f", str(tmp), "--pooling", self.pooling,
                                "--embd-output-format", "array", "-ngl", "99"],
                               capture_output=True, text=True, timeout=self.timeout)
            vecs = json.loads(r.stdout)
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
        if not isinstance(vecs, list) or len(vecs) != len(texts):
            return None
        return vecs


class ExampleBank:
    """弾倉の例文を埋め込みにして持っておく。"""

    def __init__(self, embedder: Embedder, examples: list, cache_dir: Path):
        """examples: [(例文, 穴番号), ...]"""
        self.embedder = embedder
        self.examples = list(examples)
        self.cache_file = cache_dir / f"embed_{embedder.key}.json"
        self.ready = False
        cache = {}
        if self.cache_file.exists():
            try:
                cache = json.loads(self.cache_file.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                cache = {}
        todo = [t for t in dict.fromkeys(t for t, _ in self.examples) if t not in cache]
        if todo:
            vecs = embedder.embed(todo)
            if vecs is None:
                return
            for t, v in zip(todo, vecs):
                cache[t] = [round(x, 5) for x in v]
            try:
                cache_dir.mkdir(parents=True, exist_ok=True)
                self.cache_file.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass
        if not self.examples:
            return
        raw = [cache[t] for t, _ in self.examples]
        dim = len(raw[0])
        self.mean = [sum(v[i] for v in raw) / len(raw) for i in range(dim)]
        self.vecs = [self._center(v) for v in raw]
        self.ready = True

    def _center(self, v):
        return _unit([x - m for x, m in zip(v, self.mean)])

    def rank(self, vec, top_k: int = 2, skip: Optional[int] = None):
        """→ [(穴番号, 点数, いちばん近い例文), ...] 点数の高い順。skip は無視する例文の番号(検証用)。"""
        q = self._center(vec)
        per = {}
        for i, ((text, slot), v) in enumerate(zip(self.examples, self.vecs)):
            if i == skip:
                continue
            per.setdefault(slot, []).append((sum(a * b for a, b in zip(q, v)), text))
        out = []
        for slot, sims in per.items():
            sims.sort(reverse=True)
            top = sims[:top_k]
            out.append((slot, sum(s for s, _ in top) / len(top), top[0][1]))
        out.sort(key=lambda x: -x[1])
        return out

    def judge(self, text: str, top_k: int, min_score: float, min_margin: float) -> Optional[dict]:
        if not self.ready or not text.strip():
            return None
        vecs = self.embedder.embed([text])
        if vecs is None or len(vecs[0]) != len(self.mean):
            return None
        ranked = self.rank(vecs[0], top_k)
        if len(ranked) < 2:
            return None
        (slot, score, near), (slot2, score2, _) = ranked[0], ranked[1]
        margin = score - score2
        if score < min_score or margin < min_margin:
            return None
        return {"slot": slot, "runner_up": slot2, "score": score, "margin": margin,
                "conf": 1.0 / (1.0 + math.exp(-margin / 0.05)),   # 2位との差を 0.5〜1.0 に写しただけの目安
                "reason": f"例文「{near}」に近い"}
