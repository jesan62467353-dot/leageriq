import io
import os
import zipfile
import xml.etree.ElementTree as ET
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests

app = Flask(__name__)
CORS(app)  # 브라우저 Live Server의 CORS 차단 해제

DART_API_KEY = "8afa81b7d29ef20c109dfd3daff0fdccae2b911f"
CORP_CODE_MAP = {}  # { "삼성전자": "00126380", ... }

def init_corp_codes():
    """DART 전체 고유번호(corp_code) 목록을 받아와 메모리에 캐싱"""
    global CORP_CODE_MAP
    xml_path = "CORPCODE.xml"
    
    if not os.path.exists(xml_path):
        print("[DART] 기업 고유번호 마스터 파일 다운로드 중...")
        url = f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={DART_API_KEY}"
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
                z.extractall(".")
            print("[DART] 고유번호 파일 압축 해제 완료.")
        else:
            print("[DART] 고유번호 파일 다운로드 실패:", resp.status_code)
            return

    tree = ET.parse(xml_path)
    root = tree.getroot()
    for item in root.findall("list"):
        corp_name = item.findtext("corp_name", "").strip()
        corp_code = item.findtext("corp_code", "").strip()
        stock_code = item.findtext("stock_code", "").strip()
        if stock_code:
            CORP_CODE_MAP[corp_name] = corp_code
            
    print(f"[DART] 상장기업 {len(CORP_CODE_MAP)}개사 고유번호 캐싱 완료.")

def parse_amount(val_str):
    if not val_str or val_str == "-":
        return 0
    try:
        return int(val_str.replace(",", ""))
    except ValueError:
        return 0

@app.route("/api/company", methods=["GET"])
def get_company_dart():
    name = request.args.get("name", "").strip()
    if not name:
        return jsonify({"error": "기업명을 입력해주세요."}), 400

    corp_code = CORP_CODE_MAP.get(name)
    if not corp_code:
        return jsonify({"error": f"'{name}' 기업은 DART 공시 시스템에 등록되지 않았습니다."}), 404

    # 2024년 사업보고서(11011) 우선 조회 -> 없으면 3분기보고서(11014)
    target_data = None
    target_year = ""
    
    for yr in ["2024", "2023"]:
        for reprt in ["11011", "11014"]:
            url = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"
            params = {
                "crtfc_key": DART_API_KEY,
                "corp_code": corp_code,
                "bsns_year": yr,
                "reprt_code": reprt
            }
            res = requests.get(url, params=params, timeout=10)
            data = res.json()
            if data.get("status") == "000" and data.get("list"):
                target_data = data["list"]
                target_year = yr
                break
        if target_data:
            break

    if not target_data:
        return jsonify({"error": f"'{name}'의 정기보고서 재무제표를 DART에서 가져올 수 없습니다."}), 404

    # 연결재무제표(CFS) 우선 추출
    cfs_rows = [r for r in target_data if r.get("fs_div") == "CFS"]
    dataset = cfs_rows if cfs_rows else target_data
    fs_name = "연결재무제표" if cfs_rows else "별도재무제표"

    rev_cur, rev_prev = 0, 0
    op_income = 0
    total_liab = 0
    total_equity = 0

    for row in dataset:
        acct = row.get("account_nm", "").strip()
        thstrm = parse_amount(row.get("thstrm_amount"))
        frmtrm = parse_amount(row.get("frmtrm_amount"))

        if any(keyword in acct for keyword in ["매출액", "수익(매출액)", "영업수익"]):
            if rev_cur == 0:  # 상단 대표 매출액 우선 취득
                rev_cur = thstrm
                rev_prev = frmtrm
        elif "영업이익" in acct:
            if op_income == 0:
                op_income = thstrm
        elif acct == "부채총계":
            total_liab = thstrm
        elif acct == "자본총계":
            total_equity = thstrm

    growth_rate = 0.0
    if rev_prev > 0:
        growth_rate = round(((rev_cur - rev_prev) / rev_prev) * 100, 1)

    opm = 0.0
    if rev_cur > 0:
        opm = round((op_income / rev_cur) * 100, 1)

    debt_ratio = 0.0
    if total_equity > 0:
        debt_ratio = round((total_liab / total_equity) * 100, 1)

    score = 70
    if growth_rate > 10: score += 10
    elif growth_rate < 0: score -= 10
    if opm > 10: score += 10
    elif opm < 3: score -= 10
    if debt_ratio < 100: score += 10
    elif debt_ratio > 200: score -= 15
    score = max(30, min(95, score))

    print(f"[DART 성공] {name} | 기준: {target_year}년 {fs_name} | 매출성장률: {growth_rate}% | 당기매출: {rev_cur} | 전기매출: {rev_prev}")

    return jsonify({
        "corp_name": name,
        "corp_code": corp_code,
        "period": f"{target_year}년 DART 정기보고서 ({fs_name}) 원본",
        "score": score,
        "growth": f"{'+' if growth_rate > 0 else ''}{growth_rate}%",
        "opm": f"{opm}%",
        "debt": f"{debt_ratio}%",
        "raw": {
            "rev_cur": rev_cur,
            "rev_prev": rev_prev,
            "op_income": op_income,
            "total_liab": total_liab,
            "total_equity": total_equity
        }
    })

if __name__ == "__main__":
    init_corp_codes()
    port = int(os.environ.get("PORT", 8000))
    print(f"\n[서버 실행 완료] 포트 {port}에서 API 응답 대기 중...\n")
    app.run(host="0.0.0.0", port=port, debug=False)