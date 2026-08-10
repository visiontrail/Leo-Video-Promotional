from backend.pipeline import composer, visual_plan


def test_mount_list_starts_with_content_and_has_no_title_card():
    board = {
        "content_start": 0.0,
        "outro_start": 12.0,
        "outro_duration": 5.0,
        "scenes": [
            {"id": "scene-01", "start": 0.0, "duration": 7.0},
            {"id": "scene-02", "start": 7.0, "duration": 5.0},
        ],
    }

    mounts = composer._mount_list(board)

    assert mounts[0] == {"id": "scene-01", "start": 0.0, "duration": 7.0}
    assert [mount["id"] for mount in mounts] == [
        "scene-01",
        "scene-02",
        visual_plan.OUTRO_SCENE_ID,
    ]
