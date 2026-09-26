import os
import sqlite3
from fastapi import FastAPI, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from datetime import datetime

app = FastAPI()

# 📂 データベース（SQLite）のセットアップ
DB_FILE = "contact_board.db"

def init_db():
    """データベースのテーブル作成と初期デモデータの登録"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # 物件（現場）マスタテーブル
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            start_date TEXT,
            end_date TEXT,
            agent TEXT,
            tel TEXT,
            location TEXT,
            loop_seconds INTEGER
        )
    """)
    conn.commit()
    
    # データが空の場合のみ、初期デモ物件を登録
    cursor.execute("SELECT COUNT(*) FROM projects")
    if cursor.fetchone()[0] == 0:
        cursor.execute("""
            INSERT INTO projects (name, start_date, end_date, agent, tel, location, loop_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ("エイルヴィラニ日市 大規模修繕工事", "2026-09-01", "2027-01-31", "榮 真吾", "080-8555-1626", "福岡県筑紫野市", 5))
        cursor.execute("""
            INSERT INTO projects (name, start_date, end_date, agent, tel, location, loop_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ("テストマンション大規模修繕工事（第2現場）", "2026-10-01", "2027-03-15", "現場 次郎", "090-9999-8888", "東京都千代田区", 3))
        conn.commit()
    conn.close()

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
        "docs_a3": sorted([f for f in os.listdir(folders["a3"]) if f.lower().endswith('.pdf')]),
        "docs_a4_left": sorted([f for f in os.listdir(folders["a4_left"]) if f.lower().endswith('.pdf')]),
        "docs_a4_right": sorted([f for f in os.listdir(folders["a4_right"]) if f.lower().endswith('.pdf')]),
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
        "mobile_url": f"http://127.0.0.1:8000/mobile/{project_id}"
    }

# 🟢 サイネージアプリ用 API (末尾の数字が物件IDになります)
@app.get("/api/signage-data/{project_id}")
def get_signage_data(project_id: int):
    info = fetch_project_info(project_id)
    return info if info else {"error": "Project Not Found"}

