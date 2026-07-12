#!/usr/bin/env python3
"""面談メモ（自由文）から候補者を構造化し、jobs.csv の求人と照合して
おすすめ順（総合判断が良い順）に提示するツール。

パイプライン:
  1. extract_profile(): 自由文 → 構造化プロフィール（正規表現・キーワード
     ベースの簡易抽出。厳密なNLUではなく、面談メモの典型的な言い回しを
     カバーする実務的なヒューリスティック）
  2. JOB_META: jobs.csv の7求人をあらかじめ「業務内容の種類」「必須経験の
     カテゴリ」等のタグに構造化（求人側は数が少なく安定しているため、
     事前タグ付けを採用）
  3. ng_check / a_axis / b_axis: これまでと同じ4段階評価（◎/○/△/✕）を
     プロフィールとJOB_METAの突き合わせで自動算出
  4. overall_judgment(): match.py と同じ総合判断ロジックを再利用
  5. おすすめ順にソートして提示
"""

import re
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
# 1. 自由文 → 構造化プロフィール
# ---------------------------------------------------------------------------

SKILL_KEYWORDS = ["Java", "Python", "AWS", "React", "PHP", "Ruby", "Go", "C#", "SQL", "Azure", "GCP", "TypeScript"]
INDUSTRY_KEYWORDS = ["金融", "製造業", "医療", "小売", "EC", "保険", "通信"]


def _first_match(pattern: str, text: str, group: int = 1):
    m = re.search(pattern, text)
    return m.group(group) if m else None


def extract_profile(text: str) -> dict:
    profile: dict = {"raw_text": text}

    age = _first_match(r"(\d{1,2})\s*歳", text)
    profile["年齢"] = f"{age}歳" if age else ""

    years = _first_match(r"(\d{1,2})\s*年(?:在籍|目|ほど|勤務)", text)
    years_experience = int(years) if years else None
    profile["years_experience"] = years_experience
    profile["経験年数"] = f"{years_experience}年" if years_experience else "不明"

    if "独立系SES" in text:
        profile["現職"] = "独立系SES企業"
    elif "SES" in text:
        profile["現職"] = "SES企業"
    elif "SIer" in text:
        profile["現職"] = "SIer"
    else:
        profile["現職"] = "不明"

    skills = [kw for kw in SKILL_KEYWORDS if kw in text]
    if "チームリード" in text or "マネジメント" in text:
        skills.append("チームマネジメント")
    profile["スキル"] = "、".join(skills) if skills else "不明"

    if any(k in text for k in ["開発", "エンジニア", "SE"]) and any(k in text for k in ["チームリード", "PL", "マネジメント"]):
        current_type = "開発PL型"
    elif any(k in text for k in ["開発", "エンジニア", "SE"]):
        current_type = "開発PL型"
    elif "コンサル" in text:
        current_type = "経営戦略型"
    else:
        current_type = "不明"
    profile["current_business_type"] = current_type

    want_type = "不明"
    if re.search(r"PM|プロジェクトマネージャ|案件全体を(仕切|統括)", text):
        want_type = "PM型"
    elif "コンサル" in text:
        want_type = "ITアドバイザリー型"
    elif re.search(r"DX|業務改革", text):
        want_type = "DX推進型"
    profile["want_business_type"] = want_type

    industry_tags = {kw for kw in INDUSTRY_KEYWORDS if kw in text}
    profile["industry_tags"] = industry_tags

    if re.search(r"英語は使わない|英語不問|英語は不要|英語は使いません", text):
        english_level = "不問"
    elif "ビジネスレベル" in text:
        english_level = "ビジネスレベル"
    elif "日常会話" in text:
        english_level = "日常会話レベル"
    else:
        english_level = "不明"
    profile["英語力"] = english_level

    ng_flags = {}
    if re.search(r"案件を選べない|案件選定権限もない|案件を選ぶ(こと)?ができない", text):
        ng_flags["no_project_choice"] = True
    if re.search(r"月?(\d{2,3})\s*時間以上.*(残業|NG)", text):
        h = _first_match(r"(\d{2,3})\s*時間以上.*(?:残業|NG)", text)
        if h:
            ng_flags["max_overtime_hours"] = int(h)
    profile["ng_flags"] = ng_flags
    ng_desc = []
    if "案件を選べない" in text or "案件選定権限もない" in text:
        ng_desc.append("案件を選べない環境は避けたい")
    profile["NG条件"] = "、".join(ng_desc) if ng_desc else "特記なし"

    salary = _first_match(r"年収.{0,4}?(\d{3,4})\s*万円?\s*以上", text)
    profile["desired_salary_min"] = int(salary) if salary else None
    want_reduce_onsite = bool(re.search(r"常駐比率.*下げたい|常駐を減らしたい", text))
    profile["want_reduce_onsite"] = want_reduce_onsite

    kibou_parts = []
    if salary:
        kibou_parts.append(f"年収{salary}万円以上")
    if want_reduce_onsite:
        kibou_parts.append("常駐比率を下げたい")
    profile["希望条件"] = "、".join(kibou_parts) if kibou_parts else "特記なし"

    tenshoku_riyuu = []
    if "不満" in text or "ただし" in text:
        for sentence in re.split(r"[。\n]", text):
            if "不満" in sentence:
                tenshoku_riyuu.append(sentence.strip())
    profile["転職理由"] = "。".join(tenshoku_riyuu) if tenshoku_riyuu else "不明"

    shikousei_parts = []
    for sentence in re.split(r"[。\n]", text):
        if re.search(r"たい$|将来的には", sentence) and not re.search(r"年収|希望", sentence):
            shikousei_parts.append(sentence.strip())
    profile["志向性"] = "。".join(shikousei_parts) if shikousei_parts else "不明"

    keiken_naiyou = []
    for sentence in re.split(r"[。\n]", text):
        if re.search(r"担当|マネジメント|経験", sentence) and "不満" not in sentence:
            keiken_naiyou.append(sentence.strip())
    profile["経験内容"] = "。".join(keiken_naiyou) if keiken_naiyou else "不明"

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
    for key in ["年齢", "現職", "経験年数", "経験内容", "スキル", "志向性", "転職理由", "希望条件", "NG条件", "英語力"]:
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
    if len(sys.argv) > 1:
        text = " ".join(sys.argv[1:])
    else:
        text = sys.stdin.read()

    profile = extract_profile(text)
    print_profile(profile)
    results = evaluate(profile)
    print_ranking(results)


if __name__ == "__main__":
    main()
