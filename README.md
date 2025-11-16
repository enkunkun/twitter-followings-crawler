# Twitter Followings Crawler (via Nitter)

Twitter のアーカイブ（following.js）に含まれる **フォロー一覧（accountId）から、  
Nitter 経由でプロフィール情報・画像を取得し、Cosense 用 JSON を生成するツール** です。

- 一括クロール
- 差分クロール（resume）
- 完全再クロール（force）
- Cosense だけ再出力（export-only）
- 画像の時系列保存（pbs 名＋timestamp）
- Nitter 複数インスタンスの自動リトライ
- 中断しても再開可能（success.jsonl に逐次追記）
- プロフィール画像/バナーを自動ダウンロード

すべて `fetch_followings.py` だけで完結します。

---

# 🚀 特徴

### ✔ Twitter API ゼロ  
Nitter からスクレイピングするため、Twitter API 鍵が不要です。

### ✔ フォロー中のユーザー情報を一括取得  
- screen_name  
- name  
- bio  
- location  
- joined  
- profile_pic（pbs 化）  
- profile_banner（pbs 化）  
- fetched_at（ISO/JST）  
- fetched_from（Nitter インスタンス）

### ✔ プロフィール画像 / バナーをダウンロード  
画像は：

```

images/<account_id>/<profile|banner>/<timestamp>_<pbs_filename>.jpg
images/<account_id>/profile.jpg   (最新)
images/<account_id>/banner.jpg    (最新)

````

のように **時系列で保存**されます。

### ✔ Cosense JSON 出力  
`output/cosense_followings.json` に、以下の形式で出力します：

```json
{
  "pages": [
    {
      "title": "@screen_name",
      "lines": [
        "@screen_name",
        "",
        "Name: ...",
        "Bio: ...",
        "Profile Image: [url]",
        "Profile Banner: [url]",
        "Last Updated: ...",
        "#twitter #followings"
      ]
    }
  ]
}
````

---

# 📦 セットアップ

## 0. uv が未インストールの場合

公式手順に従ってインストールしてください。

https://docs.astral.sh/uv/getting-started/

## 1. 仮想環境の作成

```bash
uv venv
uv sync
```

## 2. following.js を配置

Twitter アーカイブの following.js を data/ に置いてください。

```
data/following.js
```

## 3. 実行準備

```
mkdir -p logs images output
```

---

# 🖥 使用方法（コマンド一覧）

## 🔹 1. 通常モード（未取得ユーザーだけ処理）

```bash
uv run fetch_followings.py
```

* success.jsonl に無いユーザーだけ処理
* 中断しても success.jsonl が残るので安全

---

## 🔹 2. 再開モード（resume）

```bash
uv run fetch_followings.py --resume
```

* 前回の中断から続きだけ処理
* 進捗バーは全体の中で正しく表示される

---

## 🔹 3. 全件取得モード（force）

```bash
uv run fetch_followings.py --force
```

**成功済みでも全アカウントの HTML を再取得**
最新情報で success.jsonl を完全更新したいときに使用。

* success.jsonl は継続更新される
* 画像は URL が変わった時だけ追加保存（差分保存）

---

## 🔹 4. 1 件だけ試すモード（single）

```bash
uv run fetch_followings.py --single
```

最初の 1 件だけ取得し、結果を JSON で出力する。

---

## 🔹 5. Cosense 出力だけ行う（export-only）

```bash
uv run fetch_followings.py --export-only
```

* success.jsonl を読み込むだけ
* HTML / 画像取得なし
* Cosense JSON を即再生成
* 壊れた URL（例：pbs が二重など）はプログラム側で自動修復して出力

---

# 📁 出力ファイル

```
logs/success.jsonl              # 逐次追記される成功ログ
images/<id>/profile.jpg         # 最新プロフィール画像（シンボリックリンクまたはコピー）
images/<id>/banner.jpg          # 最新バナー画像
images/<id>/profile/<timestamp>_<filename>.jpg
images/<id>/banner/<timestamp>_<filename>.jpg
output/cosense_followings.json  # Cosense 用ページ
```

---

# 📄 ライセンス

MIT License