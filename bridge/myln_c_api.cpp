#include "myln_c_api.h"
#include "../include/myln/frame.h"
#include "../include/myln/cascade.h"
#include "../include/myln/tune_config.h"
#include "../tuner/difficulty_tuner.h"
#include <memory>
#include <string>
#include <vector>
#include <cstring>

// ── 内部コンテキスト ───────────────────────────────────────
struct MylnContext {
    std::unique_ptr<myln::Frame> frame;
    std::vector<float>           out_buf;   // 推論結果バッファ
};

static MylnContext* ctx(void* h) { return static_cast<MylnContext*>(h); }

// ── エラー保持（スレッドごと）─────────────────────────────
static thread_local std::string g_last_error;
const char* myln_last_error(void) { return g_last_error.c_str(); }

// ── フレームのライフサイクル ──────────────────────────────
void* myln_new(const char* size, int n_classes) {
    try {
        auto c = std::make_unique<MylnContext>();
        std::string s(size);
        if      (s == "SS") c->frame = std::make_unique<myln::Frame>(myln::SS, n_classes);
        else if (s == "T")  c->frame = std::make_unique<myln::Frame>(myln::T,  n_classes);
        else if (s == "S")  c->frame = std::make_unique<myln::Frame>(myln::S,  n_classes);
        else return nullptr;
        c->out_buf.resize(n_classes, 0.f);
        return c.release();
    } catch (...) { return nullptr; }
}

void myln_free(void* frame) {
    delete ctx(frame);
}

// ── チューニング ──────────────────────────────────────────
void myln_tune_difficulty(void* frame) {
    myln::tune_difficulty(*ctx(frame)->frame);
}

int myln_tune_custom(void* frame, const char* path_or_json) {
    g_last_error.clear();
    try {
        if (!frame || !path_or_json) throw std::runtime_error("null argument");
        myln::tune_custom(*ctx(frame)->frame, path_or_json);
        return 0;
    } catch (const std::exception& e) { g_last_error = e.what(); }
    catch (...) { g_last_error = "unknown error"; }
    return -1;
}

// ── 推論 ─────────────────────────────────────────────────
const float* myln_infer(void* frame, const float* features, int n_in, int* out_n) {
    auto* c = ctx(frame);
    myln::Vec input(features, features + n_in);
    auto probs = c->frame->forward(input);
    int  n     = (int)probs.size();
    c->out_buf.resize(n);
    std::copy(probs.begin(), probs.end(), c->out_buf.begin());
    if (out_n) *out_n = n;
    return c->out_buf.data();
}

// ── 拡張パッケージ用フック ────────────────────────────────
void* myln_frame_native(void* frame) { return frame ? ctx(frame)->frame.get() : nullptr; }

// ── メタ情報 ──────────────────────────────────────────────
const char* myln_tag      (void* frame) { return ctx(frame)->frame->tag(); }
int         myln_dim      (void* frame) { return ctx(frame)->frame->dim(); }
int         myln_n_classes(void* frame) { return ctx(frame)->frame->n_classes(); }
const char* myln_version  (void)        { return "0.1.0"; }

// ── カスケード ─────────────────────────────────────────────
struct CascadeCtx {
    myln::CascadeFrame cas;
    std::vector<float> out_buf;
    explicit CascadeCtx(float thr, int n_classes = 5) : cas(thr, n_classes) { out_buf.resize(n_classes, 0.f); }
};
static CascadeCtx* cctx(void* h) { return static_cast<CascadeCtx*>(h); }

void* myln_cascade_new(float threshold) {
    try { return new CascadeCtx(threshold); }
    catch (...) { return nullptr; }
}
void* myln_cascade_new_n(float threshold, int n_classes) {
    if (n_classes < 2 || n_classes > 4096) return nullptr;
    try { return new CascadeCtx(threshold, n_classes); }
    catch (...) { return nullptr; }
}
int myln_cascade_n_classes(void* cas) { return cctx(cas)->cas.n_classes(); }
void myln_cascade_free(void* cas) { delete cctx(cas); }

void myln_cascade_tune_difficulty(void* cas) {
    if (cctx(cas)->cas.n_classes() != 5) return;   // 難易度チューナーは5クラス専用
    myln::tune_cascade_difficulty(cctx(cas)->cas, cctx(cas)->cas.threshold());
}

int myln_cascade_tune_custom(void* cas, const char* path_or_json) {
    g_last_error.clear();
    try {
        if (!cas || !path_or_json) throw std::runtime_error("null argument");
        myln::tune_cascade_custom(cctx(cas)->cas, path_or_json);
        return 0;
    } catch (const std::exception& e) { g_last_error = e.what(); }
    catch (...) { g_last_error = "unknown error"; }
    return -1;
}

const float* myln_cascade_infer(void* cas, const float* features,
                                int n_in, int* out_n, int* out_used_relay) {
    auto* c = cctx(cas);
    myln::Vec input(features, features + n_in);
    auto res = c->cas.run(input);
    int n = (int)res.probs.size();
    c->out_buf.resize(n);
    std::copy(res.probs.begin(), res.probs.end(), c->out_buf.begin());
    if (out_n)          *out_n = n;
    if (out_used_relay) *out_used_relay = res.used_relay ? 1 : 0;
    return c->out_buf.data();
}

float myln_cascade_relay_rate(void* cas) {
    return cctx(cas)->cas.relay_rate();
}
