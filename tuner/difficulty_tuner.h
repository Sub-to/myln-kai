#pragma once
#include "../include/myln/frame.h"
#include "../include/myln/cascade.h"
#include "../heads/passthrough_head.h"

// ── 依頼文の難易度判定チューナー ──────────────────────────────
//
// 「依頼文 → 5特徴量 → 難易度クラス」を判定するための手動チューニング。
//
// 入力ベクトル（5次元・各 0.0〜1.0）:
//   [0] tech       技術度     コード/ファイル名/技術用語の多さ
//   [1] length     長さ       依頼文の長さ
//   [2] steps      手数       「まず/次に/最後に」など手順の数
//   [3] scope      影響範囲   複数ファイル・プロジェクト全体・成果物（最重要）
//   [4] reasoning  推論度     原因調査・比較・設計など考える必要の度合い
//
// 出力クラス:
//   0=CHAT(雑談)  1=EASY(易)  2=MEDIUM(中)  3=HARD(難)  4=EXTREME(最難)
//
// 難易度スコア（加算モデル）:
//   score = w_tech*tech + w_length*length + w_steps*steps + w_scope*scope + w_reasoning*reasoning
//   既定: 1.5 / 1.0 / 1.5 / 2.5 / 1.0   → scope が最も効く
//   技術用語が多いだけ（エラー文の質問など）では難しい扱いにならず、
//   範囲・手順・推論が重なるほど上がる。
//
// 実装（スロットの役割分担は維持したまま、結果がちょうど「重み付き和」になるようにしている）:
//   slot 0 → tech            slot 1 → steps
//   slot 2 → scope           slot 3 → length + reasoning
//   各スロットは「自分の担当の寄与 × 4」を dim[0] に出す。
//   Center Line のクエリを 0 にして 4スロットを一様に平均 → 平均 = 寄与の合計 = score。
//   Ring の self+左右 の重みの合計は 1.0（スコアを変えない）。
//
// 難易度スコア→クラスの閾値:
//   CHAT:    score < 0.5
//   EASY:    0.5 ≤ score < 1.5
//   MEDIUM:  1.5 ≤ score < 2.8
//   HARD:    2.8 ≤ score < 4.2
//   EXTREME: 4.2 ≤ score
//
// カスケード:
//   リレー（SS）もフル（T）も同じ加算モデル。リレーは分類の傾きを2倍にして確信度を高め、
//   確信度が閾値以上なら即決、スコアが閾値の境目に近い曖昧な入力だけフルで再判定する。
//   （リレーとフルは同じスコアを見るので、判定クラスは常に一致する。違いは速さだけ。）
//
// ※ 2026-10-03 改訂: 以前は別用途のチューナーの数値を流用しており、リレーが tech と scope しか
//    見ない・tech だけで最難になる等の偏りがあった（docs と報告を参照）。

