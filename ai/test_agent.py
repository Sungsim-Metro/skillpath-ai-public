"""상담 에이전트(#25) 확인. 실행: python -m ai.test_agent

가짜 모드(BEDROCK_MOCK=1)로 data/sample/mock_bedrock_agent.json 대본을 끝까지 돌린다. AWS 는 부르지 않는다.
converse_tools 의 실제 호출 형식(toolConfig, toolResult)은 가짜 클라이언트를 바꿔 끼워 확인한다.
"""
import copy
import io
import logging
import os

os.environ["BEDROCK_MOCK"] = "1"

import ai.bedrock as bedrock  # noqa: E402
from ai import agent  # noqa: E402
from ai.bedrock import BedrockJSONError  # noqa: E402
from ai.usage import SessionUsage, track_session  # noqa: E402

QUESTION = "Python, Docker 해봤는데 클라우드 엔지니어 되려면 뭐부터 공부해요?"
KEYS = {"answer", "tool_calls", "usage", "latency_ms", "uncertain", "uncertain_reason",
        "guard", "scope", "course_rows"}  # 뒤의 셋은 화면용(backend/agent_screen.py, 11/22)


def names(r):
    return [c["name"] for c in r["tool_calls"]]


def test_normal():
    r = agent.chat(QUESTION)
    assert set(r) == KEYS, set(r)
    assert names(r) == ["get_gap", "recommend_courses"], names(r)
    assert r["tool_calls"][1]["input"] == {"skills": ["AWS", "Linux"]}
    assert r["tool_calls"][0]["input"] == {"user_skills": ["Python", "Docker"]}
    assert all(c["ok"] for c in r["tool_calls"])
    assert "40건 중 31건" in r["answer"] and "77%" in r["answer"]
    assert r["uncertain"] is False and r["uncertain_reason"] is None
    assert set(r["usage"]) == set(bedrock.USAGE_KEYS) and isinstance(r["latency_ms"], int)


def test_ambiguous_skill_is_asked_back_not_replaced():
    r = agent.chat("파이선이랑 도카 써봤어요. 뭐 배우면 돼요?")
    assert r["tool_calls"][0]["input"] == {"user_skills": ["파이선", "도카"]}  # 모델은 표기를 고치지 않고 넘긴다
    out = agent.tool_get_gap(["파이선", "도카"])
    assert out["recognized_skills"] == [] and out["basis"] == "no_skills"  # 자동으로 Python·Docker 로 바꾸지 않는다
    assert out["unrecognized"] == [{"input": "파이선", "maybe": "Python"}, {"input": "도카", "maybe": "Docker"}]
    assert "Python" in [m["skill"] for m in out["missing_top"]]  # 그래서 보유로 치지 않는다
    assert "'파이선'은(는) 'Python'을(를) 말씀하신 건가요?" in r["answer"]
    assert "'도카'은(는) 'Docker'을(를) 말씀하신 건가요?" in r["answer"]
    assert r["uncertain"] is False
    # 후보가 없는 표기, 비슷해 보여도 다른 기술은 후보로 내지 않는다(시연에 엉뚱한 되묻기가 나오지 않게)
    assert agent.maybe_skill("점심") is None
    # JavaScript 는 이제 사전의 독립 항목이라 정확히 일치하면 Java 와 따로 나온다(2026-11-20)
    assert [agent.maybe_skill(x) for x in ("자바스크립트", "JavaScript", "쿠버", "리녹스")] == ["JavaScript", "JavaScript", "Kubernetes", "Linux"]
    assert agent.ASK_BACK_UNKNOWN.format(input="점심").startswith("'점심'은(는) 아직 사전에 없는")