# 📝 一元管理画面（物件切り替え・新規作成対応版）
@app.get("/admin", response_class=HTMLResponse)
def admin_panel(current_id: int = 1):
    # 全物件リストをDBから取得（プルダウン用）
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM projects ORDER BY id DESC")
    all_projects = cursor.fetchall()
    conn.close()
    
    info = fetch_project_info(current_id)
    if not info:
        return "指定された物件が見つかりません。<a href='/admin'>戻る</a>"
        
    def make_file_list(files, folder_key):
        if not files: return "<li>ファイルなし</li>"
        return "".join([f"<li>📄 {f} <a href='/admin/delete/{current_id}/{folder_key}/{f}' style='color:red; font-size:12px;'>[削除]</a></li>" for f in files])

    # プルダウンのHTML作成
    options_html = "".join([f"<option value='{pid}' {'selected' if pid==current_id else ''}>{pname}</option>" for pid, pname in all_projects])

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
        </style>
        <script>
            function switchProject(pid) {{
                window.location.href = "/admin?current_id=" + pid;
            }}
        </script>
    </head>
    <body>
        
        <div class="nav-bar">
            <h2>🏢 拠点一元管理ダッシュボード</h2>
            <div>
                <span>🔍 管理対象の物件を選択：</span>
                <select onchange="switchProject(this.value)" style="width:300px; display:inline-block;">
                    {options_html}
                </select>
            </div>
        </div>

        <div class="grid">
            <!-- 左側：選択中物件の編集フォーム -->
            <div class="col" style="flex:1.5;">
                <div class="section" style="box-shadow:none; padding:0;">
                    <h3>1. 設定変更（物件ID: {info['id']}）</h3>
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
                            <!-- 保存ボタン（1行表示を維持） -->
                            <button type="submit" style="padding: 10px 16px; background: #00a0e9; color: #fff; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; font-size: 14px; white-space: nowrap;">この物件の設定を保存</button>
                            
                            <!-- ⚠️ 【修正】onclick の中に直接安全な確認ダイアログのスクリプトを仕込みます。これにより100%確実に動きます。 -->
                            <button type="button" 
                                    onclick="var fCheck = confirm('⚠️ 警告: 物件「{info['name']}」を完全に削除しますか？\\n登録されているすべてのPDF資料の配置情報も消去されます。'); if (fCheck) {{ var sCheck = confirm('🚨 本当に本当によろしいですか？\\nこの操作は取り消せません。また、物件ID「{info['id']}」は永久欠番となり、以後再利用できなくなります。'); if (sCheck) {{ window.location.href = '/admin/project/delete/{info['id']}'; }} }}" 
                                    style="padding: 10px 16px; background: #ff4d4f; color: #fff; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; font-size: 14px; white-space: nowrap;">🚨 この物件を削除する</button>
                        </div>
                    </form>
                </div>
            </div>
            
            <!-- 右側：物件の新規追加フォーム -->
            <div class="col">
                <h3>➕ 新しい工事現場（物件）を追加</h3>
                <form action="/admin/create" method="post" class="form-grid">
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

        <div class="section" style="margin-top:20px;">
            <h3>2. 資料PDFの登録（枠内にファイルを直接ドラッグ＆ドロップ、またはクリックして選択）</h3>
            <div class="grid">
                <!-- A4左区画 -->
                <div class="col">
                    <h4>【左】A4区画（作業内容等）</h4>
                    <!-- 🔒 表示テキストを変更 -->
                    <div class="drop-zone" id="dz-a4_left">ここにPDFをドラッグ＆ドロップ<br><span style="font-size:12px; color:#666;">（またはクリックしてファイル選択）</span></div>
                    <form id="form-a4_left" action="/admin/upload/{info['id']}/a4_left" method="post" enctype="multipart/form-data">
                        <input type="file" id="file-a4_left" name="file" accept=".pdf" onchange="document.getElementById('form-a4_left').submit();" style="display:none;">
                    </form>
                    <ul>{make_file_list(info['docs_a4_left'], 'a4_left')}</ul>
                </div>

                <!-- A3中央区画 -->
                <div class="col">
                    <h4>【中央】A3区画（お知らせ図面等）</h4>
                    <div class="drop-zone" id="dz-a3">ここにPDFをドラッグ＆ドロップ<br><span style="font-size:12px; color:#666;">（またはクリックしてファイル選択）</span></div>
                    <form id="form-a3" action="/admin/upload/{info['id']}/a3" method="post" enctype="multipart/form-data">
                        <input type="file" id="file-a3" name="file" accept=".pdf" onchange="document.getElementById('form-a3').submit();" style="display:none;">
                    </form>
                    <ul>{make_file_list(info['docs_a3'], 'a3')}</ul>
                </div>

                <!-- A4右区画 -->
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

        <!-- 🔒 【最重要】ドラッグ＆ドロップの挙動を完全に制御するJavaScriptの組み込み -->
        <script>
            function setupDropZone(zoneId, fileInputId, formId) {{
                const zone = document.getElementById(zoneId);
                const input = document.getElementById(fileInputId);
                const form = document.getElementById(formId);

                // エリアをクリックした時は、ファイル選択画面を開く
                zone.addEventListener("click", () => input.click());

                // ファイルがエリアの上に乗った時（青色を濃くする視覚効果）
                zone.addEventListener("dragover", (e) => {{
                    e.preventDefault();
                    zone.style.background = "#bae7ff";
                    zone.style.borderColor = "#0066cc";
                }});

                // ファイルがエリアから外れた時（元の色に戻す）
                zone.addEventListener("dragleave", () => {{
                    zone.style.background = "#e6f7ff";
                    zone.style.borderColor = "#00a0e9";
                }});

                // ファイルがドロップされた瞬間に、自動でサーバーへアップロード実行
                zone.addEventListener("drop", (e) => {{
                    e.preventDefault();
                    zone.style.background = "#e6f7ff";
                    zone.style.borderColor = "#00a0e9";
                    
                    if (e.dataTransfer.files.length > 0) {{
                        // ドロップされたファイルをファイル入力欄（input）にセットして即送信
                        input.files = e.dataTransfer.files;
                        form.submit();
                    }}
                }});
            }}

            // 3つの区画それぞれに自動アップロード機能を紐付け
            setupDropZone("dz-a4_left", "file-a4_left", "form-a4_left");
            setupDropZone("dz-a3", "file-a3", "form-a3");
            setupDropZone("dz-a4_right", "file-a4_right", "form-a4_right");
        </script>
    </body>
    </html>
    """


# 物件の新規作成 API
@app.post("/admin/create")
def create_project(name: str = Form(...), start_date: str = Form(...), end_date: str = Form(...), agent: str = Form(...), tel: str = Form(...), location: str = Form(...), loop_seconds: int = Form(...)):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO projects (name, start_date, end_date, agent, tel, location, loop_seconds)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (name, start_date, end_date, agent, tel, location, loop_seconds))
    new_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/admin?current_id={new_id}", status_code=303)

# 物件情報の更新 API
@app.post("/admin/update/{project_id}")
def update_project(project_id: int, name: str = Form(...), start_date: str = Form(...), end_date: str = Form(...), agent: str = Form(...), tel: str = Form(...), location: str = Form(...), loop_seconds: int = Form(...)):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE projects SET name=?, start_date=?, end_date=?, agent=?, tel=?, location=?, loop_seconds=? WHERE id=?
    """, (name, start_date, end_date, agent, tel, location, loop_seconds, project_id))
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"/admin?current_id={project_id}", status_code=303)

