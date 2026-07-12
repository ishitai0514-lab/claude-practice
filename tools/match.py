#!/usr/bin/env python3
"""求人×候補者 マッチング判定ツール

jobs/*.txt と candidates/*.txt を読み込み、以下の流れで判定する。
  1. NG条件チェック（抵触すれば以降は評価せず「対象外」）
  2. A軸：志向性マッチ度（◎/○/△/✕）
  3. B軸：必須要件充足度（◎/○/△/✕）
  4. 総合判断（A軸×B軸の組み合わせから提案スタンスを決定）

A軸・B軸の評価内容（グレードと理由）は求人票・候補者票を読んだ上での
判断結果を ASSESSMENTS に構造化データとして保持する。総合判断はそこから
ルールベースで自動算出する。

A軸（志向性マッチ度）の評価方針:
    「現在担当する実務内容」を最優先で評価する。「将来のキャリアパス制度」
    「今後の可能性」（例:「将来的にコンサル転向支援あり」等の記載）は
    補足情報にとどめ、それを理由にA軸のランクを引き上げない。
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
JOBS_DIR = REPO_ROOT / "jobs"
CANDIDATES_DIR = REPO_ROOT / "candidates"

JOB_FILES = {f"job{i}": f"job{i}.txt" for i in range(1, 7)}
CANDIDATE_FILES = {f"candidate{i}": f"candidate{i}.txt" for i in range(1, 4)}


def parse_txt(path: Path) -> dict:
    """"キー：値" 形式の行を読み取る。1行に「／」区切りで複数項目が
    連結されている場合（例: "氏名：G様（仮）／年齢：29歳"）も分解する。"""
    data = {}
    last_key = None
    for line in path.read_text(encoding="utf-8").splitlines():
        for segment in line.split("／"):
            segment = segment.strip()
            if not segment:
                continue
            if "：" in segment:
                key, value = segment.split("：", 1)
                key, value = key.strip(), value.strip()
                data[key] = value
                last_key = key
            elif last_key is not None:
                data[last_key] += f"／{segment}"
    return data


def load_jobs() -> dict:
    return {jid: parse_txt(JOBS_DIR / fname) for jid, fname in JOB_FILES.items()}


def load_candidates() -> dict:
    return {cid: parse_txt(CANDIDATES_DIR / fname) for cid, fname in CANDIDATE_FILES.items()}


# ---------------------------------------------------------------------------
# 1. NG条件チェック
#    候補者のNG条件を構造化した上で、求人票に明記された条件と照合する。
#    求人票に該当情報の記載がない場合は「抵触なし」として評価を続行する。
# ---------------------------------------------------------------------------

CANDIDATE_NG = {
    "candidate1": {"max_overtime_hours": 80},
    "candidate2": {"domestic_only": True},
    "candidate3": {"no_project_choice": True},
}

# 求人票の記述から読み取れるNGリスク要因（明記されている場合のみ True/値を設定）
JOB_NG_FACTS = {
    "job1": {},
    "job2": {},
    "job3": {},
    "job4": {},
    "job5": {},
    "job6": {},
}


def ng_check(candidate_id: str, job_id: str) -> tuple[bool, str]:
    ng = CANDIDATE_NG.get(candidate_id, {})
    facts = JOB_NG_FACTS.get(job_id, {})

    if "max_overtime_hours" in ng:
        overtime = facts.get("overtime_hours")
        if overtime is not None and overtime > ng["max_overtime_hours"]:
            return False, f"求人の想定残業時間（月{overtime}時間）がNG条件（月{ng['max_overtime_hours']}時間超はNG）に抵触"

    if ng.get("domestic_only") and facts.get("domestic_only_firm"):
        return False, "国内案件限定のファームであり、NG条件（海外案件への関与必須）に抵触"

    if ng.get("no_project_choice") and facts.get("no_project_choice"):
        return False, "案件を選べないフルSES型の契約であり、NG条件に抵触"

    return True, "抵触なし"


# ---------------------------------------------------------------------------
# 2〜3. A軸（志向性マッチ度）／B軸（必須要件充足度）
#    求人票・候補者票を読んだ上での評価結果。グレードと理由を保持する。
# ---------------------------------------------------------------------------

ASSESSMENTS = {
    ("candidate1", "job1"): {
        "a": "△",
        "a_reason": "「経営課題解決」への接近という方向性は合うが、job1は純粋な戦略コンサル（非IT）であり、"
                    "G様が志す「ITコンサル」という業務内容の種類とはズレる。",
        "b": "✕",
        "b_reason": "必須要件（事業会社の経営企画3年以上、または戦略ファーム2年以上）に該当する経験がなく、"
                    "SIerでの開発・PL・上流工程経験のみでは充足しない。",
    },
    ("candidate1", "job2"): {
        "a": "○",
        "a_reason": "DX推進（BPX）という業務内容の種類は「技術を活かした経営課題解決」というG様の志向に近い。"
                    "ただし対象業界が製造業であり、金融ITを主戦場としてきたG様とは業界面でのギャップがある。",
        "b": "△",
        "b_reason": "必須要件の「製造業でのエンジニアリング・生産管理経験5年以上」は未経験。"
                    "歓迎要件側の「コンサル経験不問・技術領域への探究心」には合致し、方向性が近いためポテンシャルとして評価。",
    },
    ("candidate1", "job3"): {
        "a": "△",
        "a_reason": "現時点の実務内容はシステム開発PM（PMO型）であり、SIerでの上流工程の延長線上にある。"
                    "金融ドメイン・チームマネジメント経験との親和性は高いものの、G様が志向する"
                    "「経営課題解決」「ITコンサル」という業務内容の種類そのものとは異なる。"
                    "「PM→ITコンサル転向支援あり」は将来のキャリアパス制度であり、現時点の実務ではないため、"
                    "A軸のランクを押し上げる材料としては扱わない。",
        "b": "◎",
        "b_reason": "必須要件のPM経験3年以上に対し、PL2年＋上流工程2年の金融基幹システム経験は"
                    "機能的にほぼ同等。金融ドメイン経験も歓迎要件と一致。",
    },
    ("candidate1", "job4"): {
        "a": "△",
        "a_reason": "現場の開発PLとしてのマネジメント継続が中心で、G様が求める「より上流の経営課題解決」への"
                    "接近という方向性とはズレる。",
        "b": "○",
        "b_reason": "必須要件（開発5年以上、PL経験2年以上）は満たすが、歓迎のReact経験はなくAWSのみ一致。",
    },
    ("candidate1", "job5"): {
        "a": "◎",
        "a_reason": "ITアドバイザリー（DX支援）はG様の「ITコンサル」志向に直結し、"
                    "経営層と直接対話したいという転職理由にも合致する。",
        "b": "○",
        "b_reason": "必須要件「SIerでの上流工程経験3年以上」に対し実績は2年とわずかに不足するが、"
                    "要件定義〜設計のリード経験は質的に近い。英語・会計知識等の歓迎要件は不足。",
    },
    ("candidate1", "job6"): {
        "a": "△",
        "a_reason": "「経営課題解決」への接近という方向性は合うが、job6も非ITの純粋戦略コンサルであり、"
                    "G様の「ITコンサル」志向とは異なる。",
        "b": "✕",
        "b_reason": "必須要件（戦略ファーム経験1年以上、または事業企画・経営企画経験2年以上）に該当する経験がない。",
    },
}


def overall_judgment(a: str, b: str) -> str:
    if a == "✕":
        return "提案しない（志向・方向性が大きく合わない）"
    if a == "◎" and b == "◎":
        return "本命として提案"
    if a == "◎":
        return "ポテンシャル提案（企業にその旨を伝えて選考にかける）"
    if b == "◎":
        return "経験は十分だが本人の意向を再確認してから提案"
    if b == "✕":
        return "必須要件を大きく下回るため提案しない"
    return "条件をすり合わせつつ提案余地あり（優先度中）"


# ---------------------------------------------------------------------------
# レポート出力
# ---------------------------------------------------------------------------

def print_report(candidate_id: str) -> None:
    jobs = load_jobs()
    candidates = load_candidates()
    cand = candidates[candidate_id]

    print(f"# {cand.get('氏名', candidate_id)} のマッチング結果\n")
    print("| 求人 | 企業名／ポジション | NG判定 | A軸 | A軸理由 | B軸 | B軸理由 | 総合判断 |")
    print("|---|---|---|---|---|---|---|---|")

    for jid in JOB_FILES:
        job = jobs[jid]
        label = f"{job.get('企業名', '')} / {job.get('ポジション', '')}"
        ok, ng_reason = ng_check(candidate_id, jid)

        if not ok:
            print(f"| {jid} | {label} | **対象外**：{ng_reason} | - | - | - | - | - |")
            continue

        key = (candidate_id, jid)
        if key not in ASSESSMENTS:
            print(f"| {jid} | {label} | {ng_reason} | 未評価 | - | 未評価 | - | - |")
            continue

        assess = ASSESSMENTS[key]
        judgment = overall_judgment(assess["a"], assess["b"])
        print(
            f"| {jid} | {label} | {ng_reason} | {assess['a']} | {assess['a_reason']} "
            f"| {assess['b']} | {assess['b_reason']} | {judgment} |"
        )


if __name__ == "__main__":
    candidate_id = sys.argv[1] if len(sys.argv) > 1 else "candidate1"
    print_report(candidate_id)