namespace myln {

inline constexpr const char* DIFFICULTY_CLASS_NAMES[5]    = { "CHAT", "EASY", "MEDIUM", "HARD", "EXTREME" };
inline constexpr const char* DIFFICULTY_CLASS_NAMES_JA[5] = { "雑談", "易", "中", "難", "最難" };
inline constexpr const char* DIFFICULTY_FEATURE_NAMES[5]  = { "tech", "length", "steps", "scope", "reasoning" };

enum DifficultyFeature { DF_TECH = 0, DF_LENGTH = 1, DF_STEPS = 2, DF_SCOPE = 3, DF_REASONING = 4 };

// 共通パラメータ（score への寄与の重み）
struct DifficultyWeights {
    float w_tech      = 1.5f;
    float w_length    = 1.0f;
    float w_steps     = 1.5f;
    float w_scope     = 2.5f;   // 最重要
    float w_reasoning = 1.0f;
};

// フル（T）用パラメータ
struct DifficultyTuneParams {
    int in_dim = 5;
    DifficultyWeights w;
    float ring_self = 0.6f;     // self + 左右2つ = 1.0 になるようにする（スコアを保つ）
    float ring_nb   = 0.2f;
    // クラス i の得点 = slope_i * score + bias_i（交点が閾値 0.5/1.5/2.8/4.2）
    float slope[5] = { -1.0f, 0.5f, 1.5f, 2.5f, 3.5f };
    float bias [5] = {  9.25f, 8.50f, 7.00f, 4.20f, 0.00f };
};

// リレー（SS）用パラメータ: 同じスコアで、傾きを2倍にして確信度を高める
struct DifficultyRelayParams {
    int in_dim = 5;
    DifficultyWeights w;
    float ring_self = 0.6f;
    float ring_nb   = 0.2f;
    float slope[5] = { -2.0f, 1.0f, 3.0f, 5.0f, 7.0f };
    float bias [5] = { 18.5f, 17.0f, 14.0f, 8.4f, 0.0f };
};

namespace detail {
constexpr float DIFFICULTY_SLOTS = 4.0f;   // スロット数（一様平均で割られる分を先に掛けておく）

// 1スロット分: score(dim[0]) へ「(特徴量, 寄与の重み) × スロット数」を差し込む
inline void difficulty_slot(Frame& f, int slot, int in_dim,
                            std::initializer_list<std::pair<int, float>> fw) {
    Mat W((size_t)f.dim() * in_dim, 0.f);
    Vec b(f.dim(), 0.f);
    for (auto kv : fw) W[0 * in_dim + kv.first] = kv.second * DIFFICULTY_SLOTS;
    f.router().set_slot(slot, W, b);
}
inline void difficulty_frame(Frame& f, int in_dim, const DifficultyWeights& w,
                             float ring_self, float ring_nb, const float* slope, const float* bias) {
    const int dim = f.dim();
    f.init_router(in_dim);
    f.router().set_normalize(false);
    difficulty_slot(f, 0, in_dim, {{DF_TECH,  w.w_tech}});
    difficulty_slot(f, 1, in_dim, {{DF_STEPS, w.w_steps}});
    difficulty_slot(f, 2, in_dim, {{DF_SCOPE, w.w_scope}});
    difficulty_slot(f, 3, in_dim, {{DF_LENGTH, w.w_length}, {DF_REASONING, w.w_reasoning}});

    f.set_head(0, std::make_unique<PassthroughHead>("tech"));
    f.set_head(1, std::make_unique<PassthroughHead>("steps"));
    f.set_head(2, std::make_unique<PassthroughHead>("scope"));
    f.set_head(3, std::make_unique<PassthroughHead>("length_reasoning"));
    f.ring().set_near_identity(ring_self, ring_nb);

    f.center().set_normalize_agg(false);
    f.center().set_key_identity();
    f.center().set_query(Vec(dim, 0.f));          // クエリ 0 → 4スロットを一様平均
    Mat W(5 * (size_t)dim, 0.f); Vec b(5, 0.f);
    for (int c = 0; c < 5; ++c) { W[(size_t)c * dim + 0] = slope[c]; b[c] = bias[c]; }
    f.center().set_cls(W, b);
}
} // namespace detail

// ── フル（T）のチューニング ──────────────────────────────────
inline void tune_difficulty(Frame& frame, const DifficultyTuneParams& p = {}) {
    detail::difficulty_frame(frame, p.in_dim, p.w, p.ring_self, p.ring_nb, p.slope, p.bias);
}

// ── リレー（SS）のチューニング ───────────────────────────────
inline void tune_difficulty_relay(Frame& frame, const DifficultyRelayParams& p = {}) {
    detail::difficulty_frame(frame, p.in_dim, p.w, p.ring_self, p.ring_nb, p.slope, p.bias);
}

// ── CascadeFrame をまとめてチューニング ──────────────────────
inline void tune_cascade_difficulty(CascadeFrame& cascade, float threshold = 0.80f) {
    cascade.set_threshold(threshold);
    tune_difficulty_relay(cascade.relay());
    tune_difficulty      (cascade.full());
}

} // namespace myln