# PDFアップロード API
@app.post("/admin/upload/{project_id}/{folder_key}")
async def upload_pdf(project_id: int, folder_key: str, file: UploadFile = File(...)):
    folders = get_project_folders(project_id)
    if folder_key in folders and file.filename.lower().endswith('.pdf'):
        save_path = os.path.join(folders[folder_key], file.filename)
        with open(save_path, "wb") as b:
            b.write(await file.read())
    return RedirectResponse(url=f"/admin?current_id={project_id}", status_code=303)

# PDF削除 API
@app.get("/admin/delete/{project_id}/{folder_key}/{filename}")
def delete_pdf(project_id: int, folder_key: str, filename: str):
    folders = get_project_folders(project_id)
    if folder_key in folders:
        file_path = os.path.join(folders[folder_key], filename)
        if os.path.exists(file_path):
            os.remove(file_path)
    return RedirectResponse(url=f"/admin?current_id={project_id}", status_code=303)

# 📱 物件別オープンスマホサイト（URLにIDを付与）
@app.get("/mobile/{project_id}", response_class=HTMLResponse)
def mobile_site(project_id: int):
    info = fetch_project_info(project_id)
    if not info: 
        return "物件データがありません。"
    
    def make_mobile_links(files, folder_key):
        if not files: 
            return "<li>現在、掲示資料はありません</li>"
        return "".join([f"<li><a href='/files/{project_id}/{folder_key}/{f}' target='_blank'>📄 {f}</a></li>" for f in files])
        
    return f"""
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

# 🔒 【新規追加】オブジェクトとDBから物件を完全に消去するAPI
@app.get("/admin/project/delete/{project_id}")
def delete_project_completely(project_id: int):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 1. データベースから物件データを削除
    cursor.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    conn.commit()
    conn.close()
    
    # 2. サーバー内に保管されているその物件用のPDFフォルダ（server_storage/物件ID/）も安全のため丸ごと削除
    import shutil
    project_folder = os.path.join(BASE_STORAGE, str(project_id))
    if os.path.exists(project_folder):
        shutil.rmtree(project_folder) # フォルダごと一括削除
        
    # 削除完了後、ダッシュボードのトップ（初期画面）へ自動リダイレクト
    return RedirectResponse(url="/admin", status_code=303)


if __name__ == "__main__":
    import uvicorn
    # 本番仕様のIPアドレス「127.0.0.1」でWebサーバーを起動
    uvicorn.run(app, host="127.0.0.1", port=8000)

