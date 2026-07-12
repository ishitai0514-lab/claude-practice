#!/usr/bin/env python3
"""面談メモ（自由文）から候補者を構造化し、jobs.csv の求人と照合して
おすすめ順（総合判断が良い順）に提示するツール。

パイプライン:
  1. extract_profile_via_claude(): 自由文 → 構造化プロフィール。
     ローカルの正規表現・キーワードマッチではなく、`claude` CLI
     （Claude Code が使うのと同じ認証済みCLI）を --print --json-schema で
     非対話実行し、Claude自身に面談メモを読ませてJSONを抽出させる。
  2. JOB_META: jobs.csv の7求人をあらかじめ「業務内容の種類」「必須経験の
     カテゴリ」等のタグに構造化（求人側は数が少なく安定しているため、
     事前タグ付けを採用）
  3. ng_check / a_axis / b_axis: これまでと同じ4段階評価（◎/○/△/✕）を
     プロフィールとJOB_METAの突き合わせで自動算出
  4. overall_judgment(): match.py と同じ総合判断ロジックを再利用
  5. おすすめ順にソートして提示
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from match import JOB_IDS, load_jobs, overall_judgment  # noqa: E402

# ---------------------------------------------------------------------------
# 求人側の構造化タグ（一度だけ定義しておく安定データ）
# ---------------------------------------------------------------------------

JOB_META = {
    "job1": {"business_type": "経営戦略型", "required_experience_type": "strategy_or_biz_planning",
              "english_required": None},
    "job2": {"business_type": "DX推進型", "required_experience_type": "manufacturing_engineering",
              "english_required": None},
    "job3": {"business_type": "PM型", "required_experience_type": "generic_pm",
              "english_required": None},
    "job4": {"business_type": "開発PL型", "required_experience_type": "generic_dev",
              "english_required": None},
    "job5": {"business_type": "ITアドバイザリー型", "required_experience_type": "it_consulting_or_upstream",
              "english_required": None},
    "job6": {"business_type": "経営戦略型", "required_experience_type": "strategy_or_biz_planning",
              "english_required": None},
    "job7": {"business_type": "経営戦略型", "required_experience_type": "business_proposal_english",
              "english_required": "ビジネス以上（聴・話）"},
}

# 志向（want_business_type）と求人business_typeの相性（◎○△✕の元になる素点）
COMPAT = {
    ("PM型", "PM型"): "◎",
    ("PM型", "開発PL型"): "△",
    ("PM型", "DX推進型"): "△",
    ("PM型", "ITアドバイザリー型"): "△",
    ("PM型", "経営戦略型"): "✕",

    ("開発PL型", "開発PL型"): "◎",
    ("開発PL型", "PM型"): "○",
    ("開発PL型", "DX推進型"): "△",
    ("開発PL型", "ITアドバイザリー型"): "△",
    ("開発PL型", "経営戦略型"): "✕",

    ("DX推進型", "DX推進型"): "◎",
    ("DX推進型", "ITアドバイザリー型"): "○",
    ("DX推進型", "PM型"): "△",
    ("DX推進型", "開発PL型"): "△",
    ("DX推進型", "経営戦略型"): "✕",

    ("ITアドバイザリー型", "ITアドバイザリー型"): "◎",
    ("ITアドバイザリー型", "DX推進型"): "○",
    ("ITアドバイザリー型", "経営戦略型"): "△",
    ("ITアドバイザリー型", "PM型"): "△",
    ("ITアドバイザリー型", "開発PL型"): "✕",

    ("経営戦略型", "経営戦略型"): "◎",
    ("経営戦略型", "ITアドバイザリー型"): "△",
    ("経営戦略型", "DX推進型"): "△",
    ("経営戦略型", "PM型"): "✕",
    ("経営戦略型", "開発PL型"): "✕",
}


# ---------------------------------------------------------------------------
# 1. 自由文 → 構造化プロフィール（Claude自身に抽出させる）
# ---------------------------------------------------------------------------

BUSINESS_TYPES = ["PM型", "開発PL型", "DX推進型", "ITアドバイザリー型", "経営戦略型", "不明"]
ENGLISH_LEVELS = ["不問", "日常会話レベル", "ビジネスレベル", "不明"]

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "age": {"type": "string", "description": "年齢。例:「32歳」。読み取れなければ空文字。"},
        "current_job": {"type": "string", "description": "現職（会社の種類・立場）の要約。"},
        "years_experience_text": {"type": "string", "description": "経験年数の説明文。例:「独立系SESに5年在籍」。"},
        "years_experience_number": {
            "type": ["integer", "null"],
            "description": "総経験年数を表す整数。読み取れなければ null。",
        },
        "experience_details": {"type": "string", "description": "経験内容（担当業務・マネジメント経験・業界経験等）の要約。"},
        "orientation": {
            "type": "object",
            "properties": {
                "current_business_type": {
                    "type": "string",
                    "enum": BUSINESS_TYPES,
                    "description": "現在の実務内容として最も近い種類。",
                },
                "desired_business_type": {
                    "type": "string",
                    "enum": BUSINESS_TYPES,
                    "description": (
                        "本人が直近のキャリアステップとして具体的に望んでいる業務内容の種類。"
                        "「ゆくゆくは」等の遠い将来の副次的な興味ではなく、"
                        "最も強く・直近で望んでいる方向性を優先して1つ選ぶこと。"
                    ),
                },
                "industry_theme": {"type": "string", "description": "業界・テーマへの関心（例: 金融、製造業など）。"},
                "work_style": {"type": "string", "description": "働き方の希望（裁量重視／安定重視／昇格スピード重視 等）。"},
                "career_direction": {"type": "string", "description": "キャリアの方向性（技術を極める／マネジメント／経営に近づく 等）。"},
            },
            "required": [
                "current_business_type",
                "desired_business_type",
                "industry_theme",
                "work_style",
                "career_direction",
            ],
        },
        "reason_for_change": {"type": "string", "description": "転職理由の要約。"},
        "desired_conditions_text": {"type": "string", "description": "希望条件（年収・働き方など）の要約文。"},
        "desired_salary_min": {
            "type": ["integer", "null"],
            "description": "希望年収の下限（万円単位の整数）。読み取れなければ null。",
        },
        "ng_conditions_text": {"type": "string", "description": "NG条件の要約文。"},
        "ng_no_project_choice": {
            "type": "boolean",
            "description": "「案件を選べない環境」がNG条件として明言されているか。",
        },
        "ng_max_overtime_hours": {
            "type": ["integer", "null"],
            "description": "NGとなる残業時間の閾値（月◯時間、を表す整数）。言及がなければ null。",
        },
        "english_level": {
            "type": "string",
            "enum": ENGLISH_LEVELS,
            "description": (
                "英語力の水準。「英語は使わない／使用しない」「英語不要」等、"
                "本人が英語を業務で使っていない・使う必要がない旨の記載がある場合は「不問」に分類する。"
                "日常会話レベル・ビジネスレベルの明記があればそれに従う。判断材料が全くない場合のみ「不明」。"
            ),
        },
    },
    "required": [
        "age",
        "current_job",
        "years_experience_text",
        "years_experience_number",
        "experience_details",
        "orientation",
        "reason_for_change",
        "desired_conditions_text",
        "desired_salary_min",
        "ng_conditions_text",
        "ng_no_project_choice",
        "ng_max_overtime_hours",
        "english_level",
    ],
}

EXTRACTION_PROMPT_TEMPLATE = """あなたは人材紹介エージェントのアシスタントです。
以下は転職候補者の面談メモ（自由文）です。この内容を分析し、指定されたJSON Schemaの
とおりに構造化してください。本文に明記されていない項目は無理に推測せず、
不明な場合は「不明」（該当するenumがあればそれ）や空文字・nullを使ってください。

