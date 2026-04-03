---
trigger: always_on
---
# Python Windows 開発規約 (PhotoArrange 標準)

Windows (Miniforge + uv) 環境下での Python 開発における共通ルールを定義します。

## 1. 環境構築 & パッケージ管理
- **Conda (Miniforge)**: Python インタープリタおよび C++ 依存パッケージ (cudatoolkit, cudnn, ffmpeg) の管理に使用。
- **uv**: 高速な Python パッケージインストールに使用。
- **Rule**: conda install conda-forge::<package> でバイナリ依存を優先し、その後 uv pip install <package> で PySide6 や AI ライブラリを導入すること。

## 2. コーディング標準
- **Ruff**: コードのリンティングおよびフォーマットに必ず **ruff** を使用すること。提出前に uff check および uff format をパスさせること。
- **Type Hints**: 明示的な型ヒント (typing) を可能な限り使用すること。
- **PEP 8**: 最新の Python イディオムと PEP 8 に準拠すること。

## 3. UI スレッド分離 (QThread)
- **Rule**: UI スレッドは描画に専念させ、重い処理は QThread を継承した Worker クラスにオフロードすること。
- **Communication**: UI と Worker 間の通信は、必ず Signal と @Slot() デコレータを使用した安全な実装にすること。Worker から GUI 要素を直接操作してはならない。

## 4. Windows パス正規化 (Strict)
- **Problem**: Windows は大文字小文字を区別しないが、SQLite やハッシュ計算では区別される。
- **Rule**: DB 登録時や検索時は、必ず os.path.normcase(os.path.abspath(path)) を適用して絶対パスかつ小文字に統一すること。スラッシュとバックスラッシュの混在も、この処理で統一する。

## 5. GPU & VRAM 管理
- **Rule**: 推論完了後やバッチ処理ごとに 	orch.cuda.empty_cache() の実行を検討し、VRAM の断片化と枯渇を防ぐこと。
- **Isolation**: PyTorch と InsightFace の DLL (libiomp5md.dll) 競合を避けるため、起動時に環境変数を適切に設定すること。

## 6. SQLite 並列処理 (WAL Mode)
- **Rule**: DB 接続時は PRAGMA journal_mode=WAL; および PRAGMA busy_timeout=5000; を有効化し、並列書き込み時のロックを最小限に抑えること。
