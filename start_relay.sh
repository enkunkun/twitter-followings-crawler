#!/usr/bin/env bash
set -e

# ==========================================
# twitter-api-safe-relay セットアップ＆起動スクリプト
# ==========================================

RELAY_DIR="twitter_api_safe_relay"
REPO_URL="https://github.com/fa0311/twitter_api_safe_relay.git"

# 1. pnpmの確認
if ! command -v pnpm &> /dev/null; then
    echo "エラー: 'pnpm' がインストールされていません。"
    echo "事前に 'npm install -g pnpm' 等でインストールしてください。"
    exit 1
fi

# 2. リポジトリのクローン
if [ ! -d "$RELAY_DIR" ]; then
    echo "=> リレーサーバーのリポジトリをクローンします..."
    git clone "$REPO_URL" "$RELAY_DIR"
else
    echo "=> リレーサーバーは既にクローンされています ($RELAY_DIR)"
fi

cd "$RELAY_DIR"

# 3. 依存関係とPlaywrightブラウザのインストール
echo "=> 依存関係をインストールしています..."
pnpm install

echo "=> Playwright用ブラウザ(Chromium)をインストールしています..."
pnpm --filter twitter-api-safe-relay exec playwright install chromium

# 4. headlessモードの切り替え設定（環境変数 HEADLESS=true でバックグラウンド起動可能）
if [ "$HEADLESS" = "true" ]; then
    echo "=> Headlessモード(画面なし)で起動するよう設定を書き換えます..."
    sed -i.bak 's/"headless": false/"headless": true/g' settings.json
else
    echo "=> 画面ありモードで起動するよう設定を書き換えます..."
    sed -i.bak 's/"headless": true/"headless": false/g' settings.json
fi

# 5. サーバー起動
echo "======================================================"
if [ "$HEADLESS" != "true" ]; then
    echo "ブラウザが起動します。手動で X (Twitter) にログインしてください。"
    echo "ログインが完了し、ターミナルにサーバー起動のログが出たら準備完了です！"
    echo "次回以降、画面を出さずに起動したい場合は:"
    echo "  HEADLESS=true ./start_relay.sh"
    echo "として実行してください。"
else
    echo "Headlessモード（画面なし）で起動します。"
fi
echo "======================================================"

# 起動
pnpm dev:relay
