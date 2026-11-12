#pragma once
#include "../include/myln/head.h"

namespace myln {

// ── PassthroughHead ────────────────────────────────────────
// ルーターが出力した信号をそのまま通す（恒等写像）。
// チューニング時に使用: 信号が他の層で変形されないようにする。
class PassthroughHead : public Head {
    std::string name_;
public:
    explicit PassthroughHead(std::string name) : name_(std::move(name)) {}
    std::string name() const override { return name_; }
    Vec forward(const Vec& x, int /*dim*/) override { return x; }
};

} // namespace myln
