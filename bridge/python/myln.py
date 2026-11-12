"""
MYLN-FRAME Python Bridge
========================
ctypes経由でC APIを呼ぶ薄いラッパー。
GPUもフレームワークも不要。

Usage:
    from myln import MylnFrame

    frame = MylnFrame(size="T", n_classes=5)
    frame.tune_difficulty()

    probs  = frame.infer([0.67, 0.05, 0.0, 0.0, 0.67])   # [tech, length, steps, scope, reasoning]
    label  = frame.predict([0.67, 0.05, 0.0, 0.0, 0.67])
    print(label)  # → MEDIUM など
"""

import ctypes
import json
import os
import sys
from pathlib import Path
from typing import Optional

# ── libmyln の検索 ────────────────────────────────────────
def _find_lib() -> str:
    env = os.environ.get("MYLN_LIB")   # テスト・開発ビルド用の明示指定
    if env:
        return env
    candidates = [
        Path(__file__).parent.parent.parent / "build" / "libmyln.so",
        Path(__file__).parent.parent.parent / "build" / "libmyln.dylib",
        Path(__file__).parent.parent.parent / "build" / "myln.dll",
        Path("libmyln.so"),
        Path("libmyln.dylib"),
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    raise FileNotFoundError(
        "libmyln が見つかりません。まず build/ でビルドしてください。\n"
        "  cd build && cmake .. && make myln"
    )


# ── 難易度判定チューナー（tuner/difficulty_tuner.h）の定数 ──
# 入力 features: [tech, length, steps, scope, reasoning]  各 0.0〜1.0
DIFFICULTY_CLASSES    = ["CHAT", "EASY", "MEDIUM", "HARD", "EXTREME"]
DIFFICULTY_CLASSES_JA = ["雑談", "易", "中", "難", "最難"]
DIFFICULTY_FEATURES   = ["tech", "length", "steps", "scope", "reasoning"]


def _default_labels(n: int) -> list:
    """クラス名が決まっていないときの仮ラベル。"""
    return [f"class{i}" for i in range(n)]


# ── C API バインディング ──────────────────────────────────
class _CAPI:
    def __init__(self, lib_path: Optional[str] = None):
        path = lib_path or _find_lib()
        lib = ctypes.CDLL(path)

        lib.myln_new.restype  = ctypes.c_void_p
        lib.myln_new.argtypes = [ctypes.c_char_p, ctypes.c_int]

        lib.myln_free.restype  = None
        lib.myln_free.argtypes = [ctypes.c_void_p]

        lib.myln_infer.restype  = ctypes.POINTER(ctypes.c_float)
        lib.myln_infer.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
        ]

        lib.myln_tag.restype       = ctypes.c_char_p
        lib.myln_tag.argtypes      = [ctypes.c_void_p]
        lib.myln_dim.restype       = ctypes.c_int
        lib.myln_dim.argtypes      = [ctypes.c_void_p]
        lib.myln_n_classes.restype = ctypes.c_int
        lib.myln_n_classes.argtypes= [ctypes.c_void_p]
        lib.myln_version.restype   = ctypes.c_char_p
        lib.myln_version.argtypes  = []

        # カスケード API
        lib.myln_cascade_new.restype  = ctypes.c_void_p
        lib.myln_cascade_new.argtypes = [ctypes.c_float]
        lib.myln_cascade_free.restype  = None
        lib.myln_cascade_free.argtypes = [ctypes.c_void_p]
        lib.myln_cascade_infer.restype  = ctypes.POINTER(ctypes.c_float)
        lib.myln_cascade_infer.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_float),
            ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)
        ]
        lib.myln_cascade_relay_rate.restype  = ctypes.c_float
        lib.myln_cascade_relay_rate.argtypes = [ctypes.c_void_p]

        # 拡張パッケージ用フック（古い libmyln には無い）
        if hasattr(lib, "myln_frame_native"):
            lib.myln_frame_native.restype  = ctypes.c_void_p
            lib.myln_frame_native.argtypes = [ctypes.c_void_p]

        # 難易度判定チューナー（古い libmyln には無いので、あれば束縛）
        if hasattr(lib, "myln_tune_difficulty"):
            lib.myln_tune_difficulty.restype  = None
            lib.myln_tune_difficulty.argtypes = [ctypes.c_void_p]
            lib.myln_cascade_tune_difficulty.restype  = None
            lib.myln_cascade_tune_difficulty.argtypes = [ctypes.c_void_p]

        # 汎用チューニング API（古い libmyln には無いので、あれば束縛）
        if hasattr(lib, "myln_tune_custom"):
            lib.myln_tune_custom.restype  = ctypes.c_int
            lib.myln_tune_custom.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
            lib.myln_cascade_tune_custom.restype  = ctypes.c_int
            lib.myln_cascade_tune_custom.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
            lib.myln_last_error.restype  = ctypes.c_char_p
            lib.myln_last_error.argtypes = []

        self.lib = lib

    def require(self, fn_name: str) -> None:
        if not hasattr(self.lib, fn_name):
            raise RuntimeError(f"この libmyln は {fn_name} に未対応です。再ビルドしてください。")

    def tune_custom(self, fn_name: str, handle, config) -> dict:
        """JSON設定(パス / JSON文字列 / dict)を適用し、解析済みの設定dictを返す。"""
        self.require(fn_name)
        if isinstance(config, dict):
            text = json.dumps(config)
        else:
            text = str(config)
            if not text.lstrip().startswith("{"):
                text = str(Path(text).expanduser().resolve())   # ファイルパス
        rc = getattr(self.lib, fn_name)(handle, text.encode("utf-8"))
        if rc != 0:
            raise ValueError(self.lib.myln_last_error().decode("utf-8", "replace"))
        if text.lstrip().startswith("{"):
            return json.loads(text)
        return json.loads(Path(text).read_text(encoding="utf-8"))

    def version(self) -> str:
        return self.lib.myln_version().decode()


