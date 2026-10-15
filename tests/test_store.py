import json

from webapptester.store import RunStore


def test_observations_have_unique_ids_and_append_only_metadata(tmp_path):
    store = RunStore(tmp_path)

    first = store.save_observation("https://example.test/one", "第一", b"png-one")
    first_line = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()[0]
    second = store.save_observation("https://example.test/two", "第二", b"png-two")

    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert first.id != second.id
    assert first.screenshot != second.screenshot
    assert (tmp_path / "screenshots" / "observation-0001.png").read_bytes() == b"png-one"
    assert (tmp_path / "screenshots" / "observation-0002.png").read_bytes() == b"png-two"
    assert lines[0] == first_line
    assert len(lines) == 2

    second_event = json.loads(lines[1])
    assert second_event["url"] == second.url
    assert second_event["text"] == second.text
    assert second_event["fingerprint"] == second.fingerprint


def test_action_values_are_not_persisted(tmp_path):
    store = RunStore(tmp_path)

    store.append_event(
        {
            "type": "action",
            "action": {"kind": "fill", "locator": "#password", "value": "secret"},
        }
    )
    store.append_event({"kind": "fill", "locator": "#password", "value": "secret-2"})

    contents = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert "secret" not in contents
    assert "value" not in contents


def test_report_is_written_as_json(tmp_path):
    report_path = RunStore(tmp_path).write_report({"status": "UNKNOWN", "message": "未知"})

    assert report_path == tmp_path / "report.json"
    assert json.loads(report_path.read_text(encoding="utf-8")) == {
        "status": "UNKNOWN",
        "message": "未知",
    }