def test_tool_results_are_short():
    gap = agent.tool_get_gap(["Python", "Docker", "쿠버"])
    assert set(gap) == {"of", "basis", "recognized_skills", "unrecognized", "missing_top"}
    assert gap["basis"] == "recognized_skills" and gap["recognized_skills"] == ["Python", "Docker"]
    assert len(gap["missing_top"]) == agent.TOP_GAP == 3
    assert all(set(m) == {"skill", "postings", "required_pct", "summary"} for m in gap["missing_top"])
    rec = agent.tool_recommend_courses(["Kubernetes"])
    assert set(rec) == {"of", "recommendations", "not_found"}
    # 선수지식은 뺀다. 과정마다 제목·난이도·match·검사를 통과한 reason
    for row in rec["recommendations"]:
        assert set(row) == {"skill", "demand_known", "postings", "required_pct", "summary", "courses"} and row["demand_known"]
        assert all(set(c) == {"title", "level", "match", "reason"} for c in row["courses"])


def test_recommend_courses_tool():
    # 화면 상담과 같은 get_recommendations(review=True) 를 부르고, 기술은 MAX_RECOMMEND 개까지만
    seen = []
    old = agent.api.get_recommendations

    def spy(job, skills, *, review=False, levels=None):
        seen.append((job, list(skills), review))
        return old(job, skills, review=review, levels=levels)

    agent.api.get_recommendations = spy
    try:
        out = agent.tool_recommend_courses(["AWS", "리눅스", "aws", "Kubernetes", "Terraform", "없는기술"])
    finally:
        agent.api.get_recommendations = old
    assert seen == [("cloud_engineer", ["AWS", "Linux", "Kubernetes"], True)]  # 별칭으로 맞추고 중복을 빼고 셋까지, review=True
    assert out["not_found"] == ["없는기술"]  # 수요 집계에 없는 기술은 추천하지 않는다
    gap = {m["skill"]: m for m in agent.tool_get_gap([])["missing_top"]}
    for row in out["recommendations"]:
        if row["skill"] in gap:  # 공고 수·필수 비율·summary 는 get_gap 과 같은 계산
            assert (row["postings"], row["required_pct"], row["summary"]) == tuple(gap[row["skill"]][k] for k in ("postings", "required_pct", "summary"))
        for c in row["courses"]:
            if c["match"] == "uncertain":  # uncertain 과정은 코드가 붙인 안내 문장이 reason 에 남는다(#24)
                assert "확실하지 않습니다" in c["reason"] or "입문 과정을 붙였습니다" in c["reason"], c["reason"]
    # 수요 짝 검사가 이 도구의 (공고 수, 기술 공고 수, 필수 %) 도 맞는 짝으로 본다
    facts = agent.demand_facts([out], [])
    row = out["recommendations"][0]
    assert (out["of"], row["postings"]) in facts["pairs"] and (out["of"], row["postings"], row["required_pct"]) in facts["triples"]
    assert agent.tool_recommend_courses([]) == {"of": out["of"], "recommendations": [], "not_found": []}


def test_recommend_named_skill_without_demand():
    # 사용자가 이름을 댄 기술은 수요 집계에 없어도 추천한다(수요 "정보 없음"). 이름을 대지 않았으면 not_found(11/19 결정 ⓐ)
    token = agent._MESSAGE.set("EC2랑 VPC를 배울 수 있는 AWS 강의 추천해 주세요")
    try:
        out = agent.tool_recommend_courses(["EC2", "VPC", "Terraform", "없는기술"])
    finally:
        agent._MESSAGE.reset(token)
    rows = {r["skill"]: r for r in out["recommendations"]}
    assert set(rows) == {"EC2", "VPC", "Terraform"} and out["not_found"] == ["없는기술"]
    for skill in ("EC2", "VPC"):
        r = rows[skill]
        assert r["demand_known"] is False and r["summary"] == agent.NO_DEMAND_SUMMARY and "postings" not in r
        for c in r["courses"]:  # "공고 n건 중 0건" 같은 문장은 빠지고 안내 문장이 앞에 붙는다
            assert c["reason"].startswith(agent.NO_DEMAND_REASON) and not agent._PAIR.search(c["reason"]), c["reason"]
    assert rows["Terraform"]["demand_known"] is True and "postings" in rows["Terraform"]
    facts = agent.demand_facts([out], [])  # 수요 정보가 없는 기술은 맞는 짝에 넣지 않는다
    assert all(m != 0 for _, m in facts["pairs"])
    # 메시지에 이름이 없으면 수요에 없는 기술은 추천하지 않는다
    assert agent.tool_recommend_courses(["EC2"])["not_found"] == ["EC2"]
    # chat() 이 끝나면 메시지 기억을 지운다
    agent.chat(QUESTION)
    assert agent._MESSAGE.get() == ""


