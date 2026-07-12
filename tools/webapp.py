#!/usr/bin/env python3
"""interview_match.py の機能をブラウザから使える簡易フォームにするWebアプリ。

トップページのテキストエリアに面談メモを貼り付けて「診断する」を押すと、
interview_match.extract_profile() → evaluate() を実行し、
おすすめ求人TOP3をカード形式で表示する。
"""

from pathlib import Path

from flask import Flask, render_template_string, request

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from interview_match import DEFAULT_MODEL, evaluate, extract_profile  # noqa: E402

app = Flask(__name__)

BASE_STYLE = """
<style>
  * { box-sizing: border-box; }
  body {
    font-family: "Hiragino Sans", "Yu Gothic", "Segoe UI", sans-serif;
    background: #f4f6f8;
    color: #1f2937;
    margin: 0;
    padding: 32px 16px 64px;
  }
  .container { max-width: 880px; margin: 0 auto; }
  h1 { font-size: 22px; margin-bottom: 4px; }
  p.subtitle { color: #6b7280; margin-top: 0; margin-bottom: 24px; font-size: 14px; }
  form {
    background: #fff;
    border-radius: 12px;
    padding: 20px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    margin-bottom: 28px;
  }
  textarea {
    width: 100%;
    min-height: 160px;
    padding: 12px;
    border: 1px solid #d1d5db;
    border-radius: 8px;
    font-size: 14px;
    line-height: 1.6;
    resize: vertical;
  }
  button {
    margin-top: 12px;
    background: #2563eb;
    color: #fff;
    border: none;
    padding: 10px 24px;
    border-radius: 8px;
    font-size: 15px;
    font-weight: 600;
    cursor: pointer;
  }
  button:hover { background: #1d4ed8; }
  button:disabled { background: #9ca3af; cursor: not-allowed; }

  .profile-box {
    background: #fff;
    border-radius: 12px;
    padding: 20px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    margin-bottom: 24px;
    font-size: 13px;
    line-height: 1.8;
  }
  .profile-box h2 { font-size: 15px; margin-top: 0; }
  .profile-box dl { display: grid; grid-template-columns: 100px 1fr; gap: 4px 12px; margin: 0; }
  .profile-box dt { color: #6b7280; }
  .profile-box dd { margin: 0; }

  .rank-label {
    display: inline-block;
    background: #2563eb;
    color: #fff;
    font-weight: 700;
    font-size: 13px;
    border-radius: 999px;
    padding: 3px 12px;
    margin-bottom: 8px;
  }
  .card-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    gap: 16px;
  }
  .card {
    background: #fff;
    border-radius: 12px;
    padding: 18px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    border-top: 4px solid #2563eb;
  }
  .card h3 { font-size: 15px; margin: 4px 0 12px; }
  .axis-row { display: flex; align-items: baseline; gap: 8px; margin-bottom: 4px; }
  .axis-badge {
    display: inline-flex; align-items: center; justify-content: center;
    width: 26px; height: 26px; border-radius: 50%;
    font-weight: 700; font-size: 14px; flex-shrink: 0;
    background: #eef2ff; color: #1e3a8a;
  }
  .axis-label { font-weight: 600; font-size: 13px; color: #374151; }
  .axis-reason { font-size: 12.5px; color: #4b5563; margin: 2px 0 10px 34px; line-height: 1.6; }
  .judgment {
    margin-top: 10px;
    padding: 8px 10px;
    background: #ecfdf5;
    color: #065f46;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 700;
  }
  .error-box {
    background: #fef2f2;
    color: #991b1b;
    border: 1px solid #fecaca;
    padding: 16px;
    border-radius: 8px;
    margin-bottom: 24px;
    white-space: pre-wrap;
    font-size: 13px;
  }
  .full-ranking { margin-top: 32px; }
  table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
  th, td { padding: 8px 10px; font-size: 12.5px; text-align: left; border-bottom: 1px solid #f0f0f0; }
  th { background: #f9fafb; color: #6b7280; font-weight: 600; }
</style>
"""

