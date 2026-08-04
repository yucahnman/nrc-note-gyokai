"""
① ネタ選定 → ② AI下書き生成 → ③ Chatwork通知 を行うスクリプト。
GitHub Actionsから週次で自動実行される想定。

必要な環境変数(GitHub Secretsに設定する):
  ANTHROPIC_API_KEY   … Claude APIキー
  CHATWORK_API_TOKEN  … ChatworkのAPIトークン
  CHATWORK_ROOM_ID     … 通知先のChatworkルームID

やっていること:
  1. config/topics.yaml からテーマ候補を読み込む
  2. state/used_topics.json を見て、まだ使っていないテーマを1つ選ぶ
  3. Claude APIに投げて、記事の下書き(Markdown)を生成する
  4. output/ 配下にMarkdownファイルとして保存する(GitHub Actions側でリポジトリにコミットする)
  5. 使ったテーマを state/used_topics.json に記録する(次回は選ばれないようにする)
  6. Chatworkに「下書きができたよ」という通知を送る

このスクリプトは note への「投稿」はしない。下書きを作るところまで。
投稿は本人が内容を確認してnoteに手動で行う想定。
"""

import os
import json
import yaml
import datetime
import requests
from pathlib import Path
from anthropic import Anthropic

BASE_DIR = Path(__file__).parent
TOPICS_FILE = BASE_DIR / "config" / "topics.yaml"
USED_FILE = BASE_DIR / "state" / "used_topics.json"
OUTPUT_DIR = BASE_DIR / "output"

# コストを抑えるためデフォルトはHaiku 4.5。
# 生成される記事の質が物足りない場合は "claude-sonnet-5" に変更してください
# (Haiku 4.5: $1/$5 per 百万トークン、Sonnet 5: $2/$10 per 百万トークン ※2026年時点)
MODEL_NAME = "claude-haiku-4-5-20251001"

ARTICLE_TEMPLATE_PROMPT = """あなたは「配管の向こう側」というnoteアカウントの語り手です。
以下のキャラクターになりきって、テーマについての記事を作成してください。

# キャラクター設定
冷凍・空調設備の現場で経験を積んできた、30代前半の無骨な先輩。
口数は多くないが、聞かれれば丁寧に教えてくれるタイプ。
偉そうというより、現場で鍛えられてきた自信からくる頼もしさがある。
一人称は「俺」。読者に語りかけるときは「〜なんだ」「〜なんだよな」「教えてやるよ」
「〜だと思ってる奴、多いんだよな」のような、ぶっきらぼうだが面倒見の良い口調を使う。
偉ぶった専門用語の説明はしない。あくまで現場感のある、実感のこもった言葉で語る。

テーマ: {title}
カテゴリ: {category}
関連キーワード: {keywords}

# コンセプト
この記事は「困って検索する人」向けの実務情報記事ではありません。
冷凍・空調の"仕組み"そのものが持つ科学・工学的な面白さを伝え、
「へえ、そうなってるんだ」という驚きや発見を届けることが目的です。
読者は業界知識がゼロの人(学生、施主、他業種の人など)を想定してください。

# 記事の構成(この順番を厳守)
1. 素朴な疑問・意外な事実の提示(読者の「え、なんで？」を引き出す一文から始める)
2. 仕組みの解説(身近な現象・比喩を使いながら、科学的に正確にかみ砕く)
3. 「実はこれ、現場ではこう活きている」という一言(専門会社としての実務との接続。ただし営業色は出さない)
4. まとめ・読者への問いかけ(次にエアコンや冷蔵庫を見たときの見方が変わるような一言)

# 執筆ルール
- 上記のキャラクターの口調を、記事全体で一貫して守ること(タイトル・見出しは通常表記でよいが、本文は必ずキャラクターの語り口で書く)
- 業界知識ゼロの読者でも面白がれる文体。専門用語は必ず身近な例えに置き換える
- 断定しすぎず、事実ベースで正確に。不確かな数値の詳細は書かない
- 文字数は1200〜1800字程度
- タイトルは記事内で繰り返さない。本文(1.の内容)から直接書き始める。見出し(#や##)で記事タイトルを再掲しない
- 見出し(##)を使って読みやすく区切る
- **難易度への配慮(重要)**: 数式・専門的な計算式・業界の専門用語の羅列は避ける。どうしても専門用語を使う場合は、必ずその場で日常的な例えに言い換える。「読者が置いていかれる」文章は絶対に避けること
- 記事の最後に免責文を入れる:「※この記事は一般的な情報提供を目的としており、正確な数値や個別の状況については専門家にご確認ください。」
"""