def test_suggest_is_limited_per_call():
    raws = ["도카", "파이선", "쿠버", "리녹스", "테라폼즈", "도커스", "Dokcer"]  # 모두 후보가 있는 표기
    out = agent.tool_get_gap(raws)["unrecognized"]
    assert [u["input"] for u in out] == raws
    assert all(u["maybe"] for u in out[:agent.MAX_SUGGEST]) and agent.MAX_SUGGEST == 5
    assert [u["maybe"] for u in out[agent.MAX_SUGGEST:]] == [None, None]  # 6번째부터는 찾지 않는다(실제 모드 임베딩 호출 수 제한)


def test_warmup_does_nothing_in_mock_mode():
    import pipeline.embed as embed

    old = embed._titan
    embed._titan = lambda text: (_ for _ in ()).throw(AssertionError("가짜 모드에서 임베딩을 불렀습니다"))
    try:
        assert agent.warmup() == 1
    finally:
        embed._titan = old


def test_extract_numbers_public_name():
    import ai.recommend as rec

    assert rec.extract_numbers("공고 40건 중 31건, EC2") == {40.0, 31.0}
    assert rec._numbers is rec.extract_numbers  # 예전 이름도 그대로 쓸 수 있다


def test_out_of_scope():
    r = agent.chat("오늘 점심 뭐 먹을까요?")
    assert r["tool_calls"] == []
    assert "취업 준비" in r["answer"]
    assert r["uncertain"] is False  # 범위 밖 안내는 불확실로 두지 않는다


def test_made_up_number_is_retried_once():
    r = agent.chat("AWS는 공고 몇 건에 나와요?")
    assert names(r) == ["get_skill_demand"]
    assert "31건" in r["answer"] and "35" not in r["answer"] and "80%" not in r["answer"]
    assert r["uncertain"] is False


def test_made_up_number_twice_is_removed():
    r = agent.chat("Terraform은 얼마나 필요해요?")
    assert r["answer"] == "Terraform은 공고 40건 중 14건에 나옵니다.", r["answer"]  # 근거 없는 30% 문장만 지움
    assert r["uncertain"] is True and r["uncertain_reason"] == "number_check"


def test_no_tool_is_uncertain():
    r = agent.chat("클라우드 엔지니어는 무슨 일을 해요?")
    assert r["tool_calls"] == []
    assert r["uncertain"] is True and r["uncertain_reason"] == "no_tool"


def test_max_turns():
    r = agent.chat("AWS랑 Linux 계속 찾아 주세요")
    assert len(r["tool_calls"]) == agent.MAX_TURNS == 4
    assert r["answer"] == agent.MAX_TURNS_ANSWER
    assert r["uncertain_reason"] == "max_turns"


def test_json_failure_raises():
    try:
        agent.chat("형식 시험입니다")
    except BedrockJSONError:
        return
    raise AssertionError("JSON 이 두 번 모두 틀리면 오류를 그대로 내야 합니다")


