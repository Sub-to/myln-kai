# ⚡ MYLN-FRAME Kai

> **Kai** is a fresh start forked from MYLN-FRAME (2026-11-12). The security and earthquake parts were taken out;
> this repo keeps only the core frame, the cascade, the difficulty tuner and the JSON tuning API.

> A lightweight AI inference framework — no GPU, no cloud, no waiting.

Named after the **myelin sheath**, the biological insulator that makes neural signals fast and efficient.  
Built for the real world: Raspberry Pi, old laptops, embedded boards, anywhere AI _should_ run.

---

## The idea in one picture

```
         INPUT
           ↓
       [ROUTER]          ← routes signal into specialist slots
     ↙   ↓   ↓   ↘
   [A]  [B]  [C]  [D]   ← swap heads for any domain
     ↕    ↕    ↕    ↕   ← Ring Attention: heads share context
       [CENTER LINE]     ← aggregates → classifies
           ↓
         OUTPUT
```

Same frame. Different heads. Any device.

---

## Three sizes

| | MYLN-SS ⚡ | MYLN-T 🪶 | MYLN-S 💪 |
|---|---|---|---|
| dim | 16 | 64 | 128 |
| RAM | ~1 MB | ~20 MB | ~100 MB |
| Latency | < 0.1 ms | < 1 ms | < 5 ms |
| Best for | microcontrollers / SBC | old PC / edge | laptop / server |
| GPU needed | ✗ | ✗ | ✗ |

---

## Cascade: fast path + precise path

When input is _obvious_, skip the heavy frame entirely.

```
INPUT
  ↓
[RELAY]  ← SS, 2 heads, ~9 µs
  ↓
confidence ≥ 80%?
  ├─ YES → output immediately          ← obvious cases
  └─ NO  → [FULL]  T, 4 heads, ~109µs ← ambiguous cases
```

Real numbers on a Raspberry Pi 5:

| Case | Path | Latency |
|---|---|---|
| Obvious input | relay only | **9 µs** |
| Borderline input | relay → full | 118 µs |

---

## Quick start

### Build

```bash
git clone https://github.com/Sub-to/myln-frame.git
cd myln-frame
mkdir build && cd build
cmake .. && make myln
# → build/libmyln.dylib  (macOS)
# → build/libmyln.so     (Linux)
```

### Python

```python
from bridge.python.myln import MylnCascade

cas = MylnCascade(threshold=0.80).tune_difficulty()

label, conf, used_relay = cas.predict_with_path(
    [0.67, 0.14, 1.0, 1.0, 1.0]   # [tech, length, steps, scope, reasoning]
)
print(label, f"{conf:.0%}", "relay" if used_relay else "full")
# → EXTREME (see "Difficulty grading" below)
```

### C / Any language via C API

```c
void* frame = myln_new("T", 5);
myln_tune_difficulty(frame);

float features[5] = {0.67f, 0.14f, 1.0f, 1.0f, 1.0f};
int n_out = 0;
const float* probs = myln_infer(frame, features, 5, &n_out);

myln_free(frame);
```

The C API works with Python (ctypes), Node.js (ffi-napi), Ruby (Fiddle), Go (cgo), and Rust (bindgen).

---

## Tuners

A *tuner* sets the Router weights, Ring parameters and Center Line classifier of a frame by hand — **no training**.

| Tuner | Where | Input → Output | Use |
|---|---|---|---|
| `difficulty` | `tuner/difficulty_tuner.h` | 5 features → `CHAT`…`EXTREME` | grading how hard a request is (e.g. model routing) |
| `custom` | `include/myln/tune_config.h` | anything → anything | **any** setup from a JSON file — see below |

Each has a C++ function, a C API call and a Python method:

| | difficulty | custom (JSON) |
|---|---|---|
| C++ | `tune_difficulty(frame)` / `tune_cascade_difficulty(cas)` | `tune_custom(frame, path_or_json)` / `tune_cascade_custom(cas, ...)` |
| C | `myln_tune_difficulty` / `myln_cascade_tune_difficulty` | `myln_tune_custom` / `myln_cascade_tune_custom` |
| Python | `.tune_difficulty()` | `.tune_custom(config)` |

