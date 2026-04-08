# PhotoArrange Project Context & Guidelines

このファイルは、プロジェクト内の全AIエージェント（Gemini CLIを含む）が遵守すべき共通の開発ルールと、各専門エージェントの役割・責任範囲を統合したものです。指示を受けたエージェントは、これらのルールを厳格に守って実装および調査を行ってください。

## 1. Python Windows 構築・運用ルール (Miniforge + uv)

### 環境構築とパッケージ管理
- **作成と切り替え**: Miniforge (conda) を使用し、仮想環境は `conda create -n photo_env python=3.10` で作成します。実行時は必ず `conda activate photo_env` を行ってください。
  - **環境パス**: `C:\Users\osaka\miniforge3\envs\photo_env` (Windows標準)
- **OS・GPUライブラリ**: C++ ライブラリや GPU 駆動用ドライバー類 (`cudatoolkit`, `cudnn`, `ffmpeg` 等) は `conda install conda-forge::<パッケージ名>` でインストールします。
- **Pythonパッケージ**: アプリケーションライブラリ (`PySide6`, `insightface` 等) は原則すべて `uv pip install <パッケージ名>` でインストールします。

### コーディング標準と設計ルール
- **Ruff (Lint/Format)**: コードの品質管理とフォーマットは Ruff に完全に委ねます。Ruff のチェックを通らないコードは許容されません。
- **Type Hints**: 関数・メソッドの引数と戻り値には、必ず型アノテーションを記述してください。
- **Windows Path Normalization (Strict)**: データベースに保存する前、および取得したパスを比較する際は、必ず `os.path.normcase(os.path.abspath(path))` をを通して正規化してください。
- **UX & Responsiveness**: 重い処理は別スレッド（QThread）で実行し、UI更新は Signal/Slot 経由で行います。Workerからの直接のUI操作は厳禁です。
- **GPU & VRAM Management**: 解析 Worker は500枚（または500ビデオフレーム）の処理ごと、および Worker の停止・キャンセル時に `torch.cuda.empty_cache()` を呼び出してください。
- **SQLite Concurrency**: `WAL` モードを使用し、`GROUP BY` や重複検索クエリでは必ず `file_hash IS NOT NULL AND file_hash != ''` を条件に含めてください。
- **Worker State Safety**: 解析初期に既存の有効な DB ステート（`file_hash` 等）を `None` で上書きしないでください。
- **Refactoring & Modernization**: 
  - **認知負荷と複雑度の制限**: 循環的複雑度は原則「10以下」に抑え、Early Return を活用してネストを平坦化してください。マジックナンバーを排除し、意味のある定数名を定義してください。
  - **モダンなデータ構造**: 生の `dict` や `tuple` ではなく、`@dataclass` や `Pydantic` を活用して型安全性を確保してください。
  - **エンジニアリング規約**: ボーイスカウトルール（触ったコードは少し綺麗にする）を適用し、機能追加とリファクタリングのコミットは必ず分離してください。テストなきリファクタリングは厳禁です。
- **Performance Profiling & Logging**: 
  - 重い処理には `time.perf_counter()` による計測を実装し、`PROFILER: <処理名> took <時間>s` の形式でログ出力してください。
  - **Latency Standard**: ユーザーのクリック（操作）から、バックグラウンド処理、およびUIへの最終的な反映（表示）までの合計時間は、いかなる標準的な操作においても**最大3秒以内**としてください。これを越える場合は、処理の分割、非同期化、またはボトルネックの解消を必須とします。AI推論などで合計時間を短縮するように取り組んでも3秒を超えるようであれば、ユーザーに相談し、許容範囲やUI表現（進捗表示等）を決定してください。
  - **Automated Performance Monitoring**: 主要な GUI 操作については `pytest-qt` を用いた自動テストを実装し、この 3 秒ルールが遵守されているかを定量的に継続監視してください。
