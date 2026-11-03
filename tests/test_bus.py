"""In-process bus used by tests and by the single-process demo."""

from shared.bus import InProcessBus, topic_matches


def test_topic_wildcards() -> None:
    assert topic_matches("site/demo/reading", "site/demo/reading")
    assert topic_matches("site/+/reading", "site/demo/reading")
    assert not topic_matches("site/+/reading", "site/demo/event")
    assert topic_matches("site/#", "site/demo/reading")
    assert not topic_matches("site/demo/reading", "site/other/reading")


def test_handlers_get_a_copy() -> None:
    bus = InProcessBus()
    seen: list[dict] = []

    def handler(_topic: str, body: dict) -> None:
        seen.append(body)

    bus.subscribe("site/+/reading", handler)
    payload = {"tank_temp_c": 4.0}
    bus.publish("site/demo/reading", payload)
    payload["tank_temp_c"] = 99
    seen[0]["tank_temp_c"] = 1
    assert payload["tank_temp_c"] == 99
    bus.publish("site/demo/reading", {"tank_temp_c": 4.0})
    assert seen[0]["tank_temp_c"] == 1
    assert seen[1]["tank_temp_c"] == 4.0