# 記事末尾に必ず付与する定型フッター(永田冷機工業所への導線)
FOOTER = """

---

この記事は「配管の向こう側」が発信しています。運営は熊本県で50年以上、冷凍・空調設備を手がける[永田冷機工業所](https://nagatareiki.jp)です。
"""


def load_topics():
    with open(TOPICS_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data["topics"]


def load_used_ids():
    if not USED_FILE.exists():
        return set()
    with open(USED_FILE, "r", encoding="utf-8") as f:
        return set(json.load(f))


def save_used_id(topic_id):
    used = load_used_ids()
    used.add(topic_id)
    USED_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(USED_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(used), f, ensure_ascii=False, indent=2)


def select_next_topic():
    topics = load_topics()
    used_ids = load_used_ids()
    for topic in topics:
        if topic["id"] not in used_ids:
            return topic
    return None  # すべて使い切った


def generate_draft(topic):
    client = Anthropic()  # 環境変数 ANTHROPIC_API_KEY を自動で読む
    prompt = ARTICLE_TEMPLATE_PROMPT.format(
        title=topic["title"],
        category=topic["category"],
        keywords="、".join(topic["keywords"]),
    )
    message = client.messages.create(
        model=MODEL_NAME,
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def notify_chatwork(topic, output_path):
    """下書きができたことをChatworkに通知する。トークン未設定ならスキップする。"""
    token = os.environ.get("CHATWORK_API_TOKEN")
    room_id = os.environ.get("CHATWORK_ROOM_ID")
    if not token or not room_id:
        print("Chatworkの環境変数が未設定のため、通知はスキップしました。")
        return

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    file_rel_path = output_path.relative_to(BASE_DIR)
    file_url = f"https://github.com/{repo}/blob/main/{file_rel_path}" if repo else str(output_path)

    body = (
        "[info][title]「配管の向こう側」下書きができたぞ[/title]"
        f"テーマ: {topic['title']}\n"
        f"カテゴリ: {topic['category']}\n"
        f"確認はこちら: {file_url}\n"
        "内容を確認して、良ければnoteに手動で投稿してくれ。[/info]"
    )

    resp = requests.post(
        f"https://api.chatwork.com/v2/rooms/{room_id}/messages",
        headers={"X-ChatWorkToken": token},
        data={"body": body},
        timeout=30,
    )
    if resp.status_code == 200:
        print("Chatworkへの通知を送信しました。")
    else:
        print(f"Chatwork通知に失敗しました: {resp.status_code} {resp.text}")


def main():
    topic = select_next_topic()
    if topic is None:
        print("未使用のテーマがありません。config/topics.yaml にテーマを追加してください。")
        return

    print(f"選定テーマ: [{topic['category']}] {topic['title']}")
    draft_text = generate_draft(topic)

    # 保険: AIがタイトルを本文冒頭で繰り返してしまった場合、その行を除去する
    lines = draft_text.lstrip().split("\n")
    if lines and lines[0].lstrip("#").strip() == topic["title"]:
        draft_text = "\n".join(lines[1:]).lstrip()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().strftime("%Y%m%d")
    safe_title = topic["title"][:20].replace("/", "_").replace(" ", "_")
    output_path = OUTPUT_DIR / f"{today}_{topic['id']}_{safe_title}.md"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"# {topic['title']}\n\n")
        f.write(draft_text)
        f.write(FOOTER)

    save_used_id(topic["id"])
    print(f"下書きを保存しました: {output_path}")

    notify_chatwork(topic, output_path)


if __name__ == "__main__":
    main()
