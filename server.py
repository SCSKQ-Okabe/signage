import os
import sqlite3
import secrets
from fastapi import FastAPI, Form, UploadFile, File, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from datetime import datetime
from passlib.context import CryptContext
from typing import Optional
from fastapi import Request
import urllib.request  # 🌐 EC2のメタデータを取得するために追加
import urllib.error

app = FastAPI()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
DB_FILE = "contact_board.db"

# 🌐 EC2のパブリックIPアドレスを自動取得する関数を追加
def get_ec2_public_ip() -> str:
    """
    AWS EC2のメタデータ（IMDSv2）からパブリックIPアドレスを取得します。
    取得に失敗した場合やローカル環境の場合は '127.0.0.1' を返します。
    """
    token_url = "http://169.254.169.254/latest/api/token"
    ip_url = "http://169.254.169.254/latest/meta-data/public-ipv4"
    
    try:
        # 1. IMDSv2のセッショントークンを取得（有効期限60秒）
        token_req = urllib.request.Request(token_url, method="PUT")
        token_req.add_header("X-aws-ec2-metadata-token-ttl-seconds", "60")
        with urllib.request.urlopen(token_req, timeout=2) as token_res:
            token = token_res.read().decode("utf-8")
        
        # 2. トークンを使ってパブリックIPアドレスを取得
        ip_req = urllib.request.Request(ip_url)
        ip_req.add_header("X-aws-ec2-metadata-token", token)
        with urllib.request.urlopen(ip_req, timeout=2) as ip_res:
            return ip_res.read().decode("utf-8").strip()
            
    except (urllib.error.URLError, TimeoutError):
        # AWS環境ではない（ローカルPCなど）場合はローカルホストを返す
        return "127.0.0.1"

# サーバー起動時に一度だけIPアドレスを確定させてキャッシュしておく
SERVER_PUBLIC_IP = get_ec2_public_ip()


def init_db():
    """データベースのテーブル作成と初期デモデータの登録"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # ユーザーテーブルの作成
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            hashed_password TEXT NOT NULL
        )
    """)
    
    # 物件テーブルの作成（🔒 signage_token カラムが含まれていることを確認）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            start_date TEXT,
            end_date TEXT,
            agent TEXT,
            tel TEXT,
            location TEXT,
            loop_seconds INTEGER,
            signage_token TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)
    conn.commit()
    
    # データが空の場合のみ、初期デモユーザーと物件を登録
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        hp1 = pwd_context.hash("admin123")
        hp2 = pwd_context.hash("pass123")
        
        cursor.execute("INSERT INTO users (username, hashed_password) VALUES (?, ?)", ("admin", hp1))
        user1_id = cursor.lastrowid
        cursor.execute("INSERT INTO users (username, hashed_password) VALUES (?, ?)", ("user2", hp2))
        user2_id = cursor.lastrowid
        
        # 🔒【要確認】VALUES の中の「?」が9個あり、末尾に secrets.token_hex(20) が渡されているか確認
        cursor.execute("""
            INSERT INTO projects (user_id, name, start_date, end_date, agent, tel, location, loop_seconds, signage_token)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user1_id, "エイルヴィラニ日市 大規模修繕工事", "2026-09-01", "2027-01-31", "榮 真吾", "080-8555-1626", "福岡県筑紫野市", 5, secrets.token_hex(20)))
        
        cursor.execute("""
            INSERT INTO projects (user_id, name, start_date, end_date, agent, tel, location, loop_seconds, signage_token)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user2_id, "テストマンション大規模修繕工事（第2現場）", "2026-10-01", "2027-03-15", "現場 次郎", "090-9999-8888", "東京都千代田区", 3, secrets.token_hex(20)))
        conn.commit()
    conn.close()

# 念ため関数を呼び出す
init_db()


# 📂 サーバー用サブフォルダの物理配置管理
BASE_STORAGE = "server_storage"
app.mount("/files", StaticFiles(directory=BASE_STORAGE), name="files")

def get_project_folders(project_id):
    """物件IDごとのサブフォルダパスを生成（自動作成）"""
    folders = {
        "a3": os.path.join(BASE_STORAGE, str(project_id), "a3"),
        "a4_left": os.path.join(BASE_STORAGE, str(project_id), "a4_left"),
        "a4_right": os.path.join(BASE_STORAGE, str(project_id), "a4_right"),
    }
    for p in folders.values():
        os.makedirs(p, exist_ok=True)
    return folders