---

## Difficulty grading

Turns a request into a difficulty level. The frame does not read text — you extract five features
(0.0–1.0) however you like, the tuner turns them into a level.

| Index | Feature | Meaning |
|---|---|---|
| 0 | `tech` | technical density: code, file names, technical terms |
| 1 | `length` | length of the request |
| 2 | `steps` | number of steps ("first… then… finally") |
| 3 | `scope` | blast radius: many files, whole project, deliverables — **weighted highest** |
| 4 | `reasoning` | how much thinking it needs: root cause, comparison, design |

**Output classes:** `0 CHAT (雑談)` · `1 EASY (易)` · `2 MEDIUM (中)` · `3 HARD (難)` · `4 EXTREME (最難)`

It is an additive model: `score = 1.5·tech + 1.0·length + 1.5·steps + 2.5·scope + 1.0·reasoning`, cut at
0.5 / 1.5 / 2.8 / 4.2. (Each slot contributes its share ×4 and the Center Line averages the four slots uniformly,
so the frame computes exactly that sum.) Relay and full see the same score; the relay just answers faster when it is far from a boundary.

```python
from bridge.python.myln import MylnCascade, DIFFICULTY_CLASSES

cas = MylnCascade(threshold=0.80).tune_difficulty()
probs, used_relay = cas.infer([0.67, 0.14, 1.0, 1.0, 1.0])   # [tech, length, steps, scope, reasoning]
print(DIFFICULTY_CLASSES[probs.index(max(probs))])            # → EXTREME
```

`bridge/python/difficulty.py` is a complete example: it extracts the five features from Japanese/English
request text with a few regexes and prints `{"level", "conf", "relay", "features"}` as JSON
(`echo '{"text":"..."}' | python3 bridge/python/difficulty.py`).

---

## Opg — picking a cartridge (dry fire) and pulling the trigger

**Opg** (Opt + Generative) looks at a request and picks which *cartridge* (specialist) should answer it.
Think of a revolver: the **magazine** has 6 chambers, each holding a cartridge; Opg only turns the cylinder.
`opg.py` generates nothing (a dry fire), and it never answers with something that is not in the magazine.
`opg_fire.py` is the **trigger**: it sends the request to a local model with the chosen cartridge's instructions.

```bash
echo '{"text":"main.pyのバグを直して"}' | python3 bridge/python/opg.py
# → {"cartridge": "code", "slot": 2, "conf": 1.0, "runner_up": "general", "level": 1, "path": "relay",
#    "reason": "コード職人: 「バグ」「.py」 の語、「直して」 の言い回し"}
python3 bridge/python/opg.py --demo     # dry-fire every example sentence of the loaded cartridges
```

| Key | Meaning |
|---|---|
| `cartridge` / `slot` | the chosen cartridge id and its chamber (0–5) |
| `conf` / `runner_up` | confidence, and the next-best loaded cartridge |
| `level` | difficulty 0–4, straight from `difficulty.py` (a separate axis) |
| `path` | `relay` / `full` = fast path · `embed` = careful path · `fallback` = not sure, back to chamber 0 |
| `reason` | one line: which words decided it |

- **Fast path:** word cues from the name tags → 10 features (strong ×5, weak ×5) → a 6-output MYLN cascade
  (`configs/opg/cascade.json`, plain `tune_custom`). Chamber 0 (`general`) is a fixed baseline: a specialist
  is chosen only when its cues clearly beat it; ties and weak evidence fall back to `general`.
