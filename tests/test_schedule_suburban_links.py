from core.schedule_module import ScheduleModule, _station_slug


def test_station_slug_minsk():
    assert _station_slug("Минск") == "minsk"
    assert _station_slug("Гомель") == "gomel"


def test_suburban_rail_schedule_links():
    m = ScheduleModule()
    out = m.suburban_rail_schedule_links("Минск", "Гомель")
    assert "error" not in out
    urls = [x["url"] for x in out["links"]]
    assert any("transit.example.com/suburban" in u for u in urls)
    assert any("poezdato.net" in u for u in urls)


def test_suburban_requires_both_stations():
    m = ScheduleModule()
    assert m.suburban_rail_schedule_links("", "X").get("error")


def test_suburban_accepts_user_id_from_brain():
    m = ScheduleModule()
    out = m.suburban_rail_schedule_links(
        "Минск",
        "Гомель",
        user_id="900000001",
        language="ru",
    )
    assert "error" not in out
    assert out.get("origin") == "Минск"