- **Cross-Module Integrity**: 
  - `DuplicateManager` と `Database` 層など、モジュール間の呼び出し契約（契約による設計）を厳守してください。変更時は呼び出し側と受け手側の両方を同時に更新し、実装漏れを防いでください。
- **事実に基づくアプローチ**: 不具合の修正は推測ではなく事実に基づきます。既存ログで原因不明な場合は、憶測で修正せず、まず詳細なログ（logger.info/error等）を追加して真因を特定してください。
- **Prevention & Quality (Lessons Learned)**:
  - **インポート安全**: `list_dir` や `grep` でファイルの実在と定義場所を必ず確認し、想像でのインポートを排除してください。循環参照や、同一ファイル内での重複定義に注意してください。
  - **型ヒントの完全性**: `typing` モジュール（`Optional`, `Any`, `Iterable` 等）からのインポートを欠かさず、静的解析の指摘を無視しないでください。
  - **ライブラリ API の正確性**: 記憶に頼らず、公式ドキュメントや検索ツールで最新のメソッド名（例: PySide6 の `.itemDelegate()`）を確認してください。
  - **プロキシパターンの推奨**: 複雑なウィジェット（`ThumbnailGrid` 等）の内部実装に直接アクセスさせず、操作用メソッドを介したカプセル化を徹底してください。
  - **提出前の「三段構え」チェック**: 1. `ruff check` による静的解析（NameError排除）、2. 物理的なインポートパスの整合性確認、3. メインエントリポイントの起動確認、の3段階で品質を保証してください。
- **Documentation & Communication Standards (4C Rule)**:
  - `docs/design` 内のファイル、実施計画（Implementation Plan）、および各種設計文書については、プロジェクトの背景や業務に精通していない第三者が読んでも理解できるよう、背景・趣旨を含めて具体的かつ詳細に記載してください。
  - **Code Documentation**: ソースコード内のコメントや docstring についても同様に、単なる処理の記述にとどまらず、その実装の意図や背景を具体的に記載してください。
  - **4Cの順守**: 外部ドキュメントおよびコード内説明文の双方において、以下の 4 つの基準を常に満たしてください。
    1. **Correct (正確)**: 事実に基づき、誤解のない正確な情報を記載する。
    2. **Concise (簡潔)**: 冗長さを排し、要点を効率的に伝える。
    3. **Clear (明快)**: 曖昧さを排除し、一読して意図が伝わる表現を用いる。
    4. **Consistent (一貫性)**: プロジェクト全体で用語の定義を統一し、文書間やコードとの間で齟齬がないようにする。

---

## 2. Agent Roles & Mandates

各専門分野の要件と責任範囲は以下の通りです。

### Role: UI Agent (GUI & イベントハンドリング担当)
- **Mandate**: 
  - `main.py` および `ui/` ディレクトリ内の GUI ウィジェット、スタイリング、レイアウトの実装。
  - ユーザー入力を各 Worker スレッドへ伝達するシグナル/スロットの設計。
- **Standard: Non-Blocking UI**:
  - UI スレッドをブロックする重い処理（ファイル I/O、AI 推論、sleep 等）は絶対に記述しないこと。
  - Worker スレッドからの信号は Slot 関数で受け取り、GUI への反映を行う。
- **Standard: Safety & DLL**:
  - Windows 環境下での DLL 読み込みエラーを回避するため、起動時に `core/utils.py` 等でパスの解決を行う仕組みを Logic Agent と協力して確保すること。
- **Standard: UX**:
  - 進行状況（ProgressBar）の表示や、バックグラウンド処理中のコントロール無効化など、適切なユーザーフィードバックを行うこと。

### Role: DB Agent (SQLite & データ永続化担当)
- **Mandate**: 
  - `core/database.py` の実装と保守。
  - `processor/duplicate_manager.py` 等における重複検知のためのデータアクセス層の実装。
  - SQLite の WAL モード活用と、DB ロックの回避。