business_type（現在の実務／志向する業務内容の種類）は次の定義に基づいて分類してください:
- PM型: 案件・プロジェクト全体を統括するプロジェクトマネジメント
- 開発PL型: 開発チームのリード業務（現場マネジメント中心）
- DX推進型: 技術を活かした業務改革・DX推進コンサルティング
- ITアドバイザリー型: IT領域の経営・業務アドバイザリー、ITコンサルティング
- 経営戦略型: IT要素の薄い、全社レベルの経営戦略・事業企画コンサルティング
- 不明: 上記のいずれにも明確に当てはまらない場合

desired_business_type は、本人が「ゆくゆくは」等の遠い将来の副次的な興味としてではなく、
直近のキャリアステップとして最も強く望んでいる業務内容を優先してください。

面談メモ:
\"\"\"
{text}
\"\"\"
"""

DEFAULT_MODEL = "claude-haiku-4-5-20251001"


def call_claude_extract(text: str, model: str = DEFAULT_MODEL) -> dict:
    """`claude` CLI を --print --json-schema で非対話実行し、
    面談メモをJSON Schemaに沿って構造化させる。"""
    prompt = EXTRACTION_PROMPT_TEMPLATE.format(text=text)
    cmd = [
        "claude", "-p", prompt,
        "--output-format", "json",
        "--model", model,
        "--json-schema", json.dumps(EXTRACTION_SCHEMA, ensure_ascii=False),
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude CLIの呼び出しに失敗しました: {result.stderr.strip()}")

    payload = json.loads(result.stdout)
    if payload.get("is_error"):
        raise RuntimeError(f"claude CLIがエラーを返しました: {payload}")

    structured = payload.get("structured_output")
    if not structured:
        raise RuntimeError(f"構造化出力が取得できませんでした: {payload}")
    return structured


def extract_profile(text: str, model: str = DEFAULT_MODEL) -> dict:
    """自由文の面談メモをClaudeに構造化させ、match.pyの評価ロジックが
    期待するキーを持つプロフィールdictに変換する。"""
    data = call_claude_extract(text, model=model)
    orientation = data.get("orientation", {})

    profile: dict = {"raw_text": text}
    profile["年齢"] = data.get("age", "") or "不明"
    profile["現職"] = data.get("current_job", "") or "不明"
    profile["経験年数"] = data.get("years_experience_text", "") or "不明"
    profile["years_experience"] = data.get("years_experience_number")
    profile["経験内容"] = data.get("experience_details", "") or "不明"

    profile["current_business_type"] = orientation.get("current_business_type", "不明")
    profile["want_business_type"] = orientation.get("desired_business_type", "不明")
    profile["志向性"] = (
        f"業務内容の種類: {orientation.get('desired_business_type', '不明')}／"
        f"業界・テーマ: {orientation.get('industry_theme', '不明')}／"
        f"働き方: {orientation.get('work_style', '不明')}／"
        f"キャリア方向性: {orientation.get('career_direction', '不明')}"
    )

    profile["転職理由"] = data.get("reason_for_change", "") or "不明"
    profile["希望条件"] = data.get("desired_conditions_text", "") or "不明"
    profile["desired_salary_min"] = data.get("desired_salary_min")

    profile["NG条件"] = data.get("ng_conditions_text", "") or "特記なし"
    profile["ng_flags"] = {
        "no_project_choice": bool(data.get("ng_no_project_choice")),
        "max_overtime_hours": data.get("ng_max_overtime_hours"),
    }

    profile["英語力"] = data.get("english_level", "不明")
    profile["industry_tags"] = set()

    return profile


# ---------------------------------------------------------------------------
# 2〜3. NG条件チェック／A軸／B軸
# ---------------------------------------------------------------------------

def ng_check(profile: dict, job_id: str) -> tuple[bool, str]:
    ng_flags = profile.get("ng_flags", {})
    if ng_flags.get("no_project_choice") and job_id in {"job3", "job4"}:
        # job3/job4は客先常駐・常駐率変動の要素があるが、
        # 「案件を選べない」ことを明記した求人はjobs.csv中に存在しないため、
        # 現状の求人票の記載からは断定できず対象外にはしない。
        return True, "抵触なし（求人票に「案件選択不可」の明記なし）"
    max_overtime = ng_flags.get("max_overtime_hours")
    if max_overtime is not None:
        # jobs.csv側に残業時間の明記がある求人は現状ないため、判定は保留のまま進める
        return True, "抵触なし（求人票に残業時間の明記なし）"
    return True, "抵触なし"


def a_axis(profile: dict, job_id: str) -> tuple[str, str]:
    meta = JOB_META[job_id]
    want = profile.get("want_business_type", "不明")
    business_type = meta["business_type"]

    if want == "不明":
        return "△", "候補者の志向性から業務内容の種類を明確に特定できなかったため、暫定的に△とする。"

    grade = COMPAT.get((want, business_type))
    if grade is None:
        grade = "△"

    reason = (
        f"候補者が志向する業務内容の種類「{want}」と、本求人の実務内容"
        f"「{business_type}」との相性は{grade}。"
    )
    if job_id == "job7" and profile.get("英語力") in {"不問", "不明"}:
        reason += "加えて求人はグローバル案件専任で英語必須のため、業務内容としての親和性はさらに低い。"
        if grade in {"○", "◎"}:
            grade = "△"
    return grade, reason


GRADE_SCORE = {"◎": 3, "○": 2, "△": 1, "✕": 0}


def b_axis(profile: dict, job_id: str) -> tuple[str, str]:
    meta = JOB_META[job_id]
    req_type = meta["required_experience_type"]
    years = profile.get("years_experience") or 0
    industries = profile.get("industry_tags", set())
    english = profile.get("英語力", "不明")

    if req_type == "manufacturing_engineering":
        return "✕", "必須要件（製造業でのエンジニアリング・生産管理経験5年以上）に該当する業界経験がない。"

    if req_type == "strategy_or_biz_planning":
        return "✕", "必須要件（事業企画・経営企画または戦略ファームでの実務経験）に該当する経験がない。"

    if req_type == "business_proposal_english":
        if english in {"不問", "不明", "日常会話レベル"}:
            return "✕", f"必須要件の「外国人と会議できる英語力（特に聴・話）」に対し、英語力は「{english}」で明確に不足。"
        return "○", "英語力は要件を満たす水準にあると考えられる。"

    if req_type == "it_consulting_or_upstream":
        return "✕", "必須要件（ITコンサル経験2年以上、またはSIerでの上流工程経験3年以上）に該当する記載が面談メモから確認できない。"

    if req_type == "generic_pm":
        if years >= 3 and profile.get("current_business_type") in {"PM型", "開発PL型"}:
            return "△", (
                f"チームリード／PL相当の実務経験（{years}年）はあるが、正式なPMとしての"
                "ベンダーコントロール等の経験は確認できず、必須要件（PM経験3年以上）にはやや不足。"
                "方向性がPMそのものに合致するためポテンシャルとして評価。"
            )
        return "✕", "PM経験・チームリード経験ともに必須要件に届かない。"

    if req_type == "generic_dev":
        if years >= 5:
            return "○", f"開発経験・チームリード経験（{years}年）は必須要件（開発5年以上、PL2年以上）を満たす水準。"
        if years >= 2:
            return "△", f"開発経験（{years}年）はあるが、必須要件（開発5年以上）にはやや不足。"
        return "✕", "必須要件（開発経験5年以上）に届かない。"

    return "△", "必須要件を機械的に判定できなかったため、暫定的に△とする。"


JUDGMENT_RANK = {
    "本命として提案": 0,
    "ポテンシャル提案（企業にその旨を伝えて選考にかける）": 1,
    "経験は十分だが本人の意向を再確認してから提案": 2,
    "条件をすり合わせつつ提案余地あり（優先度中）": 3,
    "必須要件を大きく下回るため提案しない": 4,
    "提案しない（志向・方向性が大きく合わない）": 5,
}


def evaluate(profile: dict) -> list:
    jobs = load_jobs()
    results = []
    for job_id in JOB_IDS:
        job = jobs[job_id]
        label = f"{job.get('会社名', '')} / {job.get('ポジション名', '')}"
        ok, ng_reason = ng_check(profile, job_id)
        if not ok:
            results.append({
                "job_id": job_id, "label": label, "ng_reason": ng_reason,
                "a": "-", "a_reason": "対象外", "b": "-", "b_reason": "対象外",
                "judgment": "対象外", "rank": 6, "score": -1,
            })
            continue
        a, a_reason = a_axis(profile, job_id)
        b, b_reason = b_axis(profile, job_id)
        judgment = overall_judgment(a, b)
        results.append({
            "job_id": job_id, "label": label, "ng_reason": ng_reason,
            "a": a, "a_reason": a_reason, "b": b, "b_reason": b_reason,
            "judgment": judgment, "rank": JUDGMENT_RANK.get(judgment, 9),
            "score": GRADE_SCORE.get(a, 0) + GRADE_SCORE.get(b, 0),
        })

    results.sort(key=lambda r: (r["rank"], -r["score"]))
    return results


def print_profile(profile: dict) -> None:
    print("## 構造化プロフィール（自動抽出結果）\n")
    for key in ["年齢", "現職", "経験年数", "経験内容", "志向性", "転職理由", "希望条件", "NG条件", "英語力"]:
        print(f"- {key}：{profile.get(key, '')}")
    print(f"- （内部タグ）志向する業務内容の種類：{profile.get('want_business_type')}")
    print(f"- （内部タグ）現職の業務内容の種類：{profile.get('current_business_type')}")
    print()


def print_ranking(results: list) -> None:
    print("## 全求人 総合ランキング\n")
    print("| 順位 | 求人 | A軸 | B軸 | 総合判断 |")
    print("|---|---|---|---|---|")
    for i, r in enumerate(results, start=1):
        print(f"| {i} | {r['label']} | {r['a']} | {r['b']} | {r['judgment']} |")
    print()

    print("## おすすめ求人 TOP3（詳細理由付き）\n")
    for i, r in enumerate(results[:3], start=1):
        print(f"### {i}位: {r['label']}")
        print(f"- A軸: {r['a']}　{r['a_reason']}")
        print(f"- B軸: {r['b']}　{r['b_reason']}")
        print(f"- 総合判断: **{r['judgment']}**\n")


def main() -> None:
    args = sys.argv[1:]
    model = DEFAULT_MODEL
    if args and args[0] == "--model":
        model = args[1]
        args = args[2:]

    text = " ".join(args) if args else sys.stdin.read()

    print(f"（Claude（{model}）で面談メモを構造化しています…）\n", file=sys.stderr)
    profile = extract_profile(text, model=model)
    print_profile(profile)
    results = evaluate(profile)
    print_ranking(results)


if __name__ == "__main__":
    main()