def test_checks():
    allowed = {40.0, 31.0, 77.0}
    data, bad, err = agent.check_answer('{"answer": "1. AWS: 40건 중 31건\\n2. Linux", "scope": "in"}', allowed)
    assert err is None, err  # 목록 번호는 숫자 검사에서 뺀다
    data, bad, err = agent.check_answer('{"answer": "AWS 40건 중 33건", "scope": "in"}', allowed)
    assert bad == [33.0] and "33" in err
    assert agent.check_answer('{"answer": "x", "scope": "maybe"}', allowed)[2] is not None
    assert agent.check_answer("JSON 아님", allowed)[0] is None
    assert agent.strip_numbers("AWS는 40건 중 31건입니다. 작년보다 2배입니다.\n- EC2 3개", allowed) == "AWS는 40건 중 31건입니다."
    assert agent.answer_numbers("EC2와 S3") == set()  # 글자에 붙은 숫자는 이름의 일부
    # 꾸민 목록 번호도 뺀다 (11/17 실제 실행에서 숫자 1, 2 가 걸린 원인 후보)
    decorated = "**1. AWS**\n### 2. Linux\n- 3) Docker\n> **4.** Git\n__5.__ CI/CD\n  6. Kubernetes"
    assert agent.answer_numbers(decorated) == set(), agent.answer_numbers(decorated)
    assert agent.check_answer({"answer": decorated + " 40건 중 31건", "scope": "in"}, allowed)[2] is None
    # 줄 맨 앞이어도 목록 번호가 아닌 숫자는 그대로 검사한다
    assert agent.answer_numbers("12.5%가 필수") == {12.5}
    assert agent.answer_numbers("**12건** 중 3건") == {12.0, 3.0}
    assert agent.answer_numbers("31건이 요구\n2배 늘었습니다") == {31.0, 2.0}


def test_demand_pairs():
    # 11/17 실제 답: "CI/CD 4건 중 4건"(실제 12건 중 4건). 숫자 4·4 는 각각 도구 결과에 있어 숫자 검사로는 못 잡았다
    facts = {"pairs": {(12, 12), (12, 4)}, "triples": {(12, 12, 83), (12, 4, 100), (12, 4, 50)}}
    assert agent.check_pairs("CI/CD: 4건 중 4건 등장, 필수 비율 100%", facts) == ["(4, 4)"]
    assert agent.check_pairs("CI/CD: 12건 중 4건 등장", facts) == []
    assert agent.check_pairs("공고 12건 중 4건, 필수 100%", facts) == []
    assert agent.check_pairs("12건 중 4건(필수 100%)", facts) == []
    assert agent.check_pairs("12건 가운데 12건, 그중 필수 비율 83%", facts) == []
    assert agent.check_pairs("12건 중 4건(필수 70%)", facts) == ["(12, 4, 70%)"]  # 짝은 맞아도 필수 %가 틀림
    # summary 를 그대로 옮기면 통과한다
    gap = agent.tool_get_gap(["Python", "Docker"])
    facts = agent.demand_facts([gap], [])
    assert all(agent.check_pairs(row["summary"], facts) == [] for row in gap["missing_top"])
    assert gap["missing_top"][0]["summary"] == "공고 40건 중 31건, 필수 77%"
    demand = agent.tool_get_skill_demand("AWS")
    assert demand["summary"] == "공고 40건 중 31건, 필수 77%"
    assert agent.tool_get_skill_demand("없는기술")["summary"] == "수요 집계에 없는 기술"
    # 문장 지우기는 틀린 짝이 든 문장만
    kept = agent.strip_numbers("AWS는 공고 40건 중 31건입니다. Kubernetes는 17건 중 17건입니다.", {40.0, 31.0, 17.0}, facts)
    assert kept == "AWS는 공고 40건 중 31건입니다."


def test_wrong_pair_is_retried_then_removed():
    r = agent.chat("짝 시험: Python, Docker 다음은?")
    assert "공고 40건 중 17건" in r["answer"] and "17건 중 17건" not in r["answer"] and r["uncertain"] is False
    text = _agent_log("짝 시험: Python, Docker 다음은?")
    assert "재질문 pair" in text and "짝 틀림 {'1차': ['(17, 17)']}" in text and "Kubernetes" not in text
    r = agent.chat("짝 두 번: Python, Docker 다음은?")
    assert r["answer"] == "- AWS: 공고 40건 중 31건, 필수 77%", r["answer"]  # 두 번 다 틀린 Kubernetes 문장만 지움
    assert r["uncertain_reason"] == "pair_check"
    assert "짝 틀림 {'1차': ['(17, 17)'], '2차': ['(40, 17, 31%)']}" in _agent_log("짝 두 번: Python, Docker 다음은?")