- **Standard: Path Normalization (Strict)**:
  - Windows 環境における大文字・小文字の混在を解消するため、DB 登録・検索時のファイルパスは必ず `os.path.normcase(os.path.abspath(path))` を適用すること。
- **Standard: Concurrency**:
  - `database is locked` エラーを防ぐため、複数スレッドからのアクセス時は適切なコネクション管理（Thread-local connection）や `busy_timeout` 設定を徹底すること。
- **Standard: Efficiency**:
  - 大量データのバッチインサート時はトランザクションをまとめ、DB 負荷と I/O 頻度を最小限に抑えること。

### Role: AI/CV Agent (AI 推論 & 画像認識担当)
- **Mandate**: 
  - `processor/face_processor.py`, `feature_extractor.py`, `image_processor.py` 等の実装と保守。
  - InsightFace、PyTorch、OpenCV を用いた AI パイプラインの構築。
- **Standard: GPU Resource Management**:
  - Windows 環境下での CUDA メモリ枯渇 (OOM) を防ぐため、バッチ処理完了ごとに `torch.cuda.empty_cache()` の実行を検討すること。
  - プロセス終了時や推論完了後の適切なメモリ解放 (VRAM クリア) を徹底すること。
- **Standard: Precision & Speed**:
  - 特徴抽出 (Embedding) の精度と、大量画像の推論スピードのバランスを最適化すること。
  - 推論パイプラインにおいて UI スレッドをブロックしないよう、Logic Agent と連携して非同期実行を行うこと。

### Role: Logic Agent (ビジネスロジック & システム統合担当)
- **Mandate**: 
  - `core/sync_engine.py` や `processor/` 内の非AI系ロジック（ファイル移動、ハッシュ計算、メタデータ抽出）の実装と保守。
  - 各専門エージェント（DB, AI）の機能を組み合わせた高レベルな業務フロー（同期、重複検知フロー等）の構築。
- **Standard: QThread Worker Pattern**:
  - UIスレッドをブロックする可能性のある処理は、必ず QThread を継承した Worker クラス内に実装すること。
  - 処理状況や結果は Signal を通じて UI Agent に通知し、Worker 内から直接 GUI 要素を操作しないこと。
- **Standard: Concurrency & DB Strategy**:
  - DB 操作が必要な場合は、DB Agent が定義したスレッドセーフなアクセスパターンに従うこと。
  - 大量ファイルの処理時は、UI のレスポンスを維持するため、チャンク分割して処理すること。
- **Standard: Error Handling**:
  - OSレベルのファイルアクセス拒否やネットワーク中断を想定し、堅牢なリトライメカニズムとログ記録を実装すること。

### Role: QA Sheriff (品質管理 & 仕様整合性担当)
- **Mandate**: 
  - `specification.md` および `GEMINI.md` 内の全ルールの監視。
  - 各エージェント（UI, DB, AI, Logic）間のインターフェースと設計の一貫性チェック。
- **Standard: Architectural Integrity**:
  - UI 直接操作の禁止、DB ロック回避、VRAM 管理など、プロジェクト固有の制約が守られているかを常に監査する。
  - 不要なコード (Dead Code) や、重複したロジックの排除を提案する。
- **Standard: Test & Validation**:
  - `pytest` および `pytest-qt` を用いた自動テストを必須とします。特に GUI の主要なイベント（ボタンクリック、リスト選択、非同期ロード完了）については、モックデータを活用してその整合性とパフォーマンスを常に検証してください。その際、単なる描画完了だけでなく、**「表示内容の正確性（正しいラベル、件数、画像データがUIに反映されているか）」**を確認することを必須とします。
  - AI 処理における精度劣化 (Regression) が発生していないか、AI/CV Agent と協力して検証する。
- **Standard: E2E Consistency**:
  - GUI 上の操作（UI Agent）から、バックグラウンド処理（Logic Agent）、AI 推論（AI/CV Agent）、DB 保存（DB Agent）まで、一貫したデータフローが維持されているかを確認する。