import sys
import os
import argparse 
import requests
import pymupdf
import qrcode
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QLabel, QTableWidget, QTableWidgetItem
from PySide6.QtGui import QPixmap, QImage, QFont, QColor
from datetime import datetime

#SERVER_URL = "18.179.223.163:8000"
SERVER_URL = "127.0.0.1:8000"

class SignageWindow(QMainWindow):
    def __init__(self, project_id, token, is_kiosk): # 🔒 tokenを追加
        super().__init__()
        self.setWindowTitle(f"コンタクトボード - 現場ID:{project_id} 起動")
        
        self.project_id = project_id
        self.token = token # 🔒 インスタンス変数に保存
        self.is_kiosk = is_kiosk
        
        self.LOCAL_STORAGE = os.path.join("signage_storage", str(self.project_id))
        for sub in ["a3", "a4_left", "a4_right"]:
            os.makedirs(os.path.join(self.LOCAL_STORAGE, sub), exist_ok=True)
            
        self.idx_a3, self.idx_a4_l, self.idx_a4_r = 0, 0, 0
        self.server_data = None
        self.w_a3, self.w_a4_l, self.w_a4_r, self.target_height = 0, 0, 0, 0

        self.init_ui()
        
        if self.is_kiosk:
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
            self.showFullScreen()
            self.show()
        else:
            self.resize(1280, 720)
            self.show()
        
        # 🔒 【修正・最重要】データを取りに行く（fetch）よりも『先』に、
        # 画面位置と拡大倍率の計算（recalculate_positions）を呼び出してサイズ変数を確定させます！
        self.recalculate_positions()
        
        self.sync_timer = QTimer(self)
        self.sync_timer.timeout.connect(self.fetch_server_data)
        self.sync_timer.start(5000)
        
        self.loop_timer = QTimer(self)
        self.loop_timer.timeout.connect(self.rotate_pages)
        self.loop_timer.start(5000)

        # 🔒 【修正】サイズ変数が確定した状態（0ではない状態）で、満を持してサーバーからデータを取得・描画します
        self.fetch_server_data()


    def init_ui(self):
        self.main_widget = QWidget()
        self.setCentralWidget(self.main_widget)
        self.main_widget.setStyleSheet("background-color: #ffffff;") 

        # 🟢 1. 上部ヘッダー（緑色背景と工事タイトル・日付）
        self.header_container = QWidget(self.main_widget)
        self.header_container.setStyleSheet("background-color: #00a572;") 
        
        self.lbl_obj_name = QLabel("", self.header_container)
        self.lbl_obj_name.setStyleSheet("font-size: 20px; font-weight: bold; color: white; background: transparent;")
        
        # 【修正】「工事用掲示板」を画面のド中央へ配置
        self.lbl_title = QLabel("工事用掲示板", self.header_container)
        self.lbl_title.setStyleSheet("font-size: 32px; font-weight: bold; color: white; background: transparent;")
        self.lbl_title.setAlignment(Qt.AlignCenter)
        
        self.lbl_date = QLabel("", self.header_container)
        self.lbl_date.setStyleSheet("font-size: 22px; font-weight: bold; color: white; background: transparent;")
        self.lbl_date.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        # 📊 スクロールバーなし・枠線クッキリの格子状テーブル
        self.info_table = QTableWidget(2, 6, self.header_container)
        self.info_table.setStyleSheet("""
            QTableWidget { 
                background-color: #ffffff; 
                border: 2px solid #000000; 
                color: #000000; 
                gridline-color: #000000;
            }
        """)
        self.info_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.info_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.info_table.horizontalHeader().setVisible(False)
        self.info_table.verticalHeader().setVisible(False)
        self.info_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.info_table.setFocusPolicy(Qt.NoFocus)

        # 🔵 2. 中央：3区画表示エリア（左から A4左 ➔ A3 ➔ A4右）
        self.area_a4_l = QLabel("A4左", self.main_widget)
        self.area_a4_l.setStyleSheet("background-color: #ffffff; border: 2px solid #00a572;")
        self.area_a4_l.setAlignment(Qt.AlignCenter)
        
        self.area_a3 = QLabel("A3", self.main_widget)
        self.area_a3.setStyleSheet("background-color: #ffffff; border: 2px solid #00a572;")
        self.area_a3.setAlignment(Qt.AlignCenter)
        
        self.area_a4_r = QLabel("A4右", self.main_widget)
        self.area_a4_r.setStyleSheet("background-color: #ffffff; border: 2px solid #00a572;")
        self.area_a4_r.setAlignment(Qt.AlignCenter)

        # 🟡 3. 下部フッターパーツ
        self.lbl_koujichu = QLabel(self.main_widget)
        self.lbl_koujichu.setAlignment(Qt.AlignCenter)
        if os.path.exists("koujichu.png"):
            self.lbl_koujichu.setPixmap(QPixmap("koujichu.png").scaled(110, 110, Qt.KeepAspectRatio, Qt.SmoothTransformation))

        self.qr_container = QWidget(self.main_widget)
        self.qr_container.setStyleSheet("border: 2px solid #000000; background-color: #ffffff;")
        self.qr_img_label = QLabel(self.qr_container)
        self.qr_img_label.setAlignment(Qt.AlignCenter)
        self.qr_text_label = QLabel("読み取り用QRコード", self.qr_container)
        self.qr_text_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #000000; background-color: #ffffff;")
        self.qr_text_label.setAlignment(Qt.AlignCenter)

        self.weather_label = QLabel(self.main_widget)
        self.weather_label.setStyleSheet("font-size: 15px; font-weight: bold; background-color: #222222; color: #ffffff; padding: 10px; border: 1px solid #000000; border-radius: 4px;")
        self.weather_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        
        self.lbl_taihei_logo = QLabel(self.main_widget)
        self.lbl_taihei_logo.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        if os.path.exists("taihei_logo.png"):
            self.lbl_taihei_logo.setPixmap(QPixmap("taihei_logo.png").scaled(280, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        
        self.recalculate_positions()

    def recalculate_positions(self):
        """画面の解像度（全画面等）を感知し、上部テーブルの5列の幅を完璧な比率で固定ロックする"""
        win_w = self.width()
        win_h = self.height()
        
        scale = win_h / 720.0
        if scale <= 0: scale = 1.0

        margin = int(15 * scale)
        gap = int(15 * scale)
        header_h = int(175 * scale)
        
        self.header_container.setGeometry(0, 0, win_w, header_h)
        self.lbl_obj_name.setGeometry(margin, int(12 * scale), int(400 * scale), int(35 * scale))
        self.lbl_obj_name.setStyleSheet(f"font-size: {int(20 * scale)}px; font-weight: bold; color: white; background: transparent;")
        
        self.lbl_title.setGeometry(int(win_w / 2) - int(200 * scale), int(5 * scale), int(400 * scale), int(45 * scale))
        self.lbl_title.setStyleSheet(f"font-size: {int(32 * scale)}px; font-weight: bold; color: white; background: transparent;")
        
        self.lbl_date.setGeometry(win_w - int(300 * scale) - margin, int(12 * scale), int(300 * scale), int(35 * scale))
        self.lbl_date.setStyleSheet(f"font-size: {int(22 * scale)}px; font-weight: bold; color: white; background: transparent;")

        # テーブルのサイズ設定（2行5列）
        table_w = win_w - (margin * 2)
        table_h = int(106 * scale)
        self.info_table.setGeometry(margin, int(55 * scale), table_w, table_h)
        self.info_table.setColumnCount(5) # 5列に厳密固定
        self.info_table.setRowCount(2)    # 2行に厳密固定
        
        self.info_table.setStyleSheet(f"""
            QTableWidget {{ 
                background-color: #ffffff; border: {max(1, int(2*scale))}px solid #000000; color: #000000; gridline-color: #000000;
                font-size: {int(14 * scale)}px;
            }}
        """)
        
        # 🔒 【重要】5つの列の横幅（ピクセル数）を正確に割り振り
        w_col0 = int(table_w * 0.14)  # 「工期」ラベル列
        w_col1 = int(table_w * 0.14)  # 「着工」「完工」ラベル列
        w_col2 = int(table_w * 0.25)  # 日付（値）列
        w_col3 = int(table_w * 0.18)  # 「現場代理人」「連絡先」ラベル列
        w_col4 = table_w - (w_col0 + w_col1 + w_col2 + w_col3) - 4 # 残りすべてを「代理人名・TEL（値）」列へ
        
        self.info_table.setColumnWidth(0, w_col0)
        self.info_table.setColumnWidth(1, w_col1)
        self.info_table.setColumnWidth(2, w_col2)
        self.info_table.setColumnWidth(3, w_col3)
        self.info_table.setColumnWidth(4, w_col4)
        
        self.info_table.setRowHeight(0, int(51 * scale))
        self.info_table.setRowHeight(1, int(51 * scale))

        # （以降のフッター・中央3区画の配置処理は変更なしのため省略）
        footer_h = int(120 * scale)
        footer_y = win_h - footer_h - margin
        self.lbl_koujichu.setGeometry(margin, footer_y, int(110 * scale), footer_h)
        if os.path.exists("koujichu.png"):
            self.lbl_koujichu.setPixmap(QPixmap("koujichu.png").scaled(int(110 * scale), footer_h, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        qr_container_w = int(160 * scale)
        qr_container_x = int(win_w / 2) - int(qr_container_w / 2) - int(100 * scale)
        self.qr_container.setGeometry(qr_container_x, footer_y, qr_container_w, footer_h)
        self.qr_img_label.setGeometry(int(5 * scale), int(5 * scale), qr_container_w - int(10 * scale), int(80 * scale))
        self.qr_text_label.setGeometry(int(2 * scale), int(85 * scale), qr_container_w - int(4 * scale), int(30 * scale))
        self.qr_text_label.setStyleSheet(f"border: none; font-size: {int(13 * scale)}px; font-weight: bold; color: #000000; background-color: #ffffff;")
        self.weather_label.setGeometry(qr_container_x + qr_container_w + gap, footer_y, int(300 * scale), footer_h)
        self.weather_label.setStyleSheet(f"font-size: {int(15 * scale)}px; font-weight: bold; background-color: #222222; color: #ffffff; padding: {int(10 * scale)}px; border: 1px solid #000000; border-radius: 4px;")
        logo_w = int(300 * scale)
        self.lbl_taihei_logo.setGeometry(win_w - logo_w - margin, footer_y, logo_w, footer_h)
        if os.path.exists("taihei_logo.png"):
            self.lbl_taihei_logo.setPixmap(QPixmap("taihei_logo.png").scaled(logo_w, footer_h, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        center_y = header_h + gap
        center_h = footer_y - center_y - gap
        available_w = win_w - (margin * 2) - (gap * 2)
        self.w_a4_l = int(available_w * 0.25)
        self.w_a3 = int(available_w * 0.50)
        self.w_a4_r = int(available_w * 0.25)
        self.target_height = center_h
        x_a4_l = margin
        x_a3 = x_a4_l + self.w_a4_l + gap
        x_a4_r = x_a3 + self.w_a3 + gap
        self.area_a4_l.setGeometry(x_a4_l, center_y, self.w_a4_l, self.target_height)
        self.area_a3.setGeometry(x_a3, center_y, self.w_a3, self.target_height)
        self.area_a4_r.setGeometry(x_a4_r, center_y, self.w_a4_r, self.target_height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.recalculate_positions()
        self.update_display()

    def fetch_server_data(self):
        try:
            # 🔒 【修正】AIフィルターによって削られていたURLアドレスを、ポート番号付きの完全な形に修正します
            url_address = SERVER_URL
            # 🔒 URLの末尾に認証トークンを付与
            response = requests.get(f"http://{url_address}/api/signage-data/{self.project_id}?token={self.token}", timeout=2)


            if response.status_code == 200:
                self.server_data = response.json()
                
                self.lbl_obj_name.setText(self.server_data["name"])
                # 2026年09月26日(土)のように、サーバーの曜日に合わせて動的表示する場合は適宜調整
                self.lbl_date.setText(f"{self.server_data['today_str']} (土)") 
                
                def format_to_ja_date(date_str, weekday_ja):
                    try:
                        dt = datetime.strptime(date_str, "%Y-%m-%d")
                        return dt.strftime(f"%Y年%m月%d日 ({weekday_ja})")
                    except:
                        return f"{date_str} ({weekday_ja})"

                start_ja = format_to_ja_date(self.server_data['start_date'], "火")
                end_ja = format_to_ja_date(self.server_data['end_date'], "日")

                # テーブルのセルの内容を完全にクリアして再マッピング
                self.info_table.clear()
                
                # 🔒 スケール倍率に応じたフォントサイズ調整
                scale = self.height() / 720.0
                f_size = max(11, int(14 * scale))
                f_size_large = max(13, int(16 * scale))

                # 0列目：【工期】セルを縦に2マス結合 ➔ 薄いグレー背景
                self.info_table.setSpan(0, 0, 2, 1)
                self.set_cell_style(0, 0, "工　　期", is_bold=True, font_size=f_size_large, bg_color="#e6e6e6", is_center=True)
                
                # 1列目：【着工】【完工】の項目ラベル ➔ 薄いグレー背景
                self.set_cell_style(0, 1, "着工", is_bold=True, font_size=f_size, bg_color="#e6e6e6", is_center=True)
                self.set_cell_style(1, 1, "完工", is_bold=True, font_size=f_size, bg_color="#e6e6e6", is_center=True)
                
                # 2列目：日付データ ➔ 白背景（コロン記号から表記）
                self.set_cell_style(0, 2, f"： {start_ja}", is_bold=True, font_size=f_size, bg_color="#ffffff")
                self.set_cell_style(1, 2, f"： {end_ja}", is_bold=True, font_size=f_size, bg_color="#ffffff")
                
                # 🔒 【修正】すべてのセルへの書き込みを新関数に統一し、プログラムの内部クラッシュを解消
                # 3列目：【現場代理人】【連絡先】の項目ラベル ➔ 薄いグレー背景
                self.set_cell_style(0, 3, "現場代理人", is_bold=True, font_size=f_size, bg_color="#e6e6e6", is_center=True)
                self.set_cell_style(1, 3, "連  絡  先", is_bold=True, font_size=f_size, bg_color="#e6e6e6", is_center=True)
                
                # 4列目：代理人名・TELデータ ➔ 白背景
                self.set_cell_style(0, 4, f" {self.server_data['agent']}", is_bold=True, font_size=f_size, bg_color="#ffffff")
                self.set_cell_style(1, 4, f" {self.server_data['tel']}", is_bold=True, font_size=f_size, bg_color="#ffffff")
                
                # （※この後に混ざっていた古いsetItem処理を完全に削除しました）
                
                if self.loop_timer.interval() != self.server_data['loop_seconds'] * 1000:
                    self.loop_timer.setInterval(self.server_data['loop_seconds'] * 1000)

                
                #self.generate_qr(self.server_data["mobile_url"])
                self.generate_qr(f"http://{SERVER_URL}/mobile/1")
                                
                self.update_weather(self.server_data["location"])
                self.download_missing_files("a3", self.server_data["docs_a3"])
                self.download_missing_files("a4_left", self.server_data["docs_a4_left"])
                self.download_missing_files("a4_right", self.server_data["docs_a4_right"])
                
                self.update_display()
        except requests.exceptions.RequestException:
            if self.server_data: self.update_display()

    def set_cell_style(self, row, col, text, is_bold=True, font_size=14, bg_color="#ffffff", is_center=False):
        """セルのテキスト、太さ、フォントサイズ、および背景色を完璧に制御する関数"""
        item = QTableWidgetItem(text)
        item.setFont(QFont("sans-serif", font_size, QFont.Bold if is_bold else QFont.Normal))
        item.setForeground(Qt.black)
        item.setBackground(QColor(bg_color)) # 背景色塗りつぶし設定
        item.setTextAlignment(Qt.AlignCenter if is_center else Qt.AlignLeft | Qt.AlignVCenter)
        self.info_table.setItem(row, col, item)

    def generate_qr(self, url):
        """指定のURLからQRコード画像を生成し、テキストの枠線を完全排除して余白を持たせる"""
        # 1. QRコードの画像生成
        qr = qrcode.QRCode(box_size=2, border=0)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        import io
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='PNG')
        qpix = QPixmap()
        qpix.loadFromData(img_byte_arr.getvalue())
        
        # 2. QRコード画像単体の黒枠を消し、白背景にして余白を作る
        self.qr_container.setStyleSheet("border: none; background-color: #ffffff;")
        self.qr_img_label.setStyleSheet("border: 2px solid #000000; background-color: #ffffff;")
        
        target_w = self.qr_img_label.width() - 15
        target_h = self.qr_img_label.height() - 15
        if target_w > 0 and target_h > 0:
            self.qr_img_label.setPixmap(qpix.scaled(target_w, target_h, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            
        # 3. 🔒 【修正】説明テキスト部分の背景と枠線を完全排除（オープンな白地に文字だけにする）
        self.qr_text_label.setStyleSheet("border: none; font-size: 14px; font-weight: bold; color: #000000; background-color: #ffffff;")

    def update_weather(self, location_name):
        self.weather_label.setText(f"📍 所在地：{location_name}\n本日：☀️ 晴れ (降水10%)\n明日：☁️ 曇りのち晴れ")

    def download_missing_files(self, sub_folder, file_list):
        if not self.server_data: return
        for filename in file_list:
            # 🔒 【修正】サイネージPC側の保存先にも、現場ID（self.project_id）のサブフォルダを正確に指定します
            local_path = os.path.join("signage_storage", str(self.project_id), sub_folder, filename)
            if not os.path.exists(local_path):
                url_address = SERVER_URL
                # 🔒 ファイルの差分取得API（StaticFiles）側は認証をかけない、もしくはAPI経由にする仕様に合わせてURLを指定
                # 今回はAPI側でトークン制御を実装したため、データAPI経由で取得するか、トークンを付与します
                url = f"http://{url_address}/files/{self.project_id}/{sub_folder}/{filename}"
                try:
                    res = requests.get(url, timeout=5)
                    if res.status_code == 200:
                        with open(local_path, "wb") as f: f.write(res.content)
                except Exception: pass


    def render_pdf_page(self, pdf_path, label_widget, max_w, max_h):
        """PDFの1ページ目を高精細に画像化してQLabelにぴったり収まるよう描画（最新PyMuPDF仕様対応版）"""
        if not os.path.exists(pdf_path): return
        try:
            doc = pymupdf.open(pdf_path)
            if len(doc) == 0: return
            
            # 🔒 【修正・最重要】doc から明確に「0番目のページ（最初の1ページ）」をロードします
            page = doc.load_page(0) 
            
            # ページから画像（ピックスマップ）を展開
            pix = page.get_pixmap(matrix=pymupdf.Matrix(2.0, 2.0))
            
            qimg = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
            scaled_pixmap = QPixmap.fromImage(qimg).scaled(max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            label_widget.setPixmap(scaled_pixmap)
            doc.close()
        except Exception as e:
            label_widget.setText(f"PDF描画エラー\n{e}")

    def rotate_pages(self):
        """指定秒数ごとに、2枚以上ある区画だけインデックスを進めてループ表示"""
        if not self.server_data: return
        if self.server_data["docs_a3"]: self.idx_a3 = (self.idx_a3 + 1) % len(self.server_data["docs_a3"])
        if self.server_data["docs_a4_left"]: self.idx_a4_l = (self.idx_a4_l + 1) % len(self.server_data["docs_a4_left"])
        if self.server_data["docs_a4_right"]: self.idx_a4_r = (self.idx_a4_r + 1) % len(self.server_data["docs_a4_right"])
        self.update_display()

    def update_display(self):
        """各区画のPDF表示を最新の状態に更新（現場IDフォルダ完全連動版）"""
        if not self.server_data: return
        
        # A4左区画（作業内容など）
        d_a4_l = self.server_data["docs_a4_left"]
        if d_a4_l:
            if self.idx_a4_l >= len(d_a4_l): self.idx_a4_l = 0
            # 🔒 【修正】読み込み元フォルダパスに現場IDを挟み込みます
            path = os.path.join("signage_storage", str(self.project_id), "a4_left", d_a4_l[self.idx_a4_l])
            self.render_pdf_page(path, self.area_a4_l, self.w_a4_l, self.target_height)
        else: 
            self.area_a4_l.setPixmap(QPixmap()); self.area_a4_l.setText("資料なし")

        # A3区画（中央：図面など）
        d_a3 = self.server_data["docs_a3"]
        if d_a3:
            if self.idx_a3 >= len(d_a3): self.idx_a3 = 0
            # 🔒 【修正】読み込み元フォルダパスに現場IDを挟み込みます
            path = os.path.join("signage_storage", str(self.project_id), "a3", d_a3[self.idx_a3])
            self.render_pdf_page(path, self.area_a3, self.w_a3, self.target_height)
        else: 
            self.area_a3.setPixmap(QPixmap()); self.area_a3.setText("資料なし")

        # A4右区画（洗濯物情報など）
        d_a4_r = self.server_data["docs_a4_right"]
        if d_a4_r:
            if self.idx_a4_r >= len(d_a4_r): self.idx_a4_r = 0
            # 🔒 【修正】読み込み元フォルダパスに現場IDを挟み込みます
            path = os.path.join("signage_storage", str(self.project_id), "a4_right", d_a4_r[self.idx_a4_r])
            self.render_pdf_page(path, self.area_a4_r, self.w_a4_r, self.target_height)
        else: 
            self.area_a4_r.setPixmap(QPixmap()); self.area_a4_r.setText("資料なし")
        self.main_widget.update()


    # 🔒 【新規追加】キオスクモード時の専用終了イベントロジック
    def keyPressEvent(self, event):
        """キーボードの入力を監視し、EscキーまたはCtrl+Qでアプリを安全に終了させる"""
        # 1. Escキーが押されたら終了
        if event.key() == Qt.Key_Escape:
            self.close()
            
        # 2. Ctrlキーを押しながら「Q」が押されたら終了
        elif event.modifiers() == Qt.ControlModifier and event.key() == Qt.Key_Q:
            self.close()
            
        else:
            super().keyPressEvent(event)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="コンタクトボード サイネージクライアント")
    parser.add_argument("--id", type=int, default=1, help="物件ID（現場ID）を指定します")
    parser.add_argument("--token", type=str, required=True, help="セキュリティ認証用トークンを指定します") # 🔒 必須引数として追加
    parser.add_argument("--kiosk", action="store_true", help="有効にすると全画面最前面で起動します")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    
    # 🔒 解析したトークンを画面クラスに引き渡して起動
    window = SignageWindow(project_id=args.id, token=args.token, is_kiosk=args.kiosk)
    window.show()
    sys.exit(app.exec())