- **Magazine:** `configs/opg/magazine.json` lists the 6 chambers (`null` = empty). Swap it with `--magazine` or `OPG_MAGAZINE`.
- **Name tags:** `configs/opg/cartridges/<id>.json` — `keywords` / `patterns` (strong cues), `hints` / `hint_patterns`
  (weak cues), `examples`, plus `prompt` / `base_model` for the trigger (`lora` is reserved, not used yet).
  **Adding a cartridge = adding one name-tag file and putting its id in the magazine.** Opg itself is not rebuilt.
- **Careful path** (`bridge/python/opg_embed.py`): when the fast path cannot settle on a specialist, the request is
  compared with the `examples` of the loaded name tags using embeddings from a small **local** GGUF model
  (read with llama.cpp's `llama-embedding`; no network, nothing is downloaded, nothing is generated).
  Point it at a model with `configs/opg/local.json` (copy `local.example.json`) or `OPG_EMBED_MODEL`.
  No model → the careful path is skipped and Opg runs on the fast path alone. `--fast-only` / `OPG_CAREFUL=0` turns it off.
  Adding example sentences to a name tag is all it takes to improve it; they are embedded once and cached in `configs/opg/.cache/`.
- Unseen test sentences (`tests/opg_cases.json`, `holdout2`): fast path alone 16/40, with the careful path 31/40
  (Qwen2.5-0.5B embeddings, about 1 s per request that reaches it); ambiguous sentences stay on `general`.
- First six: `0 general` · `1 chat` · `2 code` · `3 research` · `4 doc` · `5 calc`.

- **Trigger** (`bridge/python/opg_fire.py`): picks a cartridge, puts that name tag's `prompt` in as the system
  message and posts to a **local** OpenAI-compatible server (`/v1/chat/completions`, e.g. llama.cpp's `llama-server`).
  Only `127.0.0.1` / `localhost` endpoints are accepted — anything else is refused and nothing is sent.
  It never starts a server itself; if none is running you get an `error` and exit code 2.
  Settings live in `configs/opg/local.json` under `"fire"` (`endpoint`, `model`, `max_tokens`, `timeout`, `extra`),
  or `OPG_FIRE_ENDPOINT` / `OPG_FIRE_MODEL`. A name tag's `base_model` overrides the model for that cartridge.
  `extra` is merged into the request (e.g. `{"reasoning_effort": "none"}`).

```bash
echo '{"text":"8人で1万2千円を分けると1人あたりは?"}' | python3 bridge/python/opg_fire.py
# → the 7 dry-fire keys + {"model": "...", "answer": "... 答え: 1,500円", "seconds": 19.1}
python3 bridge/python/opg_fire.py --dry   # show what would be sent; no model is run
echo '{"text":"...","cartridge":"code"}' | python3 bridge/python/opg_fire.py   # choose by hand (loaded cartridges only)
```

```bash
python3 tests/opg_regression.py   # the trigger is tested against a fake local server; no model is run
```

---

## Generic tuning API (JSON)

Anything the built-in tuners do can be expressed as a JSON file — Router weights (per-slot W, b),
Ring parameters and the Center Line classifier (W_cls). This is the portable "head config" format.

```python
from bridge.python.myln import MylnFrame, MylnCascade

frame = MylnFrame("T", 5).tune_custom("configs/difficulty.json")     # a file path…
frame = MylnFrame("T", 5).tune_custom({"in_dim": 5, "router": ...})  # …or a dict / JSON string
cas   = MylnCascade(0.80).tune_custom("configs/difficulty_cascade.json")
```

```c
void* frame = myln_new("T", 5);
if (myln_tune_custom(frame, "configs/difficulty.json") != 0)
    fprintf(stderr, "%s\n", myln_last_error());     // frame is left unchanged on error
```

- Invalid configs are rejected *before* anything is applied (and unknown keys are errors, so typos don't pass silently).
- No dependencies: the C++ side ships its own ~200-line JSON parser.
- `configs/` has ready-made `difficulty*.json`, and the tests check that they give
  **bit-identical** probabilities to the C++ tuners.
- Full schema: [`docs/tuning-config.md`](docs/tuning-config.md).

---

## Tests

```bash
python3 tests/regression.py      # uses build/libmyln.dylib (or MYLN_LIB=path/to/libmyln)
```

Checks the difficulty output against a saved baseline (`tests/baseline/`), C++ tuner ⇔ JSON config equality,
and that invalid configs are rejected without side effects.

---

## Architecture

```
include/myln/
  config.h        — frame size definitions (SS / T / S)
  frame.h         — Frame class: router + heads + ring + center
  router.h        — input → 4 slot vectors
  head.h          — Head interface (swappable)
  ring_attn.h     — Ring Attention: lateral sharing between heads
  center_line.h   — aggregation + classification (W_cls)
  cascade.h       — CascadeFrame: relay → confidence → full
  tune_config.h   — generic JSON tuning (tune_custom / tune_cascade_custom)
  mini_json.h     — dependency-free JSON parser used by tune_config.h
  math_ops.h      — Vec / Mat primitives

heads/
  passthrough_head.h   — identity (signal passes unchanged)
  zero_head.h          — silence (disabled slot)

tuner/
  difficulty_tuner.h   — request difficulty: CHAT / EASY / MEDIUM / HARD / EXTREME

bridge/
  myln_c_api.h/.cpp    — universal C API
  python/myln.py       — Python ctypes wrapper (MylnFrame, MylnCascade)
  python/difficulty.py — text → 5 features → difficulty level (JSON in/out)
  python/opg.py        — Opg: text → which cartridge (6 chambers), dry fire only
  python/opg_embed.py  — Opg careful path: nearest example sentences via a local embedding model
  python/opg_fire.py   — Opg trigger: chosen cartridge's prompt + request → local model → answer

configs/               — ready-made JSON tunings (difficulty; frame + cascade)
configs/opg/           — Opg magazine, cartridge name tags, 6-output cascade
docs/tuning-config.md  — JSON tuning schema
tests/                 — regression tests + saved baselines
```

---

## Swappable heads

Heads are the only part you change between domains.

```cpp
frame.set_head(0, std::make_unique<PassthroughHead>("a"));
frame.set_head(1, std::make_unique<ZeroHead>());           // inactive slot
frame.set_head(2, std::make_unique<PassthroughHead>("b"));
frame.set_head(3, std::make_unique<DefaultHead>());         // learned head
```

**Shipped in the core:** `PassthroughHead`, `ZeroHead`, `DefaultHead`.

**Extension hook:** separate packages can configure a frame through `myln_frame_native()` (C API) / `MylnFrame.native_ptr` (Python).

**Future heads (planned):**
- `VoiceHead` — speech feature classification
- `CustomHead` — a head defined from a config file (Router/Ring/Center Line are already configurable via JSON)

---

## Why this exists

Most AI requires expensive hardware, cloud connectivity, and someone else's infrastructure.  
MYLN-FRAME runs on the machine in front of you — the one that's already there.

*Small enough to carry. Fast enough to matter. Free enough to trust.*

Human–AI coexistence shouldn't depend on a data center.

---

## Status

- [x] Core frame — router / ring-attention / center-line
- [x] Frame sizes — SS / T / S
- [x] Swappable heads — Passthrough, Zero, Default
- [x] 2-stage cascade — relay + confidence threshold + full
- [x] Universal C API — Python, Node.js, Ruby, Go, Rust
- [x] Python bridge — `MylnFrame`, `MylnCascade`
- [x] Difficulty tuner — `tune_difficulty`
- [x] Opg — cartridge selection: fast path, careful path, dry fire (`bridge/python/opg.py`), trigger (`bridge/python/opg_fire.py`); LoRA swapping not yet
- [x] JSON tuning config — `myln_tune_custom` / `tune_custom` ([schema](docs/tuning-config.md))
- [ ] `.mhead` for *heads* — portable head definitions (weights of non-trivial heads)
- [ ] CLI tool — `myln run --frame SS --head difficulty.mhead`
- [ ] Distributed mode — heads over socket / gRPC

---

## License

MIT
