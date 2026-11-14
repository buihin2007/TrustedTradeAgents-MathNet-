from uuid import uuid4

from tests.factories import PROFILE


def test_answer_resumes_pipeline_with_existing_answer_and_rejects_duplicate(api):
    client, _, store, _ = api
    item = client.portal.call(store.create_request, PROFILE, "milk")
    client.portal.call(store.ask, PROFILE, item.id, "How many litres?", ["40", "50"])
    question_id = store.requests[item.id].questions[0].id
    response = client.post(
        f"/api/requests/{item.id}/answer",
        json={
            "question_id": str(question_id),
            "answer": "40 litres",
        },
    )
    assert response.status_code == 202
    response = client.post(
        f"/api/requests/{item.id}/answer",
        json={
            "question_id": str(question_id),
            "answer": "50 litres",
        },
    )
    assert response.status_code == 409
    assert store.requests[item.id].questions[0].answer == "40 litres"
    assert len([e for e in store.events if e.event_type == "user.answered"]) == 1


def test_question_from_another_request_is_rejected_without_advancing(api):
    client, _, store, gateway = api
    item = client.portal.call(store.create_request, PROFILE, "milk")
    client.portal.call(store.ask, PROFILE, item.id, "How many litres?", ["40"])
    response = client.post(
        f"/api/requests/{item.id}/answer",
        json={
            "question_id": str(uuid4()),
            "answer": "40",
        },
    )
    assert response.status_code == 409
    assert store.requests[item.id].status == "asking"
    assert gateway.calls == []
