"""①サーバー側アトミック保存 / ②クライアント側PDF検証 の検証テスト"""
import os, sys, types, shutil, tempfile, threading, time, socket, unittest
from unittest import mock
import pymupdf, requests

SRC = "/home/claude"
WORK = tempfile.mkdtemp(); os.chdir(WORK)
os.makedirs("server_storage", exist_ok=True)

# --- テスト環境に無いライブラリのスタブ (passlib / PySide6) ---
pl = types.ModuleType("passlib"); plc = types.ModuleType("passlib.context")
class CryptContext:
    def __init__(self, **k): pass
    def hash(self, p): return "h$" + p
    def verify(self, p, h): return h == "h$" + p
plc.CryptContext = CryptContext; sys.modules["passlib"] = pl; sys.modules["passlib.context"] = plc
ps = types.ModuleType("PySide6")
for sub in ("QtCore", "QtWidgets", "QtGui"):
    m = types.ModuleType("PySide6." + sub)
    for n in ("QTimer","Qt","QApplication","QMainWindow","QWidget","QLabel","QTableWidget",
              "QTableWidgetItem","QPixmap","QImage","QFont","QColor"):
        setattr(m, n, type(n, (), {}))
    sys.modules["PySide6." + sub] = m
sys.modules["PySide6"] = ps
sys.path.insert(0, SRC)
import server, signage_app
from fastapi.testclient import TestClient

def make_pdf(text="test", pages=1):
    d = pymupdf.open()
    for i in range(pages):
        d.new_page().insert_text((72, 72), f"{text} p{i+1}")
    b = d.tobytes(); d.close(); return b

GOOD = make_pdf("お知らせ", 2)
PID = 1
TOKEN = None

def login(c):
    r = c.post("/login", data={"username": "admin", "password": "admin123"}, follow_redirects=False)
    assert r.status_code == 303