def get_stored_files(project_id):
    folders = get_project_folders(project_id)
    return {
        "docs_a3": sorted([f for f in os.listdir(folders["a3"]) if f.lower().endswith('.pdf')]) if os.path.exists(folders["a3"]) else [],
        "docs_a4_left": sorted([f for f in os.listdir(folders["a4_left"]) if f.lower().endswith('.pdf')]) if os.path.exists(folders["a4_left"]) else [],
        "docs_a4_right": sorted([f for f in os.listdir(folders["a4_right"]) if f.lower().endswith('.pdf')]) if os.path.exists(folders["a4_right"]) else [],
    }

def fetch_project_info(project_id):
    """DBから特定の物件情報を取得して工期などを自動計算"""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return None
        
    project = dict(row)
    start = datetime.strptime(project["start_date"], "%Y-%m-%d")
    end = datetime.strptime(project["end_date"], "%Y-%m-%d")
    today = datetime.now()
    
    total_days = (end - start).days + 1
    elapsed_days = (today - start).days + 1
    if elapsed_days < 1: elapsed_days = 0
    
    files = get_stored_files(project_id)
    
    return {
        **project,
        **files,
        "total_days": total_days,
        "elapsed_days": elapsed_days,
        "today_str": today.strftime("%Y年%m月%d日"),
        # 🌐 自動取得したパブリックIPアドレスをURLに組み込むよう変更
        "mobile_url": f"http://{SERVER_PUBLIC_IP}:8000/mobile/{project_id}"
    }

# 🔒 【新規追加】Cookieから現在のログインユーザーを特定するセキュリティ関数
from fastapi import Request

async def get_current_user_id(request: Request) -> Optional[int]:
    user_id = request.cookies.get("user_id")
    if not user_id:
        return None
    return int(user_id)

# 🔒 ログイン画面の表示
@app.get("/login", response_class=HTMLResponse)
def login_page(error: Optional[str] = None):
    error_msg = f"<p style='color:red;'>{error}</p>" if error else ""
    return f"""
    <html>
    <head>
        <title>ログイン - コンタクトボード</title>
        <style>
            body {{ font-family:sans-serif; background:#fafafa; display:flex; justify-content:center; align-items:center; height:100vh; margin:0; }}
            .login-box {{ background:#fff; padding:40px; border-radius:8px; box-shadow:0 4px 12px rgba(0,0,0,0.1); width:320px; }}
            input {{ width:100%; padding:10px; margin:10px 0; border:1px solid #ccc; border-radius:4px; box-sizing:border-box; }}
            button {{ width:100%; padding:10px; background:#00a0e9; color:#fff; border:none; border-radius:4px; cursor:pointer; font-weight:bold; }}
        </style>
    </head>
    <body>
        <div class="login-box">
            <h2>🏢 ログイン</h2>
            {error_msg}
            <form action="/login" method="post">
                <input type="text" name="username" placeholder="ユーザーID" required>
                <input type="password" name="password" placeholder="パスワード" required>
                <button type="submit">ログイン</button>
            </form>
        </div>
    </body>
    </html>
    """

# 🔒 ログイン認証処理
@app.post("/login")
def login(username: str = Form(...), password: str = Form(...)):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
    user = cursor.fetchone()
    conn.close()
    
    if user and pwd_context.verify(password, user["hashed_password"]):
        # 認証成功時、簡易的にCookieへユーザーIDを保存してリダイレクト
        response = RedirectResponse(url="/admin", status_code=303)
        response.set_cookie(key="user_id", value=str(user["id"]), httponly=True)
        return response
    else:
        return RedirectResponse(url="/login?error=ユーザーIDまたはパスワードが違います", status_code=303)

# 🔒 ログアウト処理
@app.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(key="user_id")
    return response


# 🟢 サイネージアプリ用 API（🔒所有権認証トークンのチェックを追加）
@app.get("/api/signage-data/{project_id}")
def get_signage_data(project_id: int, token: str):  # 🔒 引数に token を追加
    info = fetch_project_info(project_id)
    if not info:
        raise HTTPException(status_code=404, detail="Project Not Found")
    
    # 🔒 送られてきたトークンが、DB内のトークンと一致するか厳格チェック
    if info["signage_token"] != token:
        raise HTTPException(status_code=403, detail="Access Denied: Invalid Token")
        
    return info