# ── メインクラス ──────────────────────────────────────────
class MylnFrame:
    """
    MYLN-FRAME の Python ラッパー。
    どのプラットフォームでも ctypes だけで動く量産型ブリッジ。
    """

    def __init__(
        self,
        size: str = "T",
        n_classes: int = 5,
        lib_path: Optional[str] = None,
    ):
        self._api    = _CAPI(lib_path)
        self._handle = self._api.lib.myln_new(size.encode(), n_classes)
        if not self._handle:
            raise RuntimeError(f"myln_new({size}, {n_classes}) failed")
        self._n_classes = n_classes
        self.classes = None    # tune_custom() の "classes"
        self.features = None   # tune_custom() の "features"

    def __del__(self):
        if hasattr(self, "_handle") and self._handle:
            self._api.lib.myln_free(self._handle)
            self._handle = None

    # ── チューニング ────────────────────────────────────────
    def tune_difficulty(self) -> "MylnFrame":
        """
        依頼文の難易度判定用にチューニングする（n_classes=5 のフレームで使う）。
        features: [tech, length, steps, scope, reasoning]（各 0.0〜1.0）
        クラス: 0=CHAT(雑談) 1=EASY(易) 2=MEDIUM(中) 3=HARD(難) 4=EXTREME(最難)
        """
        self._api.require("myln_tune_difficulty")
        self._api.lib.myln_tune_difficulty(self._handle)
        self.classes = list(DIFFICULTY_CLASSES)
        self.features = list(DIFFICULTY_FEATURES)
        return self

    def tune_custom(self, config) -> "MylnFrame":
        """
        JSON設定でチューニングする（仕様: docs/tuning-config.md）。
        config: ファイルパス / JSON文字列 / dict。不正な設定は ValueError（frame は変更されない）。
        設定に "classes" があれば predict() のラベルに使われる。
        """
        spec = self._api.tune_custom("myln_tune_custom", self._handle, config)
        self.classes = spec.get("classes") or None
        self.features = spec.get("features") or None
        return self

    # ── 推論 ───────────────────────────────────────────────
    def infer(self, features: list) -> list:
        """
        特徴量リストを渡してクラス確率を返す。
        features: チューニングで決めた入力(各 0.0〜1.0)。self.features に名前が入る。
        """
        n_in  = len(features)
        arr   = (ctypes.c_float * n_in)(*features)
        n_out = ctypes.c_int(0)
        ptr   = self._api.lib.myln_infer(
            self._handle, arr, n_in, ctypes.byref(n_out)
        )
        return [ptr[i] for i in range(n_out.value)]

    def predict(self, features: list, classes: Optional[list] = None) -> str:
        """最も確率の高いクラス名を返す。"""
        probs  = self.infer(features)
        labels = classes or self.classes or _default_labels(len(probs))
        return labels[probs.index(max(probs))]

    def predict_with_score(self, features: list, classes: Optional[list] = None):
        """(クラス名, 確率) のタプルを返す。"""
        probs  = self.infer(features)
        labels = classes or self.classes or _default_labels(len(probs))
        best   = probs.index(max(probs))
        return labels[best], probs[best]

    # ── メタ情報 ───────────────────────────────────────────
    @property
    def native_ptr(self) -> int:
        """myln::Frame* (拡張パッケージ用。C++側で Frame を直接設定するときに渡す)。"""
        self._api.require("myln_frame_native")
        return self._api.lib.myln_frame_native(self._handle)

    @property
    def tag(self)       -> str: return self._api.lib.myln_tag(self._handle).decode()
    @property
    def dim(self)       -> int: return self._api.lib.myln_dim(self._handle)
    @property
    def n_classes(self) -> int: return self._api.lib.myln_n_classes(self._handle)
    @property
    def version(self)   -> str: return self._api.version()

    def __repr__(self):
        return f"MylnFrame(tag={self.tag!r}, dim={self.dim}, classes={self.n_classes})"


