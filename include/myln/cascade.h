#pragma once
#include "frame.h"
#include <algorithm>

// ── CascadeFrame ───────────────────────────────────────────
//
// 2段カスケード: リレー（軽い）→ 確信度判定 → フル（重い）
//
//   入力
//    ↓
//  [RELAY]  ← SS + 2頭のみ active（proc + file）
//    ↓
//  確信度 >= threshold?
//    ├─ Yes（明確な入力）→ そのまま出力   ← 高速パス
//    └─ No（曖昧な入力） → [FULL] 4頭フル ← 精密パス
//
// 入力の「いい加減さ」が速さを決める:
//   明確: アイドル・ランサムウェア → リレーで即終了
//   曖昧: 微妙な特徴量の組み合わせ → フルで判定
//
// namespace myln

namespace myln {

class CascadeFrame {
    Frame relay_;       // 軽い: SS / 2頭(proc+file)
    Frame full_;        // 重い: T  / 4頭フル
    float threshold_;   // 確信度の閾値（0.0〜1.0）
    mutable int  relay_hits_  = 0;  // リレーで終わった回数
    mutable int  full_hits_   = 0;  // フルまで回った回数

public:
    // n_classes: 出口の数（既定 5 = 難易度判定。Opg の 6穴などは 6 を渡す）
    CascadeFrame(float threshold = 0.80f, int n_classes = 5)
        : relay_(SS, n_classes)
        , full_ (T,  n_classes)
        , threshold_(threshold)
    {}
    int n_classes() const { return relay_.n_classes(); }

    // ── チューニング用アクセス ──────────────────────────────
    Frame& relay() { return relay_; }
    Frame& full()  { return full_;  }
    void   set_threshold(float t) { threshold_ = t; }
    float  threshold() const { return threshold_; }

    // ── 推論 ───────────────────────────────────────────────
    // 戻り値: {probs, used_relay}
    struct Result {
        Vec  probs;
        bool used_relay;
    };

    Result run(const Vec& input) const {
        // 1. リレー（SS・2頭）で高速判定
        auto probs = const_cast<Frame&>(relay_).forward(input);
        float conf = *std::max_element(probs.begin(), probs.end());

        if (conf >= threshold_) {
            relay_hits_++;
            return {probs, true};
        }

        // 2. 確信が低い → フル（T・4頭）で精密判定
        full_hits_++;
        return {const_cast<Frame&>(full_).forward(input), false};
    }

    // monitor.py 互換: Vec だけ返すシンプル版
    Vec forward(const Vec& input) {
        return run(input).probs;
    }

    // ── 統計 ───────────────────────────────────────────────
    int   relay_hits()  const { return relay_hits_;  }
    int   full_hits()   const { return full_hits_;   }
    float relay_rate()  const {
        int total = relay_hits_ + full_hits_;
        return total ? (float)relay_hits_ / total : 0.f;
    }
    void  reset_stats() { relay_hits_ = full_hits_ = 0; }
};

} // namespace myln