def test_prompt_has_no_digits():
    # 모델이 지침의 숫자를 답에 옮기면 숫자 검사에 걸리므로, 모델이 읽는 글에는 숫자를 두지 않는다(11/17)
    import json as _json
    import re as _re

    seen = agent.prompt.SYSTEM + agent.prompt.RETRY_MESSAGE + _json.dumps(agent.prompt.TOOLS, ensure_ascii=False)
    assert not _re.search(r"\d", seen), _re.findall(r".{0,12}\d.{0,12}", seen)
    assert "get_skill_demand 를 다시 부르지 않습니다" in agent.prompt.SYSTEM


def test_history_numbers_and_format():
    history = [{"role": "user", "content": QUESTION}, {"role": "assistant", "content": "AWS는 공고 40건 중 31건이 요구합니다."}]
    assert len(agent._history_messages(history)) == 2
    r = agent.chat("클라우드 엔지니어는 무슨 일을 해요?", history)
    assert r["uncertain_reason"] == "no_tool"
    for bad in ([{"role": "assistant", "content": "x"}], [{"role": "user", "content": "x"}]):
        try:
            agent._history_messages(bad)
        except ValueError:
            continue
        raise AssertionError(f"잘못된 history 를 받았습니다: {bad}")


def test_guard_hook():
    # 입력이 막히면 도구·모델을 부르지 않고 고정 문장. 화면용으로 막힘과 검사 실패를 나눠 돌려준다
    from ai import guardrail

    old = guardrail.check_input
    try:
        for result, kind in ((guardrail.GuardResult(True, "code", "공격", "지시 무시"), "blocked"),
                             (guardrail.GuardResult(True, "guardrails", guardrail.FAILED, "RuntimeError"), "check_failed")):
            guardrail.check_input = lambda m, result=result: result
            r = agent.chat(QUESTION)
            assert r["answer"] == guardrail.message_for(result, guardrail.BLOCK_MESSAGE) and r["tool_calls"] == []
            assert (r["guard"], r["scope"], r["course_rows"]) == (kind, None, [])
    finally:
        guardrail.check_input = old


def test_screen_base_skills_are_merged_into_get_gap_by_code():
    # 화면용: 프로필·사이드바 보유 기술을 코드가 get_gap 입력에 합친다. 평가 경로(base_skills 없음)는 그대로
    plain = agent.tool_get_gap(["Python"])
    token = agent._BASE_SKILLS.set(("Docker", "Python"))
    try:
        merged = agent.tool_get_gap(["Python"])
    finally:
        agent._BASE_SKILLS.reset(token)
    assert plain["recognized_skills"] == ["Python"]
    assert merged["recognized_skills"] == ["Python", "Docker"]  # 중복 없이 뒤에 붙는다
    assert "Docker" not in [m["skill"] for m in merged["missing_top"]]
    r = agent.chat(QUESTION, base_skills=["Linux"])
    assert r["tool_calls"][0]["input"] == {"user_skills": ["Python", "Docker"]}  # 모델 입력은 그대로
    assert agent._BASE_SKILLS.get() == ()  # 끝나면 원래대로


def test_screen_course_rows_keep_card_fields():
    # 화면용: 모델에게는 4칸만 주고, 카드에 필요한 원본 칸(url 등)은 course_rows 로 따로 돌려준다. reason 은 같은 문장
    r = agent.chat(QUESTION)
    assert r["guard"] == "pass" and r["scope"] in ("in", "out")
    rows = r["course_rows"]
    assert rows and all(row["gap_skill"] and row["courses"] for row in rows)
    for row in rows:
        for course in row["courses"]:
            assert {"title", "url", "level", "match", "reason"} <= set(course)


