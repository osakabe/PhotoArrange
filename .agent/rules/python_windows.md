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

## 3. UX & Responsiveness Standards
ユーザーにストレスを与えない、快適なデスクトップ体験を提供するために以下の事項を遵守してください。

### 1. 重い処理を「別スレッド」で実行する（根本解決）
時間のかかる処理（ファイルI/O、データベース検索、AI推論など）は、必ずメインスレッドから切り離してバックグラウンド処理（`QThread`等）として実行してください。
- **メリット**: 処理中もGUIがフリーズせず、ウィンドウの移動や他の操作が可能になります。

### 2. クリック直後に「処理中」のフィードバックを返す（即時対応）
ユーザーがアクションを起こした直後に、アプリケーションが指示を受け付けたことを視覚的に伝えてください。
- **ボタンを無効化する (Disable)**: 二重処理を防ぎ、受付済みであることを示します。
- **マウスカーソルを変更する**: `Qt.WaitCursor`（砂時計等）に変更します。
- **ステータスバーの更新**: ウィンドウ下部に現在の状況を表示します。

### 3. 処理の進捗を可視化する（高度な対応）
進捗が計算可能な場合は、ユーザーの不安を解消するためにインジケータを表示してください。
- **プログレスバー**: 完了率がわかる場合に順次進めます。
- **不確定プログレスバー (Indeterminate)**: 完了時間が不明な場合でも、アニメーションを動かすことで「動作中」であることを伝えます。

---

## 4. Windows Path Normalization Rules (Strict)
Windows は大文字小文字を区別しない（Case-Insensitive）が、SQLite 等のデータベースや内部処理は大文字小文字を区別する場合があります。スラッシュ（`/`）とバックスラッシュ（`\`）の混在も、重複検知ミスの原因となります。

- **【最重要】厳格なルール**: 
  - データベースに保存（INSERT/UPDATE）する前、およびデータベースから取得したパスを比較・処理に使用する際は、**必ず** `os.path.normcase(os.path.abspath(path))` を通して正規化してください。
  - このルールは、`core/database.py` および `processor/duplicate_manager.py` 等の全てのモジュールに適用されます。
- **理由**: 同一ファイルが `C:\...` と `c:\...` 、あるいは `C:/...` と `C:\...` のように異なるエンティティとして重複登録される「幽霊データ」や検知漏れを防ぎ、AI解析の整合性を保つためです。

## 5. Architectural Consistency & Specification Standards
- **E2Eの一貫性 (Ripple Effect)**: コア機能（AIアルゴリズムやデータ構造等）を変更・廃止する際は、UI（表示・フィルタ）、ロジック（Worker）、データベース（SQLクエリ）の3層すべてに渡って波及効果を確認し、実装を完全に同期させてください。この整合性は **QA Sheriff** が最終監査を行います。
- **Dead Code Elimination (不要コードの徹底削除)**: 廃止された機能（古い推論ロジックや使われなくなったBLOBカラム等）は、サイレント・バグやパフォーマンス低下を防ぐため、関連するUI選択肢やSQLのSELECT句も含めてコードベースから完全に削除してください。
- **意味的命名とドメインの整合 (Semantic Naming)**: DBのマイグレーションコストを避ける等の理由で既存カラムを別の役割に流用する場合（例: `image_hash` をグループIDとして使用）、SQLの `AS` 句を用いたリネームや明示的なコメント付与により、「コード上の意味の再定義」を行い、開発者の誤用を防いでください。
- **再構築可能性と同期**: `specification.md` は、ゼロからアプリケーションを再構築できるレベルの設計図として維持し、データベーススキーマやAIの動作パラメータ等の実装状況と完全に同期させてください。

## 6. Windows DLL & Environment Isolation
- **ルール**: プログラムの開始直後（`main.py` の最上部）で、必ず `core.utils.fix_dll_search_path()` を呼び出し、`os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"` を設定してください。
- **理由**: InsightFace や PyTorch が依存する DLL (libiomp5md.dll 等) の重複ロードによるクラッシュを防止するためです。

## 7. GPU & VRAM Management
- **ルール**: 大量のリソース（SSIM、顔認識等）をバッチ処理する際は、一定数（500枚等）ごとに `torch.cuda.empty_cache()` を呼び出してください。また、Workerの停止（stop）時にも必ず実行してください。
- **理由**: 長時間の稼働に伴う VRAM 枯渇やメモリ断片化を防ぎ、システム全体の安定性を維持するためです。

## 8. SQLite Concurrency (WAL Mode)
- **ルール**: データベース接続時には必ず `PRAGMA journal_mode=WAL;` および `PRAGMA busy_timeout=5000;` を実行してください。
- **理由**: 分析Workerによる大量書き込み中も、UIスレッドによる読込（ギャラリー表示等）をブロックさせないためです。

## 9. Absolute Path Integrity
- **ルール**: データベースに保存・比較するパスは、常に絶対パス化（`os.path.abspath`）を徹底してください。
- **理由**: 相対パスの混在によるレコードの重複や、ファイル移動後の追跡失敗を防ぐためです。

## 10. Agent Roles & File Ownership
プロジェクトの整合性を維持するため、以下の4つのエージェント役割と担当範囲を定義します。

### 1. AI Architect (AIパイプライン・アーキテクト)
- **責務**: AIモデル、GPU推論、高性能画像処理の実装。
- **担当範囲**: `processor/face_processor.py`, `processor/feature_extractor.py`, `processor/image_processor.py`, `insightface/`, `tmp_test_*.py` (AI/GPU検証用)

### 2. Data Librarian (データ整合性・同期スペシャリスト)
- **責務**: DBスキーマ設計、パス正規化、重複グループ統合ロジックの管理。
- **担当範囲**: `core/database.py`, `processor/duplicate_manager.py`, `processor/geo_processor.py`, `photo_app.db`, `tmp/` (DB検証スクリプト)

### 3. App Conductor (UI・ワーカー・オーケストレーター)
- **責務**: UIの実装、非同期Worker管理、アプリ全体のライフサイクル制御。
- **担当範囲**: `main.py`, `ui/`, `core/utils.py`, `run_photo_arrange.bat`, `requirements.txt`

### 4. QA Sheriff (品質・仕様守護者)
- **責務**: システム全体の整合性、仕様書・ルールの保守、E2Eの一貫性監査。
- **担当範囲**: `specification.md`, `README.md`, `.agent/rules/`, `.gitignore`, `python-debugger/`

