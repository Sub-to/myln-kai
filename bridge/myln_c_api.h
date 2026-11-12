/*
 * MYLN-FRAME  C API  ── Universal Bridge
 * ========================================
 * C99互換のフラットAPIで、あらゆる言語から呼べる量産型ブリッジ。
 *
 * Python  → ctypes / cffi
 * Node.js → ffi-napi
 * Ruby    → Fiddle
 * Go      → cgo
 * Rust    → bindgen
 *
 * Usage (どの言語でも同じ流れ):
 *   frame = myln_new("T", 5)
 *   myln_tune_difficulty(frame)
 *   probs = myln_infer(frame, features, 5, &n)
 *   myln_free(frame)
 */

#ifndef MYLN_C_API_H
#define MYLN_C_API_H

#ifdef __cplusplus
extern "C" {
#endif

/* エクスポートマクロ */
#if defined(_WIN32)
#  define MYLN_API __declspec(dllexport)
#else
#  define MYLN_API __attribute__((visibility("default")))
#endif

/* ── フレームのライフサイクル ──────────────────────────────
 * size     : "SS" / "T" / "S"
 * n_classes: 出力クラス数
 * 戻り値   : フレームハンドル (NULL = 失敗)
 */
MYLN_API void* myln_new (const char* size, int n_classes);
MYLN_API void  myln_free(void* frame);

/* ── 汎用チューニング（JSON設定）──────────────────────────
 * path_or_json: 先頭が '{' なら JSON 文字列、それ以外はファイルパス
 * 戻り値      : 0=成功 / -1=失敗（理由は myln_last_error()）
 *               検証に失敗した場合、frame は一切変更されない
 * myln_last_error: 直近の失敗理由（スレッドごと。成功時は空文字列）
 */
MYLN_API int         myln_tune_custom(void* frame, const char* path_or_json);

/* ── 難易度判定チューニング（依頼文 → 難易度）────────────────
 * features: [tech, length, steps, scope, reasoning]  各 0.0〜1.0
 * 出力クラス: 0=CHAT(雑談) 1=EASY(易) 2=MEDIUM(中) 3=HARD(難) 4=EXTREME(最難)
 * (n_classes は 5 で作ること。in_dim は 5 固定)
 */
MYLN_API void myln_tune_difficulty(void* frame);
MYLN_API const char* myln_last_error (void);

/* ── 推論 ──────────────────────────────────────────────────
 * features : 入力特徴量配列 (float32)
 * n_in     : 入力次元数
 * out_n    : [out] 出力クラス数が書き込まれる
 * 戻り値   : クラス確率配列 (フレームが所有するバッファ)
 *            スレッドセーフではない。コピーが必要な場合は呼び出し側で行うこと。
 */
MYLN_API const float* myln_infer(void* frame, const float* features, int n_in, int* out_n);

/* ── 拡張パッケージ用フック ────────────────────────────────
 * myln_new() で作った frame の中身 (myln::Frame*) を返す。
 * 別パッケージ(例: ドメイン専用のヘッド/チューナー)が C++ で Frame を直接設定するための口。
 * 返したポインタは frame と同じ寿命。呼び出し側は同じバージョンの include/myln で
 * コンパイルされている必要がある(ABI互換はヘッダ共有が前提)。
 */
MYLN_API void* myln_frame_native(void* frame);

/* ── メタ情報 ──────────────────────────────────────────────*/
MYLN_API const char* myln_tag      (void* frame);
MYLN_API int         myln_dim      (void* frame);
MYLN_API int         myln_n_classes(void* frame);
MYLN_API const char* myln_version  (void);

/* ── カスケード（2段リレー）API ────────────────────────────
 * リレー（SS 2頭）で高速判定 → 曖昧なら フル（T 4頭）へ
 * threshold: 確信度の閾値 (0.0〜1.0, 推奨 0.80)
 */
MYLN_API void*       myln_cascade_new      (float threshold);
MYLN_API void        myln_cascade_free     (void* cas);
/* JSON設定: {"threshold":..., "relay":{...}, "full":{...}}  戻り値は myln_tune_custom と同じ */
MYLN_API int         myln_cascade_tune_custom(void* cas, const char* path_or_json);
/* 難易度判定(リレー: tech+scope / フル: 4スロット)。threshold は myln_cascade_new の値を使う */
MYLN_API void        myln_cascade_tune_difficulty(void* cas);
MYLN_API const float* myln_cascade_infer   (void* cas, const float* features,
                                            int n_in, int* out_n,
                                            int* out_used_relay);
MYLN_API float       myln_cascade_relay_rate(void* cas); // リレー通過率

#ifdef __cplusplus
}
#endif
#endif /* MYLN_C_API_H */