def _agent_log(question: str) -> str:
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    log = logging.getLogger("ai.agent")
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    try:
        agent.chat(question)
    finally:
        log.removeHandler(handler)
    return buf.getvalue()


def test_log_has_no_conversation_text():
    text = _agent_log(QUESTION)
    assert "상담 1건" in text and "get_gap" in text and "ms" in text and "재질문 없음" in text
    assert "뭐부터" not in text and "Docker" not in text and "31건" not in text  # 질문·보유 기술·답은 남기지 않는다


def test_log_records_retry_reason():
    text = _agent_log("AWS는 공고 몇 건에 나와요?")
    assert "재질문 number" in text and "근거 없는 숫자 {'1차': ['35', '80']}" in text
    text = _agent_log("Terraform은 얼마나 필요해요?")
    assert "근거 없는 숫자 {'1차': ['2'], '2차': ['30']}" in text  # 걸린 숫자만 남긴다
    assert "늘었습니다" not in text and "Terraform" not in text  # 지운 문장과 원문은 남기지 않는다
    assert "근거 없는 숫자 없음" in _agent_log(QUESTION)


class FakeClient:
    """Converse 를 흉내 낸다. 첫 호출은 도구 두 개(하나는 실패), 둘째 호출은 글."""

    def __init__(self):
        self.calls = []

    def converse(self, **kw):
        self.calls.append(copy.deepcopy(kw))  # boto3 처럼 부른 순간의 내용을 남긴다
        usage = {"inputTokens": 100, "outputTokens": 10, "totalTokens": 110}
        if len(self.calls) == 1:
            content = [{"toolUse": {"toolUseId": "a1", "name": "get_skill_demand", "input": {"skill": "AWS"}}},
                       {"toolUse": {"toolUseId": "a2", "name": "broken", "input": {}}}]
            return {"output": {"message": {"role": "assistant", "content": content}}, "stopReason": "tool_use", "usage": usage}
        return {"output": {"message": {"role": "assistant", "content": [{"text": "끝"}]}}, "stopReason": "end_turn", "usage": usage}


def test_converse_tools_real_request_shape():
    fake = FakeClient()
    old_client, old_env = bedrock._client, dict(os.environ)
    bedrock._client = lambda: fake
    os.environ.update({"BEDROCK_MOCK": "0", "BEDROCK_MODEL_ID": "test-model", "USAGE_LOG": "off"})

    def run_tool(name, inp):
        if name == "broken":
            raise RuntimeError("일부러 실패")
        return {"skill": inp["skill"], "postings": 31}

    try:
        session = SessionUsage()
        with track_session(session):
            res = bedrock.converse_tools("시스템", [{"role": "user", "content": [{"text": "질문"}]}],
                                         agent.prompt.TOOLS, run_tool, mock_file=agent.MOCK_FILE, mock_key="x")
        assert (session.input_tokens, session.output_tokens) == (200, 20)
    finally:
        bedrock._client = old_client
        os.environ.clear()
        os.environ.update(old_env)
    first = fake.calls[0]
    assert first["modelId"] == "test-model"
    assert [t["toolSpec"]["name"] for t in first["toolConfig"]["tools"]] == ["get_gap", "get_skill_demand", "recommend_courses", "answer"]
    results = fake.calls[1]["messages"][-1]
    assert results["role"] == "user"
    tr = [b["toolResult"] for b in results["content"]]
    assert tr[0] == {"toolUseId": "a1", "content": [{"json": {"skill": "AWS", "postings": 31}}], "status": "success"}
    assert tr[1]["toolUseId"] == "a2" and tr[1]["status"] == "error"
    assert res.text == "끝" and res.stop == "end_turn" and res.model_calls == 2
    assert res.usage == {"inputTokens": 200, "outputTokens": 20, "totalTokens": 220}
    assert [c["ok"] for c in res.tool_calls] == [True, False]


