"""The tool schema is generated from the catalog, and is also the guardrail.

If a coordinate, a font or a colour can be expressed here, the model will
eventually express one.
"""

from __future__ import annotations

from slidegen.spec import MAX_BULLETS, MAX_RATIONALE, MAX_TEXT
from slidegen.llm.schema import TOOL_NAME, build_tool_schema, describe_layouts
from tests.conftest import BLANK, TITLE_AND_CONTENT


def candidate_schema(catalog, n=3):
    return build_tool_schema(catalog, n)["function"]["parameters"]["properties"]["candidates"]


def test_the_tool_is_named_and_shaped_as_expected(catalog):
    tool = build_tool_schema(catalog, 3)

    assert tool["type"] == "function"
    assert tool["function"]["name"] == TOOL_NAME
    assert tool["function"]["parameters"]["required"] == ["candidates"]


def test_exactly_n_candidates_are_required(catalog):
    for n in (1, 3, 5):
        candidates = candidate_schema(catalog, n)
        assert candidates["minItems"] == n == candidates["maxItems"]


def test_layout_enum_matches_the_catalogs_usable_layouts(catalog):
    enum = candidate_schema(catalog)["items"]["properties"]["layout_index"]["enum"]

    assert enum == [layout.index for layout in catalog.usable_layouts]
    assert BLANK not in enum


def test_layout_names_appear_in_the_description(catalog):
    description = candidate_schema(catalog)["items"]["properties"]["layout_index"]["description"]

    for layout in catalog.usable_layouts:
        assert layout.name in description


def test_placeholder_properties_cover_every_fillable_idx(catalog):
    properties = candidate_schema(catalog)["items"]["properties"]["placeholders"]["properties"]

    expected = {
        str(placeholder.idx)
        for layout in catalog.usable_layouts
        for placeholder in layout.content_placeholders
    }
    assert set(properties) == expected


def test_placeholder_descriptions_name_their_layouts(catalog):
    properties = candidate_schema(catalog)["items"]["properties"]["placeholders"]["properties"]

    description = properties["1"]["description"]
    assert "Title and Content" in description
    assert "Content Placeholder 2" in description


def test_furniture_placeholders_are_not_offered(catalog):
    properties = candidate_schema(catalog)["items"]["properties"]["placeholders"]["properties"]

    furniture = {
        str(placeholder.idx)
        for layout in catalog.layouts
        for placeholder in layout.placeholders
        if placeholder.is_furniture
    }
    assert furniture and not (furniture & set(properties))


def test_a_placeholder_takes_a_string_or_a_list_of_strings(catalog):
    properties = candidate_schema(catalog)["items"]["properties"]["placeholders"]["properties"]

    variants = properties[str(TITLE_AND_CONTENT)]["anyOf"]
    assert [variant["type"] for variant in variants] == ["string", "array"]
    assert variants[1]["items"]["type"] == "string"


def test_every_string_has_a_max_length(catalog):
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "string":
                assert "maxLength" in node, node
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(build_tool_schema(catalog, 3))


def test_bullet_lists_and_rationales_are_bounded(catalog):
    items = candidate_schema(catalog)["items"]
    properties = items["properties"]["placeholders"]["properties"]

    assert items["properties"]["rationale"]["maxLength"] == MAX_RATIONALE
    assert properties["1"]["anyOf"][0]["maxLength"] == MAX_TEXT
    assert properties["1"]["anyOf"][1]["maxItems"] == MAX_BULLETS


def test_every_object_forbids_additional_properties(catalog):
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False, node
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(build_tool_schema(catalog, 3))


def test_geometry_and_styling_are_unrepresentable(catalog):
    """`additionalProperties: false` everywhere is what enforces this."""
    schema = build_tool_schema(catalog, 3)
    keys: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            keys.update(node.get("properties", {}))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)
    forbidden = {
        "left", "top", "width", "height", "x", "y", "position", "size",
        "font", "font_size", "fontSize", "color", "colour", "fill", "bold",
        "italic", "alignment", "margin", "padding",
    }
    assert not (keys & forbidden)


def test_describe_layouts_lists_every_usable_layout(catalog):
    description = describe_layouts(catalog)

    assert len(description.splitlines()) == len(catalog.usable_layouts)
    assert "[title]" in description
    assert "Blank" not in description


def test_the_schema_follows_the_template(catalog):
    """Drop a layout and the schema drops with it — no code change involved."""
    trimmed = catalog.model_copy(update={"layouts": catalog.layouts[:2]})

    enum = candidate_schema(trimmed)["items"]["properties"]["layout_index"]["enum"]

    assert enum == [0, 1]