# ── カスケード（2段リレー）────────────────────────────────────
class MylnCascade:
    """
    2段カスケード分類器。
    リレー（SS 2頭）で高速判定 →
    確信度が低ければ フル（T 4頭）へ。

    Usage:
        cas = MylnCascade(threshold=0.80).tune_difficulty()
        label, conf, used_relay = cas.predict_with_path([0.67,0.05,0.0,0.0,0.67])
    """

    def __init__(self, threshold: float = 0.80, lib_path: Optional[str] = None):
        self._api    = _CAPI(lib_path)
        self._handle = self._api.lib.myln_cascade_new(ctypes.c_float(threshold))
        if not self._handle:
            raise RuntimeError("myln_cascade_new() failed")
        self.classes = None
        self.features = None

    def __del__(self):
        if hasattr(self, "_handle") and self._handle:
            self._api.lib.myln_cascade_free(self._handle)

    def tune_difficulty(self) -> "MylnCascade":
        """
        依頼文の難易度判定用にチューニングする（リレー: tech+scope / フル: 4スロット）。
        features: [tech, length, steps, scope, reasoning]（各 0.0〜1.0）
        クラス: 0=CHAT(雑談) 1=EASY(易) 2=MEDIUM(中) 3=HARD(難) 4=EXTREME(最難)
        """
        self._api.require("myln_cascade_tune_difficulty")
        self._api.lib.myln_cascade_tune_difficulty(self._handle)
        self.classes = list(DIFFICULTY_CLASSES)
        self.features = list(DIFFICULTY_FEATURES)
        return self

    def tune_custom(self, config) -> "MylnCascade":
        """JSON設定(relay/full 2セクション + threshold)でチューニングする。"""
        spec = self._api.tune_custom("myln_cascade_tune_custom", self._handle, config)
        self.classes = spec.get("classes") or None
        self.features = spec.get("features") or None
        return self

    def infer(self, features: list) -> tuple:
        """(probs, used_relay) を返す"""
        n_in  = len(features)
        arr   = (ctypes.c_float * n_in)(*features)
        n_out = ctypes.c_int(0)
        relay = ctypes.c_int(0)
        ptr   = self._api.lib.myln_cascade_infer(
            self._handle, arr, n_in,
            ctypes.byref(n_out), ctypes.byref(relay)
        )
        probs = [ptr[i] for i in range(n_out.value)]
        return probs, bool(relay.value)

    def predict(self, features: list) -> str:
        probs, _ = self.infer(features)
        return (self.classes or _default_labels(len(probs)))[probs.index(max(probs))]

    def predict_with_path(self, features: list) -> tuple:
        """(クラス名, 確信度, リレー使用?) を返す"""
        probs, used_relay = self.infer(features)
        best = probs.index(max(probs))
        return (self.classes or _default_labels(len(probs)))[best], probs[best], used_relay

    @property
    def relay_rate(self) -> float:
        return self._api.lib.myln_cascade_relay_rate(self._handle)