class FakeFinalClient:
    """첫 호출: 수요 도구와 answer 를 같은 차례에 부름(너무 이른 답). 둘째 호출: answer 만."""

    def __init__(self):
        self.calls = []

    def converse(self, **kw):
        self.calls.append(copy.deepcopy(kw))
        usage = {"inputTokens": 50, "outputTokens": 5, "totalTokens": 55}
        if len(self.calls) == 1:
            content = [{"toolUse": {"toolUseId": "d1", "name": "get_skill_demand", "input": {"skill": "AWS"}}},
                       {"toolUse": {"toolUseId": "f1", "name": "answer", "input": {"answer": "이른 답", "scope": "in"}}}]
        else:
            content = [{"text": "정리합니다."},
                       {"toolUse": {"toolUseId": "f2", "name": "answer", "input": {"answer": "최종 답", "scope": "in"}}}]
        return {"output": {"message": {"role": "assistant", "content": content}}, "stopReason": "tool_use", "usage": usage}


def test_converse_tools_final_tool():
    fake = FakeFinalClient()
    old_client, old_env = bedrock._client, dict(os.environ)
    bedrock._client = lambda: fake
    os.environ.update({"BEDROCK_MOCK": "0", "BEDROCK_MODEL_ID": "test-model", "USAGE_LOG": "off"})
    ran = []
    try:
        res = bedrock.converse_tools("시스템", [{"role": "user", "content": [{"text": "질문"}]}], agent.prompt.TOOLS,
                                     lambda n, i: ran.append(n) or {"postings": 31}, mock_file=agent.MOCK_FILE,
                                     mock_key="x", final_tool="answer")
    finally:
        bedrock._client = old_client
        os.environ.clear()
        os.environ.update(old_env)
    assert ran == ["get_skill_demand"]  # answer 는 실행하지 않는다
    second = {b["toolResult"]["toolUseId"]: b["toolResult"] for b in fake.calls[1]["messages"][-1]["content"]}
    assert second["d1"]["status"] == "success"
    assert second["f1"]["status"] == "error" and second["f1"]["content"][0]["text"] == bedrock.FINAL_TOO_EARLY
    assert res.stop == "final_tool" and res.final == {"id": "f2", "input": {"answer": "최종 답", "scope": "in"}}
    assert res.model_calls == 2 and [c["name"] for c in res.tool_calls] == ["get_skill_demand"]


def test_answer_tool_flow():
    text = _agent_log(QUESTION)
    assert "답 answer도구" in text and "재질문 없음" in text and "모델 3회" in text
    # 다른 도구와 같은 차례에 부른 answer(35건)는 받지 않고, 결과를 본 뒤의 answer(31건)를 쓴다
    r = agent.chat("AWS 수요랑 답을 같이 불러 주세요")
    assert names(r) == ["get_skill_demand"] and "31건" in r["answer"] and "35" not in r["answer"]
    assert r["uncertain"] is False
    assert "재질문 없음" in _agent_log("AWS 수요랑 답을 같이 불러 주세요")  # 너무 이른 답은 재질문으로 세지 않는다
    # 안전장치: answer 도구 없이 글(JSON)로 답해도 받는다
    text = _agent_log("AWS는 글로만 답해 주세요")
    assert "답 글" in text and "재질문 없음" in text


class FakeDoubleAnswerClient:
    """첫 호출: answer 를 한 차례에 두 번. 둘째 호출: answer 하나."""

    def __init__(self):
        self.calls = []

    def converse(self, **kw):
        self.calls.append(copy.deepcopy(kw))
        usage = {"inputTokens": 10, "outputTokens": 1, "totalTokens": 11}
        if len(self.calls) == 1:
            content = [{"toolUse": {"toolUseId": "a1", "name": "answer", "input": {"answer": "하나", "scope": "in"}}},
                       {"toolUse": {"toolUseId": "a2", "name": "answer", "input": {"answer": "둘", "scope": "in"}}}]
        else:
            content = [{"toolUse": {"toolUseId": "a3", "name": "answer", "input": {"answer": "최종", "scope": "in"}}}]
        return {"output": {"message": {"role": "assistant", "content": content}}, "stopReason": "tool_use", "usage": usage}


