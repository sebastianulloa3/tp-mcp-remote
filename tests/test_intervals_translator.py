import asyncio
import pytest
from intervals_translator import (Unsupported, translate_structure,
                                  translate_workouts, format_length)
from wolfpack_migration import register_migration_tools


def swim_structure():
    """Forma real de una sesión de natación de TrainingPeaks (librería WOLFPACK SWIM)."""
    return {
        "primaryIntensityMetric": "percentOfThresholdPace",
        "primaryLengthMetric": "distance",
        "structure": [
            {"type": "step", "length": {"value": 1, "unit": "repetition"},
             "steps": [{"name": "Calentamiento", "length": {"value": 600, "unit": "meter"},
                        "targets": [{"minValue": 75, "maxValue": 75}],
                        "intensityClass": "warmUp", "openDuration": False}],
             "begin": 0, "end": 600},
            {"type": "repetition", "length": {"value": 10, "unit": "repetition"},
             "steps": [
                 {"type": "step", "name": "Difícil", "length": {"unit": "meter", "value": 200},
                  "targets": [{"minValue": 85, "maxValue": 85}], "intensityClass": "active",
                  "openDuration": False},
                 {"type": "step", "name": "Fácil", "length": {"unit": "second", "value": 20},
                  "targets": [{"minValue": 75, "maxValue": 85}], "intensityClass": "rest",
                  "openDuration": False}],
             "begin": 600, "end": 2790},
            {"type": "step", "length": {"value": 1, "unit": "repetition"},
             "steps": [{"name": "Enfriar", "length": {"value": 400, "unit": "meter"},
                        "targets": [{"minValue": 0, "maxValue": 77}],
                        "intensityClass": "coolDown", "openDuration": False}],
             "begin": 2790, "end": 3190},
        ],
    }


def test_swim_structure_translates_to_intervals_text():
    text = translate_structure(swim_structure())
    assert text == (
        "- 600mtr 75% Pace Calentamiento\n\n"
        "10x\n- 200mtr 85% Pace\n- 20s 75-85% Pace intensity=rest\n\n"
        "- 400mtr 0-77% Pace Enfriar"
    )


def test_meters_never_use_m_which_means_minutes_in_intervals():
    assert format_length({"value": 400, "unit": "meter"}) == "400mtr"
    assert format_length({"value": 120, "unit": "second"}) == "2m"
    assert format_length({"value": 90, "unit": "second"}) == "90s"


def test_rest_without_real_target_has_no_target():
    s = swim_structure()
    rest = s["structure"][1]["steps"][1]
    rest["targets"] = [{"minValue": 0, "maxValue": 77}]
    assert "- 20s intensity=rest" in translate_structure(s)


def test_unknown_metric_is_reported_not_guessed():
    s = swim_structure()
    s["primaryIntensityMetric"] = "rpe"
    with pytest.raises(Unsupported):
        translate_structure(s)


def test_translate_workouts_is_dry_run_and_flags_duplicate_titles():
    workouts = [
        {"source_id": 1, "title": "A", "sport_id": 1, "structure": swim_structure()},
        {"source_id": 2, "title": "A", "sport_id": 1, "structure": None},
        {"source_id": 3, "title": "B", "sport_id": 3, "structure": None},
    ]
    result = translate_workouts(workouts)
    assert result["dry_run"] is True
    assert result["summary"] == {"traducido": 1, "solo_texto": 2}
    assert [i["duplicate_title"] for i in result["items"]] == [True, True, False]
    assert result["items"][0]["structure_total"] == 3190
    assert all(i["write_performed"] is False for i in result["items"])


def test_mcp_tool_only_reads_trainingpeaks():
    calls = []

    async def tp_get(url):
        calls.append(url)
        return [{"exerciseLibraryItemId": 9, "itemName": "Swim", "workoutTypeId": 1,
                 "structure": swim_structure()}]

    class FakeMCP:
        def __init__(self): self.tools = {}
        def tool(self):
            def deco(fn):
                self.tools[fn.__name__] = fn
                return fn
            return deco

    m = FakeMCP()
    register_migration_tools(m, tp_get)
    result = asyncio.run(m.tools["wp_translate_library_to_intervals"](7))
    assert result["dry_run"] is True and result["library_id"] == 7
    assert result["items"][0]["status"] == "traducido"
    assert len(calls) == 1 and "/libraries/7/items" in calls[0]
