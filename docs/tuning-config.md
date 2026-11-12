# MYLN tuning config (`myln-tune/1`)

汎用チューニング設定。Router の重み(スロットごとの W, b)、Ring のパラメータ、
Center Line の W_cls を **JSON で外部から設定する**ためのフォーマットです(README ロードマップの `.mhead` 相当)。

Settings for the generic tuner: Router weights (per-slot W, b), Ring parameters and the
Center Line classifier (W_cls), all loaded from JSON instead of being hard-coded in C++.

## API

| Language | Frame | Cascade |
|---|---|---|
| C | `int myln_tune_custom(void* frame, const char* path_or_json)` | `int myln_cascade_tune_custom(void* cas, const char* path_or_json)` |
| C++ | `myln::tune_custom(frame, path_or_json)` | `myln::tune_cascade_custom(cascade, path_or_json)` |
| Python | `MylnFrame.tune_custom(config)` | `MylnCascade.tune_custom(config)` |

- `path_or_json`: 先頭が `{` なら JSON 文字列、それ以外はファイルパス。Python は `dict` も可。
- C は 0=成功 / -1=失敗(理由は `myln_last_error()`)。Python は `ValueError`。
- **検証してから適用**します。1か所でも不正なら frame は一切変更されません。
- 未知のキーはエラー(typo 検出)。省略したセクションは **変更されません**。

## Frame config

```jsonc
{
  "schema": "myln-tune/1",          // 任意(情報用)
  "name": "my-config",              // 任意
  "description": "...",             // 任意
  "classes":  ["A", "B", "C"],      // 任意。長さ = n_classes。Python の predict() ラベルに使われる
  "features": ["f0", "f1"],         // 任意(情報用)
  "size": "T",                      // 任意。指定すると frame の SS/T/S と一致必須
  "n_classes": 5,                   // 任意。指定すると frame と一致必須
  "in_dim": 5,                      // router がある場合は必須(入力特徴量の数)

  "router": {
    "normalize": false,             // 任意。true だと Router 出力に layer_norm(既定の挙動)
    "slots": [ {...}, {...}, {...}, {...} ]   // ちょうど4つ
  },
  "heads": [ ... 4つ ... ],
  "ring":  { ... },
  "center": { ... }
}
```

### router.slots[i]

スロット `i` の射影 `out = W·x + b`。`W` は `dim × in_dim`(行 = 出力次元, 列 = 入力特徴量)。
すべて 0 から始まり、書いた項目だけ上書きされます。`dim` は frame サイズで決まります(SS=16, T=64, S=128)。

| key | 形 | 意味 |
|---|---|---|
| `terms` | `[[out, in, value], ...]` | `W[out][in] = value`(疎な指定) |
| `bias`  | `[[out, value], ...]` | `b[out] = value` |
| `W`     | `[[...], ...]` (`dim` 行 × `in_dim` 列) | 密な指定。`terms` より先に適用される |

### heads

4要素。文字列(`"passthrough"` など)か、オブジェクト `{ "type": ..., "name": ..., "seed": ... }`。

| type | 動作 |
|---|---|
| `passthrough` | 入力をそのまま通す(恒等) |
| `zero` | 常に 0(使わないスロット) |
| `default` | 2層 MLP(`seed` 指定でランダム初期化。既定はスロット番号) |

### ring

| key | 意味 |
|---|---|
| `"mode": "near_identity"` | `self`(既定 0.6)と `neighbor`(既定 0.2)の加重平均 |
| `"mode": "custom"` | `slots`(4つ)に `terms` / `bias`。`W` は `dim × (3·dim)`、列は `[0,dim)=自分, [dim,2dim)=左, [2dim,3dim)=右` |

### center

| key | 意味 |
|---|---|
| `normalize_agg` | 集約後の layer_norm の ON/OFF(チューニング用途では通常 `false`) |
| `key` | `"identity"` のみ。キー射影を恒等写像にする |
| `query` | `[[dim_index, value], ...]`(その他は 0) |
| `cls.terms` | `[[class, dim_index, value], ...]` = `W_cls[class][dim_index]` |
| `cls.bias`  | `[[class, value], ...]` = `b_cls[class]` |
| `cls.W`     | 密な指定(`n_classes` 行 × `dim` 列) |

分類は `softmax(W_cls · agg + b_cls)`。`agg` はスロット出力のアテンション平均です。

## Cascade config

```jsonc
{
  "threshold": 0.80,        // 確信度の閾値 [0,1]
  "relay": { /* Frame config (SS) */ },
  "full":  { /* Frame config (T)  */ }
}
```

`relay`/`full` の両方が必須です。リレーの確信度が `threshold` 以上ならそのまま出力、未満ならフルで再判定します。

出口の数(クラス数)は既定で 5 です。5 以外にしたいときは、カスケードを作るときに指定します
(C: `myln_cascade_new_n(threshold, n_classes)` / C++: `CascadeFrame(threshold, n_classes)` /
Python: `MylnCascade(threshold, n_classes=6)`)。例: `configs/opg/cascade.json`(Opg の6穴)。

## 同梱サンプル(`configs/`)

| file | 内容 | 同等のC++ |
|---|---|---|
| `difficulty.json` | 依頼文の難易度(Frame) | `tune_difficulty()` |
| `difficulty_cascade.json` | 依頼文の難易度(Cascade) | `tune_cascade_difficulty()` |

これらは C++ チューナーと**ビット単位で同じ確率**を返すことをテスト(`tests/regression.py`)で確認しています。

## 例: 最小の設定

特徴量 0 だけで 2 クラスに分ける(`MylnFrame("T", 2)` に適用):

```json
{
  "in_dim": 1,
  "router": { "normalize": false, "slots": [ { "terms": [[0, 0, 1.0]] }, {}, {}, {} ] },
  "heads": ["passthrough", "zero", "zero", "zero"],
  "ring": { "mode": "near_identity", "self": 1.0, "neighbor": 0.0 },
  "center": {
    "normalize_agg": false, "key": "identity", "query": [[0, 3.0]],
    "cls": { "terms": [[0, 0, -4.0], [1, 0, 4.0]], "bias": [[0, 2.0], [1, -2.0]] }
  }
}
```

(x が小さいとクラス0、大きいとクラス1。境界は x≈1.3 付近で、`W_cls` の得点の交点(x=0.5)ではない。
Center Line がスロットをアテンションで平均するため、使うスロットが1つだと agg[0] が x より小さくなるからです。
境界を狙って決めたいときは `difficulty.json` のように4スロットを揃えて使い、実際の確率を `infer()` で確かめてください。)
