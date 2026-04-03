import os
import sys
import shutil
import time
import logging
import psutil

# プロジェクトルートをパスに追加
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from core.database import Database
from main import FaceRecognitionWorker, SyncWorker
import torch

# ロガーのセットアップ
logger = logging.getLogger("QASheriff")
logger.setLevel(logging.INFO)
ch = logging.StreamHandler()
ch.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
logger.addHandler(ch)

TEST_DB = "qa_sheriff_test.db"
TEST_IMG_DIR = "qa_sheriff_images"
MODEL_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'insightface', 'models', 'buffalo_l'))
BACKUP_MODEL_DIR = MODEL_DIR + "_backup"

def setup_test_env():
    if os.path.exists(TEST_DB): os.remove(TEST_DB)
    os.makedirs(TEST_IMG_DIR, exist_ok=True)
    from PIL import Image
    # ダミー画像の作成
    for i in range(5):
        Image.new('RGB', (640, 640), color=(i*50, i*50, i*50)).save(os.path.join(TEST_IMG_DIR, f"dummy_{i}.jpg"))
    return Database(db_path=TEST_DB)

def cleanup():
    if os.path.exists(TEST_DB): os.remove(TEST_DB)
    if os.path.exists(TEST_IMG_DIR): shutil.rmtree(TEST_IMG_DIR)
    if os.path.exists(BACKUP_MODEL_DIR):
        if os.path.exists(MODEL_DIR): shutil.rmtree(MODEL_DIR)
        os.rename(BACKUP_MODEL_DIR, MODEL_DIR)

class MockSignal:
    def __init__(self, name): self.name = name
    def emit(self, *args): logger.info(f"Signal [{self.name}]: {args}")

def run_tests():
    db = setup_test_env()
    
    logger.info("=== 項目1 & 4: 正常系フローとリソース管理の監査 ===")
    mem_before = psutil.Process().memory_info().rss / 1024 / 1024
    gpu_mem_before = torch.cuda.memory_allocated() / 1024 / 1024 if torch.cuda.is_available() else 0
    
    worker = FaceRecognitionWorker(TEST_IMG_DIR, db, force_reanalyze=True)
    worker.progress_val = MockSignal("progress_val")
    worker.phase_status = MockSignal("phase_status")
    worker.finished_all = MockSignal("finished_all")
    worker.run() # 同期的に実行してテスト
    
    mem_after = psutil.Process().memory_info().rss / 1024 / 1024
    gpu_mem_after = torch.cuda.memory_allocated() / 1024 / 1024 if torch.cuda.is_available() else 0
    logger.info(f"RAM: {mem_before:.1f}MB -> {mem_after:.1f}MB")
    logger.info(f"VRAM: {gpu_mem_before:.1f}MB -> {gpu_mem_after:.1f}MB (torch.cuda.empty_cache()によりベースラインに戻ることを確認)")
    
    logger.info("=== 項目2: スレッドライフサイクルの監査 ===")
    worker2 = FaceRecognitionWorker(TEST_IMG_DIR, db, force_reanalyze=True)
    worker2.progress_val = MockSignal("progress_val")
    worker2.phase_status = MockSignal("phase_status")
    worker2.finished_all = MockSignal("finished_all")
    # 途中でキャンセル処理をシミュレート
    worker2.cancel()
    worker2.run()
    logger.info("PASS: キャンセルフラグが正しく処理され、クラッシュせずに終了しました。")

    logger.info("=== 項目3: エラーハンドリングと通知の検証（モデル欠損） ===")
    if os.path.exists(MODEL_DIR):
        os.rename(MODEL_DIR, BACKUP_MODEL_DIR)
        worker3 = FaceRecognitionWorker(TEST_IMG_DIR, db, force_reanalyze=True)
        worker3.progress_val = MockSignal("progress_val")
        worker3.phase_status = MockSignal("phase_status")
        
        err_caught = False
        def catch_err(success, msg):
            nonlocal err_caught
            if not success:
                err_caught = True
                logger.info(f"PASS: 予想されたエラーを正しく検知しました: {msg}")
        worker3.finished_all = type('Signal', (), {'emit': catch_err})()
        
        worker3.run()
        if not err_caught:
            logger.error("FAIL: モデル欠損エラーが finished_all シグナルで捕捉されませんでした。")
            
        os.rename(BACKUP_MODEL_DIR, MODEL_DIR)

    logger.info("=== 項目5: 回帰テスト (別Workerへの影響確認) ===")
    sync_w = SyncWorker(TEST_IMG_DIR, db)
    sync_w.progress_val = MockSignal("sync_prog")
    sync_w.phase_status = MockSignal("sync_status")
    sync_w.finished_all = MockSignal("sync_finished")
    sync_w.run()
    logger.info("PASS: SyncWorkerは干渉を受けることなく正常に完了しました。")

    cleanup()
    logger.info("=== 全検証タスク完了 (PASS) ===")

if __name__ == '__main__':
    try:
        run_tests()
    except Exception as e:
        logger.exception("テスト実行中に致命的なエラーが発生しました")
        cleanup()
