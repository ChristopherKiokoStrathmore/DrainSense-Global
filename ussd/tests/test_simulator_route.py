from callback import create_app


def test_simulator_page_and_fake_numbers_only(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "s")
    c = create_app().test_client()
    assert c.get("/simulator/").status_code == 200
    assert c.post("/ussd", data={"sessionId": "a", "phoneNumber": "+254700000001", "text": ""}).status_code == 403
    real = c.post("/simulator/ussd", data={"sessionId": "a", "phoneNumber": "+254712345678", "text": ""})
    assert real.status_code == 400
    fake = c.post("/simulator/ussd", data={"sessionId": "b", "phoneNumber": "+254700000001",
                                           "serviceCode": "*384*55#", "text": ""})
    assert fake.get_data(as_text=True).startswith("CON")
