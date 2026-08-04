# nrc-note-gyokai

note「配管の向こう側」用の、週次下書き自動生成の仕組み。

## やること

毎週月曜9:00(JST)に、GitHub Actionsが自動で:
1. `config/topics.yaml` から未使用のテーマを1つ選ぶ
2. Claude APIで記事の下書きを生成する
3. `output/` フォルダに保存してリポジトリにコミットする
4. Chatworkに「下書きができたぞ」と通知する

投稿(noteへのアップロード)は自動化していない。通知を見て、内容を確認してから手動でnoteに投稿する。

## セットアップ手順

### 1. このリポジトリをGitHubにpushする

```bash
git init
git add .
git commit -m "initial commit"
git remote add origin https://github.com/yucahnman/nrc-note-gyokai.git
git branch -M main
git push -u origin main
```

### 2. GitHub Secretsを設定する

GitHubのリポジトリページ → Settings → Secrets and variables → Actions → New repository secret
から、以下の3つを登録する。

| Secret名 | 値 |
|---|---|
| `ANTHROPIC_API_KEY` | Claude Platform(console.anthropic.com)で発行したAPIキー |
| `CHATWORK_API_TOKEN` | ChatworkのAPIトークン(個人設定 → API連携 から取得) |
| `CHATWORK_ROOM_ID` | 通知を送りたいChatworkルームのID(ルームURLの数字部分) |

### 3. 動作確認(手動実行)

GitHubのリポジトリページ → Actions タブ → 「週次で下書きを生成する」ワークフロー →
「Run workflow」ボタンで、スケジュールを待たずに手動実行してテストできる。

### 4. あとは待つだけ

毎週月曜の朝に、Chatworkに通知が届く。`output/` フォルダに新しい下書きMarkdownが
コミットされているので、内容を確認して、良ければnoteに手動でコピペ投稿する。

## テーマを追加したいとき

`config/topics.yaml` に新しいテーマを追記するだけでよい。フォーマットは既存のものを参考に。

## 今後のToDo

- Markdown記法(太字など)がnoteのエディタでそのまま表示されてしまう問題への対応(自動変換)
- 記事に図解(冷凍サイクルなどの模式図)を自動生成して添付する機能
