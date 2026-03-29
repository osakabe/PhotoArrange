# Python Windows 構築・運用ルール (Miniforge + uv)

このプロジェクトでは、Miniforge (conda) と `uv` を組み合わせた最適な Python 環境構築・運用を行います。

## 1. 環境の「作成」と「切り替え」: Miniforge (conda)
Python 自体のバージョン管理や、仮想環境の箱（隔離環境）を作る作業は Miniforge の専権事項とします。
- **作成**: `conda create -n <環境名> python=3.10`
- **有効化**: `conda activate <環境名>`
- **理由**: OS から独立したクリーンな実行環境を構築する能力が最も高いため。

## 2. 「OS・GPU レベルのシステムライブラリ」: Miniforge (conda)
C++ ライブラリや GPU 駆動用ドライバー類は Miniforge からインストールします。
- **対象**: `cudatoolkit`, `cudnn`, `ffmpeg`, `nodejs` など
- **コマンド**: `conda install conda-forge::<パッケージ名>`

## 3. 「Python パッケージ」: uv (uv pip)
アプリケーションライブラリは、原則すべて `uv` でインストールします。
- **対象**: `PySide6`, `insightface`, `scikit-learn`, `numpy`, `opencv-python` など
- **コマンド**: `uv pip install <パッケージ名>`

---

# Python Development Rules & Lessons Learned

## 4. Windows Path Normalization Rules (Strict)
Windows は大文字小文字を区別しない（Case-Insensitive）が、SQLite 等のデータベースや内部処理は大文字小文字を区別する場合があります。スラッシュ（`/`）とバックスラッシュ（`\`）の混在も、重複検知ミスの原因となります。

- **【最重要】厳格なルール**: 
  - データベースに保存（INSERT/UPDATE）する前、およびデータベースから取得したパスを比較・処理に使用する際は、**必ず** `os.path.normcase(os.path.abspath(path))` を通して正規化してください。
  - このルールは、`core/database.py` および `processor/duplicate_manager.py` 等の全てのモジュールに適用されます。
- **理由**: 同一ファイルが `C:\...` と `c:\...` 、あるいは `C:/...` と `C:\...` のように異なるエンティティとして重複登録される「幽霊データ」や検知漏れを防ぎ、AI解析の整合性を保つためです。

## 5. UX & Responsiveness (Signal-based)
- **ルール**: 重い処理は必ず `QThread` 等の別スレッドで実行し、UI更新は **Signal/Slot** 経由のみで行ってください。
- **注意**: Workerスレッドから直接 UI ウィジェットを操作することは厳禁です。

## 6. GPU & VRAM Management (Batch Processing)
- **ルール**: 解析 Worker は、**500枚（または500ビデオフレーム）処理するごと** に `torch.cuda.empty_cache()` を呼び出してください。
- **ルール**: Worker の停止（`stop`）時やキャンセル時にも、必ずキャッシュの解放を実行してください。

## 7. SQLite Concurrency & Grouping (NULL Handling)
- **ルール**: `PRAGMA journal_mode=WAL;` を使用し、読込をブロックさせないようにします。
- **【重要】**: `GROUP BY` や「重複検索」のクエリでは、必ず `image_hash IS NOT NULL AND image_hash != ''` を WHERE 句に含めてください。
- **理由**: SQLite では `NULL` がすべて同一グループとして扱われ、未解析データが巨大な一つの重複グループとして誤認されるためです。

## 8. Worker State Safety (Status Preservation)
- **ルール**: 解析の初期パス（メタデータ取得等）で、既存の有効な DB ステート（`image_hash` 等）を `None` で上書き（Wipe）しないでください。
- **理由**: 解析中断時に全ての解析済みデータが消失し、UI の表示が壊れるのを防ぐためです。

## 9. Cross-Module Integrity (Strict Contract)
- **ルール**: `DuplicateManager` 等のロジック層が `Database` 層のメソッドを呼び出す際は、必ずメソッドが実装されていることを確認してください。
- **詳細**: 実装漏れによる「サイレントな失敗（解析は終わるが保存されない）」を防ぐため、変更時は呼び出し側と受け手側の両方を同時に更新してください。

## 10. Specification Integrity
- **ルール**: `specification.md` は、ゼロからアプリを再構築できるレベルの「最新の設計図」として維持してください。