PAGE_TEMPLATE = """
<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>求人マッチング診断ツール</title>
""" + BASE_STYLE + """
</head>
<body>
<div class="container">
  <h1>求人マッチング診断ツール</h1>
  <p class="subtitle">面談メモを貼り付けて「診断する」を押すと、Claudeが構造化し、7求人と自動照合しておすすめ順に表示します。</p>

  <form method="post" action="/diagnose">
    <textarea name="memo" placeholder="面談メモを貼り付けてください（例: 32歳、独立系SESに5年在籍。直近はWebアプリのバックエンド開発(Java)を担当し…）">{{ memo or '' }}</textarea><br>
    <button type="submit">診断する</button>
  </form>

  {% if error %}
  <div class="error-box"><strong>エラーが発生しました：</strong>\n{{ error }}</div>
  {% endif %}

  {% if profile %}
  <div class="profile-box">
    <h2>構造化プロフィール（自動抽出結果）</h2>
    <dl>
      <dt>年齢</dt><dd>{{ profile['年齢'] }}</dd>
      <dt>現職</dt><dd>{{ profile['現職'] }}</dd>
      <dt>経験年数</dt><dd>{{ profile['経験年数'] }}</dd>
      <dt>経験内容</dt><dd>{{ profile['経験内容'] }}</dd>
      <dt>志向性</dt><dd>{{ profile['志向性'] }}</dd>
      <dt>転職理由</dt><dd>{{ profile['転職理由'] }}</dd>
      <dt>希望条件</dt><dd>{{ profile['希望条件'] }}</dd>
      <dt>NG条件</dt><dd>{{ profile['NG条件'] }}</dd>
      <dt>英語力</dt><dd>{{ profile['英語力'] }}</dd>
    </dl>
  </div>
  {% endif %}

  {% if results %}
  <h2 style="font-size:16px;">おすすめ求人 TOP3</h2>
  <div class="card-grid">
    {% for r in results[:3] %}
    <div class="card">
      <span class="rank-label">{{ loop.index }}位</span>
      <h3>{{ r.label }}</h3>

      <div class="axis-row"><span class="axis-badge">{{ r.a }}</span><span class="axis-label">A軸（志向性マッチ度）</span></div>
      <div class="axis-reason">{{ r.a_reason }}</div>

      <div class="axis-row"><span class="axis-badge">{{ r.b }}</span><span class="axis-label">B軸（必須要件充足度）</span></div>
      <div class="axis-reason">{{ r.b_reason }}</div>

      <div class="judgment">{{ r.judgment }}</div>
    </div>
    {% endfor %}
  </div>

  <div class="full-ranking">
    <h2 style="font-size:16px;">全求人ランキング</h2>
    <table>
      <tr><th>順位</th><th>求人</th><th>A軸</th><th>B軸</th><th>総合判断</th></tr>
      {% for r in results %}
      <tr>
        <td>{{ loop.index }}</td>
        <td>{{ r.label }}</td>
        <td>{{ r.a }}</td>
        <td>{{ r.b }}</td>
        <td>{{ r.judgment }}</td>
      </tr>
      {% endfor %}
    </table>
  </div>
  {% endif %}
</div>
</body>
</html>
"""


@app.get("/")
def index():
    return render_template_string(PAGE_TEMPLATE)


@app.post("/diagnose")
def diagnose():
    memo = request.form.get("memo", "").strip()
    if not memo:
        return render_template_string(PAGE_TEMPLATE, error="面談メモを入力してください。", memo=memo)

    try:
        profile = extract_profile(memo, model=DEFAULT_MODEL)
        results = evaluate(profile)
    except Exception as exc:  # noqa: BLE001
        return render_template_string(PAGE_TEMPLATE, error=str(exc), memo=memo)

    return render_template_string(PAGE_TEMPLATE, memo=memo, profile=profile, results=results)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5055, debug=False)