class T1_Server(unittest.TestCase):
    def setUp(self):
        global TOKEN
        self.c = TestClient(server.app); login(self.c)
        TOKEN = server.fetch_project_info(PID)["signage_token"]
        self.folder = server.get_project_folders(PID)["a4_right"]
        for f in os.listdir(self.folder): os.remove(os.path.join(self.folder, f))

    def test_upload_saves_complete_pdf_and_no_tmp_left(self):
        r = self.c.post(f"/admin/upload/{PID}/a4_right", files={"file": ("notice.pdf", GOOD, "application/pdf")}, follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(os.listdir(self.folder), ["notice.pdf"])
        with open(os.path.join(self.folder, "notice.pdf"), "rb") as f: self.assertEqual(f.read(), GOOD)
        d = self.c.get(f"/api/signage-data/{PID}?token={TOKEN}").json()
        self.assertEqual(d["docs_a4_right"], ["notice.pdf"])
        self.assertEqual(self.c.get(f"/files/{PID}/a4_right/notice.pdf").content, GOOD)

    def test_file_invisible_while_writing(self):
        """書き込み中（os.replace直前）にAPI一覧・配信URLから見えないこと"""
        seen = {}
        real_replace = os.replace
        def spy(src, dst):
            seen["tmp_name"] = os.path.basename(src)
            seen["listed"] = server.get_stored_files(PID)["docs_a4_right"]
            seen["final_exists"] = os.path.exists(dst)
            real_replace(src, dst)
        with mock.patch.object(server.os, "replace", side_effect=spy):
            self.c.post(f"/admin/upload/{PID}/a4_right", files={"file": ("new.pdf", GOOD, "application/pdf")}, follow_redirects=False)
        self.assertTrue(seen["tmp_name"].endswith(".uploading"))
        self.assertEqual(seen["listed"], [])          # 一覧に出ない
        self.assertFalse(seen["final_exists"])        # /files/... からも取得できない
        self.assertEqual(server.get_stored_files(PID)["docs_a4_right"], ["new.pdf"])

    def test_overwrite_keeps_old_file_until_complete(self):
        old = make_pdf("旧版")
        with open(os.path.join(self.folder, "same.pdf"), "wb") as f: f.write(old)
        seen = {}
        real_replace = os.replace
        def spy(src, dst):
            with open(dst, "rb") as f: seen["during"] = f.read()
            real_replace(src, dst)
        with mock.patch.object(server.os, "replace", side_effect=spy):
            self.c.post(f"/admin/upload/{PID}/a4_right", files={"file": ("same.pdf", GOOD, "application/pdf")}, follow_redirects=False)
        self.assertEqual(seen["during"], old)         # 書き込み中も旧版は完全なまま
        with open(os.path.join(self.folder, "same.pdf"), "rb") as f: self.assertEqual(f.read(), GOOD)

    def test_tmp_removed_on_write_failure(self):
        with mock.patch.object(server.os, "replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                self.c.post(f"/admin/upload/{PID}/a4_right", files={"file": ("x.pdf", GOOD, "application/pdf")}, follow_redirects=False)
        self.assertEqual(os.listdir(self.folder), [])   # ゴミの一時ファイルが残らない

    def test_non_pdf_rejected(self):
        self.c.post(f"/admin/upload/{PID}/a4_right", files={"file": ("a.txt", b"hello", "text/plain")}, follow_redirects=False)
        self.assertEqual(os.listdir(self.folder), [])

class FakeRes:
    def __init__(self, content, status=200, cl="auto"):
        self.content, self.status_code = content, status
        self.headers = {} if cl is None else {"Content-Length": str(len(content) if cl == "auto" else cl)}

class T2_Client(unittest.TestCase):
    def setUp(self):
        self.w = object.__new__(signage_app.SignageWindow)
        self.w.project_id, self.w.server_url, self.w.server_data = 99, "dummy:8000", {"x": 1}
        self.dir = os.path.join("signage_storage", "99", "a4_right")
        shutil.rmtree(os.path.join("signage_storage", "99"), ignore_errors=True)
        os.makedirs(self.dir)

    def dl(self, res):
        with mock.patch.object(signage_app.requests, "get", return_value=res):
            self.w.download_missing_files("a4_right", ["n.pdf"])
        return os.listdir(self.dir)

    def test_valid_pdf_saved(self):
        self.assertEqual(self.dl(FakeRes(GOOD)), ["n.pdf"])
        with open(os.path.join(self.dir, "n.pdf"), "rb") as f: self.assertEqual(f.read(), GOOD)

    def test_truncated_pdf_rejected(self):
        self.assertEqual(self.dl(FakeRes(GOOD[: len(GOOD) // 2])), [])

    def test_empty_rejected(self):
        self.assertEqual(self.dl(FakeRes(b"")), [])

    def test_html_error_page_rejected(self):
        self.assertEqual(self.dl(FakeRes(b"<html>502 Bad Gateway</html>")), [])

    def test_content_length_mismatch_rejected(self):
        self.assertEqual(self.dl(FakeRes(GOOD, cl=len(GOOD) + 100)), [])

    def test_fake_header_only_rejected(self):
        self.assertEqual(self.dl(FakeRes(b"%PDF-1.7\n garbage \n%%EOF")), [])

    def test_404_rejected(self):
        self.assertEqual(self.dl(FakeRes(GOOD, status=404)), [])

    def test_network_error_no_file(self):
        with mock.patch.object(signage_app.requests, "get", side_effect=requests.exceptions.ConnectionError()):
            self.w.download_missing_files("a4_right", ["n.pdf"])
        self.assertEqual(os.listdir(self.dir), [])

    def test_recovers_on_next_sync(self):
        """1回目に途中までのPDFを受信 → 保存されず → 次の同期で完全版を取得"""
        self.assertEqual(self.dl(FakeRes(GOOD[:300])), [])
        self.assertEqual(self.dl(FakeRes(GOOD)), ["n.pdf"])
        d = pymupdf.open(os.path.join(self.dir, "n.pdf")); self.assertEqual(d.page_count, 2); d.close()

    def test_old_code_would_cache_broken_file(self):
        """参考：修正前ロジックでは壊れたPDFが保存され、PyMuPDFで開けない（不具合の再現）"""
        p = os.path.join(self.dir, "n.pdf")
        res = FakeRes(GOOD[:300])
        if res.status_code == 200:
            with open(p, "wb") as f: f.write(res.content)   # 旧コードと同じ処理
        with self.assertRaises(Exception):
            pymupdf.open(p).load_page(0).get_pixmap()

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

class T3_EndToEnd(unittest.TestCase):
    """実サーバー(uvicorn)を起動し、管理画面アップロード → サイネージ側ダウンロード・描画まで通しで確認"""
    @classmethod
    def setUpClass(cls):
        import uvicorn
        cls.port = free_port()
        cls.srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=cls.port, log_level="error"))
        threading.Thread(target=cls.srv.run, daemon=True).start()
        for _ in range(50):
            if cls.srv.started: break
            time.sleep(0.1)
    @classmethod
    def tearDownClass(cls): cls.srv.should_exit = True

    def test_upload_then_signage_download(self):
        base = f"http://127.0.0.1:{self.port}"
        s = requests.Session()
        s.post(base + "/login", data={"username": "admin", "password": "admin123"}, allow_redirects=False)
        folder = server.get_project_folders(PID)["a4_right"]
        for f in os.listdir(folder): os.remove(os.path.join(folder, f))
        big = make_pdf("右A4", 30)
        r = s.post(f"{base}/admin/upload/{PID}/a4_right", files={"file": ("right.pdf", big, "application/pdf")}, allow_redirects=False)
        self.assertEqual(r.status_code, 303)

        w = object.__new__(signage_app.SignageWindow)
        w.project_id, w.server_url = PID, f"127.0.0.1:{self.port}"
        shutil.rmtree(os.path.join("signage_storage", str(PID)), ignore_errors=True)
        os.makedirs(os.path.join("signage_storage", str(PID), "a4_right"))
        w.server_data = requests.get(f"{base}/api/signage-data/{PID}?token={TOKEN}").json()
        w.download_missing_files("a4_right", w.server_data["docs_a4_right"])
        local = os.path.join("signage_storage", str(PID), "a4_right", "right.pdf")
        with open(local, "rb") as f: self.assertEqual(f.read(), big)
        d = pymupdf.open(local); pix = d.load_page(0).get_pixmap(matrix=pymupdf.Matrix(2, 2))
        self.assertGreater(pix.width, 0); d.close()     # render_pdf_page と同じ描画処理が成功

if __name__ == "__main__":
    unittest.main(verbosity=2)