# 📝 一元管理画面（🔒 テナンシー制限を追加）
@app.get("/admin", response_class=HTMLResponse)
def admin_panel(current_id: Optional[int] = None, user_id: Optional[int] = Depends(get_current_user_id)):
    if user_id is None:
        return RedirectResponse(url="/login", status_code=303)
        
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # 🔒 ログイン中のユーザーに属する物件のみを取得 (マルチテナント隔離)
    cursor.execute("SELECT id, name FROM projects WHERE user_id = ? ORDER BY id DESC", (user_id,))
    all_projects = cursor.fetchall()
    
    # ユーザー名を取得してダッシュボードに表示
    cursor.execute("SELECT username FROM users WHERE id = ?", (user_id,))
    user_row = cursor.fetchone()
    username = user_row[0] if user_row else "Unknown"
    conn.close()
    
    # 表示する物件IDの決定（選択がない場合はログインユーザーの最新の物件）
    if current_id is None and all_projects:
        current_id = all_projects[0][0]
        
    info = fetch_project_info(current_id) if current_id else None
    
    # 他のテナントの物件IDをURL直打ちでクラッキングしようとした場合の防御
    if info and info["user_id"] != user_id:
        return "閲覧権限がありません。<a href='/admin'>戻る</a>"
        
    def make_file_list(files, folder_key):
        if not files: return "<li>ファイルなし</li>"
        return "".join([f"<li>📄 {f} <a href='/admin/delete/{current_id}/{folder_key}/{f}' style='color:red; font-size:12px;'>[削除]</a></li>" for f in files])

    options_html = "".join([f"<option value='{pid}' {'selected' if pid==current_id else ''}>{pname}</option>" for pid, pname in all_projects])

    # 物件が1件もない場合の条件分岐
    if not info:
        main_content = "<div class='col'><h3>管理中の物件がありません。右側のフォームから追加してください。</h3></div>"
        pdf_section = ""
    else:
        main_content = f"""
        <div class="col" style="flex:1.5;">
            <div class="section" style="box-shadow:none; padding:0;">
                <h3>1. 設定変更（物件ID: {info['id']}）</h3>
                
                <!-- 📋 コピペ専用ブロック（ここを追加） -->
                <div style="background:#fffbe6; padding:12px; border-radius:6px; margin-bottom:15px; border:1px solid #ffe58f; font-size:13px; color:#555;">
                    📌 <b>この現場のサイネージPC起動コマンド</b>（バッチファイル作成時にそのままコピー＆ペーストしてください）
                    <textarea readonly style="width:100%; height:45px; background:#fff; margin-top:6px; padding:6px; font-family:monospace; font-size:12px; border:1px solid #ccc; border-radius:4px; resize:none; font-weight:bold; color:#000;" onclick="this.select();">python signage_app.py --id {info['id']} --token {info['signage_token']} --server {SERVER_PUBLIC_IP}:8000 --kiosk</textarea>
                    <span style="font-size:11px; color:#888;">※枠内をクリックすると全選択されます。</span>
                </div>

                <form action="/admin/update/{info['id']}" method="post" class="form-grid">
                    <div>工事・物件名:</div><div><input type="text" name="name" value="{info['name']}"></div>
                    <div>着工日:</div><div><input type="date" name="start_date" value="{info['start_date']}"></div>
                    <div>完工日:</div><div><input type="date" name="end_date" value="{info['end_date']}"></div>
                    <div>現場代理人:</div><div><input type="text" name="agent" value="{info['agent']}"></div>
                    <div>連絡先TEL:</div><div><input type="text" name="tel" value="{info['tel']}"></div>
                    <div>物件所在地:</div><div><input type="text" name="location" value="{info['location']}"></div>
                    <div>ループ切替:</div><div><input type="number" name="loop_seconds" value="{info['loop_seconds']}" style="width:80px;"> 秒</div>
                    <div></div>
                    <div style="display: flex; gap: 12px; align-items: center; width: max-content;">
                        <button type="submit" style="padding: 10px 16px; background: #00a0e9; color: #fff; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; font-size: 14px; white-space: nowrap;">この物件の設定を保存</button>
                        <a href="/admin/project/delete/{info['id']}" onclick="return confirm('🚨 本当に物件を完全削除しますか？中身のPDFもすべて消去され、元に戻せません。')" style="padding: 10px 16px; background: #ff4d4f; color: #fff; border-radius: 4px; text-decoration:none; font-weight: bold; font-size: 14px; white-space: nowrap;">🚨 この物件を削除する</a>
                    </div>
                </form>
            </div>
        </div>
        """

        pdf_section = f"""
        <div class="section" style="margin-top:20px;">
            <h3>2. 資料PDFの登録（枠内にファイルを直接ドラッグ＆ドロップ、またはクリックして選択）</h3>
            <div class="grid">
                <div class="col">
                    <h4>【左】A4区画（作業内容等）</h4>
                    <div class="drop-zone" id="dz-a4_left">ここにPDFをドラッグ＆ドロップ<br><span style="font-size:12px; color:#666;">（またはクリックしてファイル選択）</span></div>
                    <form id="form-a4_left" action="/admin/upload/{info['id']}/a4_left" method="post" enctype="multipart/form-data">
                        <input type="file" id="file-a4_left" name="file" accept=".pdf" onchange="document.getElementById('form-a4_left').submit();" style="display:none;">
                    </form>
                    <ul>{make_file_list(info['docs_a4_left'], 'a4_left')}</ul>
                </div>
                <div class="col">
                    <h4>【中央】A3区画（お知らせ図面等）</h4>
                    <div class="drop-zone" id="dz-a3">ここにPDFをドラッグ＆ドロップ<br><span style="font-size:12px; color:#666;">（またはクリックしてファイル選択）</span></div>
                    <form id="form-a3" action="/admin/upload/{info['id']}/a3" method="post" enctype="multipart/form-data">
                        <input type="file" id="file-a3" name="file" accept=".pdf" onchange="document.getElementById('form-a3').submit();" style="display:none;">
                    </form>
                    <ul>{make_file_list(info['docs_a3'], 'a3')}</ul>
                </div>
                <div class="col">
                    <h4>【右】A4区画（洗濯物情報等）</h4>
                    <div class="drop-zone" id="dz-a4_right">ここにPDFをドラッグ＆ドロップ<br><span style="font-size:12px; color:#666;">（またはクリックしてファイル選択）</span></div>
                    <form id="form-a4_right" action="/admin/upload/{info['id']}/a4_right" method="post" enctype="multipart/form-data">
                        <input type="file" id="file-a4_right" name="file" accept=".pdf" onchange="document.getElementById('form-a4_right').submit();" style="display:none;">
                    </form>
                    <ul>{make_file_list(info['docs_a4_right'], 'a4_right')}</ul>
                </div>
            </div>
        </div>
        <p><a href="/mobile/{info['id']}" target="_blank">📱 この物件のスマホ用画面（デモ用）を開く</a></p>
        """

    return f"""
    <html>
    <head>
        <title>コンタクトボード 複数拠点一元管理システム</title>
        <style>
            body {{ font-family:sans-serif; padding:20px; background:#fafafa; color:#333; }}
            .section {{ background:#fff; padding:20px; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.05); margin-bottom:20px; }}
            .drop-zone {{ border: 2px dashed #00a0e9; background: #e6f7ff; padding: 15px; text-align: center; border-radius: 8px; cursor: pointer; margin-bottom: 10px; }}
            .grid {{ display: flex; gap: 20px; }}
            .col {{ flex: 1; background: #fff; padding: 15px; border-radius: 8px; border: 1px solid #ddd; }}
            .form-grid {{ display: grid; grid-template-columns: 140px 1fr; gap: 10px; align-items: center; max-width: 500px; }}
            input[type="text"], input[type="date"], input[type="number"], select {{ padding: 6px; width: 100%; border: 1px solid #ccc; border-radius: 4px; }}
            .nav-bar {{ background:#022547; color:#fff; padding:15px; border-radius:8px; margin-bottom:20px; display:flex; justify-content:space-between; align-items:center; }}
            .logout-btn {{ background:#ff4d4f; color:white; padding:6px 12px; text-decoration:none; border-radius:4px; font-weight:bold; font-size:13px; }}
        </style>
        <script>
            function switchProject(pid) {{
                window.location.href = "/admin?current_id=" + pid;
            }}
            function confirmDelete(projId, projName) {{
                if (confirm("⚠️ 警告: 物件「" + projName + "」を完全に削除しますか？\\n登録されているすべてのPDF資料の配置情報も消去されます。")) {{
                    if (confirm("🚨 本当に本当によろしいですか？\\nこの操作は取り消せません。また、物件IDは永久欠番となります。")) {{
                        window.location.href = "/admin/project/delete/" + projId;
                    }}
                }}
            }}
        </script>
    </head>
    <body>
        
        <div class="nav-bar">
            <h2>🏢 拠点一元管理ダッシュボード <span style="font-size:14px; font-weight:normal; color:#ccc;">(ユーザー: {username})</span></h2>
            <div>
                <span>🔍 管理対象の物件を選択：</span>
                <select onchange="switchProject(this.value)" style="width:300px; display:inline-block;">
                    {options_html}
                </select>
                <a href="/logout" class="logout-btn" style="margin-left:15px;">ログアウト</a>
            </div>
        </div>

        <div class="grid">
            {main_content}
            
            <div class="col">
                <h3>➕ 新しい工事現場（物件）を追加</h3>
                <form action="/admin/create" method="post" class="form-grid">
                    <input type="hidden" name="user_id" value="{user_id}">
                    <div>工事・物件名:</div><div><input type="text" name="name" required placeholder="〇〇マンション大規模修繕"></div>
                    <div>着工日:</div><div><input type="date" name="start_date" required></div>
                    <div>完工日:</div><div><input type="date" name="end_date" required></div>
                    <div>現場代理人:</div><div><input type="text" name="agent" placeholder="山田 太郎"></div>
                    <div>連絡先TEL:</div><div><input type="text" name="tel" placeholder="090-0000-0000"></div>
                    <div>物件所在地:</div><div><input type="text" name="location" placeholder="福岡県博多区"></div>
                    <div>ループ切替:</div><div><input type="number" name="loop_seconds" value="5" style="width:80px;"> 秒</div>
                    <div></div><div><button type="submit" style="padding:10px 20px; background:#52c41a; color:#fff; border:none; border-radius:4px; cursor:pointer; font-weight:bold;">新規物件を作成</button></div>
                </form>
            </div>
        </div>

        {pdf_section}

        <script>
            function setupDropZone(zoneId, fileInputId, formId) {{
                const zone = document.getElementById(zoneId);
                const input = document.getElementById(fileInputId);
                const form = document.getElementById(formId);
                if(!zone) return;
                zone.addEventListener("click", () => input.click());
                zone.addEventListener("dragover", (e) => {{
                    e.preventDefault(); zone.style.background = "#bae7ff"; zone.style.borderColor = "#0066cc";
                }});
                zone.addEventListener("dragleave", () => {{
                    zone.style.background = "#e6f7ff"; zone.style.borderColor = "#00a0e9";
                }});
                zone.addEventListener("drop", (e) => {{
                    e.preventDefault(); zone.style.background = "#e6f7ff"; zone.style.borderColor = "#00a0e9";
                    if (e.dataTransfer.files.length > 0) {{
                        input.files = e.dataTransfer.files; form.submit();
                    }}
                }});
            }}
            setupDropZone("dz-a4_left", "file-a4_left", "form-a4_left");
            setupDropZone("dz-a3", "file-a3", "form-a3");
            setupDropZone("dz-a4_right", "file-a4_right", "form-a4_right");
        </script>
    </body>
    </html>
    """




