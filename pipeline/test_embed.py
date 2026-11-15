"""embed·suggest 확인(가짜 모드). 실행: python -m pipeline.test_embed"""
import os

os.environ["BEDROCK_MOCK"] = "1"  # AWS 를 부르지 않는다

from pipeline.aliases import _key
from pipeline.embed import MOCK_DIM, cosine, embed, embed_many, usage
from pipeline.suggest import suggest


def demo():
    v = embed("쿠버네티스 클러스터 운영")
    assert len(v) == MOCK_DIM and abs(sum(x * x for x in v) - 1) < 1e-9  # 길이 1
    assert embed("Docker") == embed("Docker")  # 같은 입력은 같은 벡터
    assert embed_many(["a", "b"]) == [embed("a"), embed("b")]
    assert abs(cosine(v, v) - 1) < 1e-9
    assert cosine(embed("쿠버네티스 클러스터"), embed("쿠버네티스")) > cosine(embed("쿠버네티스 클러스터"), embed("파이썬"))
    assert usage() == {"calls": 0, "tokens": 0}  # 가짜 모드는 AWS 를 부르지 않는다

    # 사전에 없는 표현에 가장 가까운 대표 이름을 후보로 낸다(합치지는 않는다)
    got = suggest("쿠버네티스 클러스터")
    assert got[0]["canonical"] == "Kubernetes" and len(got) == 3
    assert {"canonical", "score", "matched", "confidence"} <= set(got[0])
    assert got[0]["confidence"] in ("candidate", "uncertain")
    assert [g["score"] for g in got] == sorted((g["score"] for g in got), reverse=True)
    # 사전에서 뺀 이름은 후보의 근거로 쓰지 않는다
    left_out = suggest("쿠버네티스", exclude=frozenset({_key("쿠버네티스")}))
    assert all(g["matched"] != "쿠버네티스" for g in left_out)


if __name__ == "__main__":
    demo()
    print("통과")
