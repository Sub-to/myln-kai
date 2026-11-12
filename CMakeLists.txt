cmake_minimum_required(VERSION 3.14)
project(myln-frame VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()
set(CMAKE_CXX_FLAGS_RELEASE "-O3 -march=native")

include_directories(include)

# ── コアライブラリ（共有ライブラリ）──────────────────────────
# Python / Node.js / Ruby / Go / Rust など全言語から呼べる C API
add_library(myln SHARED bridge/myln_c_api.cpp)
target_include_directories(myln PUBLIC include)
set_target_properties(myln PROPERTIES
    C_VISIBILITY_PRESET   hidden
    CXX_VISIBILITY_PRESET hidden
)

# ── デモ実行ファイル ──────────────────────────────────────
add_executable(difficulty_demo examples/difficulty_demo.cpp)
