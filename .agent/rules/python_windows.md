---
trigger: always_on
---

# Python Windows 構築・運用ルール (Miniforge + uv)

このプロジェクトでは、Miniforge (conda) と `uv` を組み合わせた最適な Python 環境構築・運用を行います。

## 1. 環境の「作成」と「切り替え」: Miniforge (conda)
Python 自体のバージョン管理や、仮想環境の箱（隔離環境）を作る作業は Miniforge の専権事項とします。

- **作成**: `conda create -n <環境名> python=3.10`
- **有効化**: `conda activate <環境名>`
- **削除**: `conda env remove -n <環境名>`
- **理由**: OS から独立したクリーンな実行環境を構築する能力が最も高いため。

## 2. 「OS・GPU レベルのシステムライブラリ」: Miniforge (conda)
Python 単体のパッケージではなく、C++ で書かれたライブラリや、GPU 駆動用ドライバー類は Miniforge からインストールします。

- **対象**: `cudatoolkit`, `cudnn`, `ffmpeg`, `nodejs` など
- **コマンド**: `conda install conda-forge::<パッケージ名>`
- **理由**: PyPI (pip) よりも safe なバイナリ（完成品）を conda-forge から取得できるため。

## 3. 「Python パッケージ」: uv (uv pip)
アプリケーション開発に必要な Python ライブラリ（`import` で呼び出すもの）は、原則すべて `uv` でインストールします。

- **対象**: `PySide6`, `insightface`, `scikit-learn`, `numpy`, `pandas`, `opencv-python` など
- **コマンド**: `uv pip install <パッケージ名>`
- **理由**: conda に比べて圧倒的に高速（数分 → 数秒）で、PyPI の最新版を最速で取得できるため。

### 【例外】トラブル時のフォールバック
`uv pip install` で「C++ ビルドツールがありません」「コンパイルエラー」等の赤いエラーが出た場合のみ、例外として `conda install conda-forge::<パッケージ名>` を試してください。

---

# Python Development Rules

## 1. General Coding Standards
- Source code MUST be based on the latest stable versions, de facto standards, or best practices (State of the Practice / State of the Art).
- Adhere to PEP 8 and modern Python idioms.
- Use explicit type hints (typing) wherever possible.

## 2. Code Quality & Formatting
- You MUST use **ruff** for code linting and formatting.
- Ensure all code passes `ruff check` and is formatted with `ruff format` before submission.
- Maintain high code quality by addressing all reported issues.