# 物件の新規作成 API
@app.post("/admin/create")
def create_project(name: str = Form(...), start_date: str = Form(...), end_date: str = Form(...), agent: str = Form(...), tel: str = Form(...), location: str = Form(...), loop_seconds: int = Form(...), user_id: int = Form(...), logged_in_user: Optional[int] = Depends(get_current_user_id)):
    if logged_in_user is None or logged_in_user != user_id:
        raise HTTPException(status_code=403, detail="Unauthorized")
    
    # 🔒 新規物件作成時に、暗号論的に強固なユニークトークンを自動発行
    token = secrets.token_hex(20)
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO projects (user_id, name, start_date, end_date, agent, tel, location, loop_seconds, signage_token)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, name, start_date, end_date, agent, tel, location, loop_seconds, token))  # 🔒 トークンを保存
    new_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/admin?current_id={new_id}", status_code=303)


# 物件情報の更新 API（🔒 所有権チェック）
@app.post("/admin/update/{project_id}")
def update_project(project_id: int, name: str = Form(...), start_date: str = Form(...), end_date: str = Form(...), agent: str = Form(...), tel: str = Form(...), location: str = Form(...), loop_seconds: int = Form(...), user_id: Optional[int] = Depends(get_current_user_id)):
    if user_id is None:
        return RedirectResponse(url="/login", status_code=303)
        
    info = fetch_project_info(project_id)
    if not info or info["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Unauthorized")
        
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE projects SET name=?, start_date=?, end_date=?, agent=?, tel=?, location=?, loop_seconds=? WHERE id=?
    """, (name, start_date, end_date, agent, tel, location, loop_seconds, project_id))
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/admin?current_id={project_id}", status_code=303)

# PDFアップロード API（🔒 所有権チェック）
@app.post("/admin/upload/{project_id}/{folder_key}")
async def upload_pdf(project_id: int, folder_key: str, file: UploadFile = File(...), user_id: Optional[int] = Depends(get_current_user_id)):
    if user_id is None:
        return RedirectResponse(url="/login", status_code=303)
        
    info = fetch_project_info(project_id)
    if not info or info["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Unauthorized")
        
    folders = get_project_folders(project_id)
    if folder_key in folders and file.filename.lower().endswith('.pdf'):
        save_path = os.path.join(folders[folder_key], file.filename)
        with open(save_path, "wb") as b:
            b.write(await file.read())
    return RedirectResponse(url=f"/admin?current_id={project_id}", status_code=303)

# PDF削除 API（🔒 所有権チェック）
@app.get("/admin/delete/{project_id}/{folder_key}/{filename}")
def delete_pdf(project_id: int, folder_key: str, filename: str, user_id: Optional[int] = Depends(get_current_user_id)):
    if user_id is None:
        return RedirectResponse(url="/login", status_code=303)
        
    info = fetch_project_info(project_id)
    if not info or info["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Unauthorized")
        
    folders = get_project_folders(project_id)
    if folder_key in folders:
        file_path = os.path.join(folders[folder_key], filename)
        if os.path.exists(file_path):
            os.remove(file_path)
    return RedirectResponse(url=f"/admin?current_id={project_id}", status_code=303)

# 📱 物件別オープンスマホサイト（一般住民用：仕様通り認証不要のまま維持）
@app.get("/mobile/{project_id}", response_class=HTMLResponse)
def mobile_site(project_id: int):
    info = fetch_project_info(project_id)
    if not info: 
        return "物件データがありません。"
    
    def make_mobile_links(files, folder_key):
        if not files: 
            return "<li>現在、掲示資料はありません</li>"
        return "".join([f"<li><a href='/files/{project_id}/{folder_key}/{f}' target='_blank'>📄 {f}</a></li>" for f in files])
        
    html_content = f"""
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>スマホ用掲示板</title>
    </head>
    <body style="font-family:sans-serif; padding:15px; background:#f4f4f4;">
        <div style="background:#fff; padding:15px; margin-bottom:15px; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.05);">
            <h2 style="margin-top:0; color:#333;">{info['name']}</h2>
            <p style="margin:5px 0;"><b>担当：</b>{info['agent']} (TEL: {info['tel']})</p>
            <p style="margin:5px 0;"><b>所在地：</b>{info['location']}</p>
        </div>
        <div style="background:#fff; padding:15px; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,0.05);">
            <h3 style="margin-top:0; color:#00a572; border-bottom:2px solid #00a572; padding-bottom:5px;">📂 掲示資料一覧</h3>
            <h4 style="margin-bottom:5px; color:#555;">■ A4左（作業内容等）：</h4>
            <ul style="padding-left:20px; margin-top:5px;">{make_mobile_links(info['docs_a4_left'], 'a4_left')}</ul>
            <h4 style="margin-bottom:5px; color:#555;">■ A3中央（図面等）：</h4>
            <ul style="padding-left:20px; margin-top:5px;">{make_mobile_links(info['docs_a3'], 'a3')}</ul>
            <h4 style="margin-bottom:5px; color:#555;">■ A4右（洗濯物情報等）：</h4>
            <ul style="padding-left:20px; margin-top:5px;">{make_mobile_links(info['docs_a4_right'], 'a4_right')}</ul>
        </div>
    </body>
    </html>
    """
    return html_content

# 🔒 オブジェクトとDBから物件を完全に消去するAPI（🔒 所有権チェック）
@app.get("/admin/project/delete/{project_id}")
def delete_project_completely(project_id: int, user_id: Optional[int] = Depends(get_current_user_id)):
    if user_id is None:
        return RedirectResponse(url="/login", status_code=303)
        
    info = fetch_project_info(project_id)
    if not info or info["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Unauthorized")
        
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    conn.commit()
    conn.close()
    
    import shutil
    project_folder = os.path.join(BASE_STORAGE, str(project_id))
    if os.path.exists(project_folder):
        shutil.rmtree(project_folder)
        
    return RedirectResponse(url="/admin", status_code=303)


if __name__ == "__main__":
    import uvicorn
    # AWS本番デプロイ時を考慮して、全てのIPからの接続(0.0.0.0)を受け付け可能に変更
    uvicorn.run(app, host="0.0.0.0", port=8000)