def test_answer_called_twice_in_one_turn():
    fake = FakeDoubleAnswerClient()
    old_client, old_env = bedrock._client, dict(os.environ)
    bedrock._client = lambda: fake
    os.environ.update({"BEDROCK_MOCK": "0", "BEDROCK_MODEL_ID": "test-model", "USAGE_LOG": "off"})
    try:
        res = bedrock.converse_tools("시스템", [{"role": "user", "content": [{"text": "질문"}]}], agent.prompt.TOOLS,
                                     lambda n, i: {}, mock_file=agent.MOCK_FILE, mock_key="x", final_tool="answer")
    finally:
        bedrock._client = old_client
        os.environ.clear()
        os.environ.update(old_env)
    results = [b["toolResult"] for b in fake.calls[1]["messages"][-1]["content"]]
    assert [r["toolUseId"] for r in results] == ["a1", "a2"]  # toolUse 마다 toolResult 하나씩
    assert all(r["status"] == "error" and r["content"][0]["text"] == bedrock.FINAL_TWICE for r in results)
    assert res.stop == "final_tool" and res.final == {"id": "a3", "input": {"answer": "최종", "scope": "in"}}
    # chat 에서도 두 답은 받지 않고 다음 차례의 답을 쓴다(재질문으로 세지 않음)
    r = agent.chat("답 두 개를 한꺼번에 주세요")
    assert r["answer"] == "- AWS: 공고 40건 중 31건, 필수 77%" and names(r) == ["get_gap"]
    assert "재질문 없음" in _agent_log("답 두 개를 한꺼번에 주세요")


def test_retry_turn_answers_every_tool_use():
    # 마지막 assistant 메시지에 toolUse 가 여럿이면 하나하나에 toolResult 를 돌려준다(Converse 가 거부하지 않게)
    msgs = [{"role": "assistant", "content": [
        {"text": "정리합니다."},
        {"toolUse": {"toolUseId": "f9", "name": "answer", "input": {}}},
        {"toolUse": {"toolUseId": "x1", "name": "get_gap", "input": {}}},
    ]}]
    loop = bedrock.ToolLoopResult("", msgs, (), 2, {}, "final_tool", {"id": "f9", "input": {}})
    results = [b["toolResult"] for b in agent._retry_turn(loop, "숫자 오류")["content"]]
    assert [r["toolUseId"] for r in results] == ["f9", "x1"] and all(r["status"] == "error" for r in results)
    assert "answer 도구로" in results[0]["content"][0]["text"] and results[1]["content"][0]["text"] == agent.SKIPPED_TOOL


def test_required_pct_same_rounding():
    gap = {m["skill"]: m for m in agent.tool_get_gap(["Python"])["missing_top"]}
    for skill, row in gap.items():
        assert row["required_pct"] == agent.tool_get_skill_demand(skill)["required_pct"], skill


def test_retry_turn_shape():
    loop = bedrock.ToolLoopResult("", [], (), 2, {}, "final_tool", {"id": "f9", "input": {}})
    turn = agent._retry_turn(loop, "숫자 오류")
    tr = turn["content"][0]["toolResult"]
    assert turn["role"] == "user" and tr["toolUseId"] == "f9" and tr["status"] == "error"  # answer 호출에 대한 결과로 돌려준다
    assert "answer 도구로" in tr["content"][0]["text"]
    text_loop = bedrock.ToolLoopResult("글", [], (), 2, {}, "end_turn")
    assert agent._retry_turn(text_loop, "형식 오류")["content"][0]["text"].startswith("직전 답이")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("통과")
