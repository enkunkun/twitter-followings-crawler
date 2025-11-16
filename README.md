# Twitter Followings Crawler (via Nitter)

Twitter のアーカイブ（data/following.js）に含まれる フォロー一覧（accountId）から、Nitter 経由でプロフィール情報・画像を取得し、Cosense 用 JSON を生成するツールです。

- 一括クロール
- 差分クロール（resume）
- 完全再クロール（force）
- Cosense だけ再出力（export-only）
- 画像の時系列保存（pbs 名＋timestamp）
- Nitter 複数インスタンスの自動リトライ
- 中断しても再開可能（success.jsonl に逐次追記）
- プロフィール画像/バナーを自動ダウンロード

## 特徴

### Twitter API 不要

Nitter からスクレイピングするため、Twitter API Key は不要です。

### フォロー中のユーザー情報を一括取得

- screen_name
- name
- bio
- location
- joined
- profile_pic
- profile_banner
- fetched_at
- fetched_from（Nitter インスタンス）

### プロフィール画像 / バナーをダウンロード

画像は以下のように時系列で保存します。

```text
images/<account_id>/<profile|banner>/<timestamp>_<pbs_filename>.jpg
images/<account_id>/profile.jpg   (最新)
images/<account_id>/banner.jpg    (最新)
````

### Cosense JSON 出力

`output/cosense_followings.json` に、以下の形式で出力します。

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

## セットアップ

### 0. uv が未インストールの場合

公式手順に従ってインストールしてください。

[uv の公式ドキュメント（Getting Started）](https://docs.astral.sh/uv/getting-started/)

### 1. 仮想環境の作成

```bash
uv venv
uv sync
```

### 2. following.js を配置

Twitter アーカイブの following.js を data/ に置いてください。

```text
data/following.js
```

### 3. 実行準備

```text
mkdir -p logs images output
```

## 使用方法（コマンド一覧）

### 🔹 1. 通常モード

```bash
uv run fetch_followings.py
```

- success.jsonl に無いユーザーだけ処理

### 🔹 2. 再開モード（resume）

```bash
uv run fetch_followings.py --resume
```

- 前回の中断から続きだけ処理

### 🔹 3. 全件取得モード（force）

```bash
uv run fetch_followings.py --force
```

- 全アカウントの HTML を再取得
- 最新情報で success.jsonl を完全更新したいときに使用
- success.jsonl は追記
- 画像は URL が変わった時だけ追加保存（差分保存）

### 🔹 4. 1 件だけ試すモード（single）

```bash
uv run fetch_followings.py --single
```

- 最初の 1 件だけ取得し、結果を JSON で出力する

### 🔹 5. Cosense 再出力モード（export-only）

```bash
uv run fetch_followings.py --export-only
```

- success.jsonl から cosense_followings.json を再生成

## 出力ファイル

```text
logs/success.jsonl              # 逐次追記される成功ログ
images/<id>/profile.jpg         # 最新プロフィール画像（シンボリックリンクまたはコピー）
images/<id>/banner.jpg          # 最新バナー画像
images/<id>/profile/<timestamp>_<filename>.jpg
images/<id>/banner/<timestamp>_<filename>.jpg
output/cosense_followings.json  # Cosense インポート用JSON
```

## ライセンス

MIT License
