import sqlite3
from passlib.context import CryptContext

# パスワードハッシュ化の設定（server.pyと共通）
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
DB_FILE = "contact_board.db"

def add_new_tenant(username, plain_password):
    """新しいテナントユーザーをデータベースに安全に登録する"""
    # 1. パスワードを強力なアルゴリズム(bcrypt)でハッシュ化
    hashed_password = pwd_context.hash(plain_password)
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    try:
        # 2. データベースへインサート
        cursor.execute("""
            INSERT INTO users (username, hashed_password)
            VALUES (?, ?)
        """, (username, hashed_password))
        conn.commit()
        print(f"🎉 ユーザー「{username}」の登録が完了しました！")
        
    except sqlite3.IntegrityError:
        # すでに同じユーザーIDが存在する場合のエラーハンドリング
        print(f"❌ エラー: ユーザーID「{username}」は既に登録されています。")
        
    finally:
        conn.close()

if __name__ == "__main__":
    print("--- 👤 コンタクトボード 新規ユーザー登録 ---")
    input_username = input("希望するユーザーIDを入力してください: ").strip()
    input_password = input("パスワードを入力してください: ").strip()
    
    if input_username and input_password:
        add_new_tenant(input_username, input_password)
    else:
        print("❌ ユーザーIDとパスワードは必須入力です。")
