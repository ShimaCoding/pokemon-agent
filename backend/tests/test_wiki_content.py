"""
Tests for backend/wiki_content.py.

These tests enforce the cross-references between LESSONS, ARCHITECTURE and
EVENTS_CATALOG so that the wiki content stays coherent as it grows.
"""

from backend.wiki_content import ARCHITECTURE, EVENTS_CATALOG, LESSONS

# Single source of truth: the set of SSE event types that _sse_generator emits.
# Keep this in sync with the "Event sequence" docstring in main.py::_sse_generator.
SSE_EVENT_TYPES: set[str] = {
    "start",
    "agent_init",
    "llm_call",
    "model_attempt",
    "tool_call",
    "tool_result",
    "text",
    "done",
    "error",
}


def test_events_catalog_covers_all_sse_types() -> None:
    """Every SSE event type emitted by _sse_generator must be in EVENTS_CATALOG."""
    catalog_keys = set(EVENTS_CATALOG.keys())
    missing = SSE_EVENT_TYPES - catalog_keys
    extra = catalog_keys - SSE_EVENT_TYPES
    assert not missing, f"EVENTS_CATALOG is missing entries for: {missing}"
    assert not extra, f"EVENTS_CATALOG has unknown event types: {extra}"


def test_lessons_reference_valid_events() -> None:
    """Every related_events entry in LESSONS must be a valid SSE event type."""
    for lesson in LESSONS:
        for ev in lesson.related_events:
            assert ev in SSE_EVENT_TYPES, (
                f"Lesson {lesson.id!r} references unknown event {ev!r}"
            )


def test_architecture_nodes_have_lesson_refs() -> None:
    """Nodes with a related_lesson_id must point to an existing lesson."""
    lesson_ids = {lesson.id for lesson in LESSONS}
    for node in ARCHITECTURE["nodes"]:
        ref = node.get("related_lesson_id")
        if ref is None:
            continue
        assert ref in lesson_ids, (
            f"Architecture node {node['id']!r} references unknown lesson {ref!r}"
        )


def test_events_catalog_references_valid_lessons_and_nodes() -> None:
    """EVENTS_CATALOG entries must reference existing lessons and nodes."""
    lesson_ids = {lesson.id for lesson in LESSONS}
    node_ids = {node["id"] for node in ARCHITECTURE["nodes"]}
    for event_type, entry in EVENTS_CATALOG.items():
        lesson_ref = entry.get("related_lesson_id")
        node_ref = entry.get("related_node_id")
        if lesson_ref is not None:
            assert lesson_ref in lesson_ids, (
                f"EVENTS_CATALOG[{event_type!r}] references unknown lesson {lesson_ref!r}"
            )
        if node_ref is not None:
            assert node_ref in node_ids, (
                f"EVENTS_CATALOG[{event_type!r}] references unknown node {node_ref!r}"
            )


def test_lessons_related_nodes_exist() -> None:
    """Lessons' related_nodes entries must match nodes in ARCHITECTURE."""
    node_ids = {node["id"] for node in ARCHITECTURE["nodes"]}
    for lesson in LESSONS:
        for n in lesson.related_nodes:
            assert n in node_ids, (
                f"Lesson {lesson.id!r} references unknown node {n!r}"
            )


def test_lessons_have_unique_ids() -> None:
    ids = [lesson.id for lesson in LESSONS]
    assert len(ids) == len(set(ids)), f"Duplicate lesson ids: {ids}"


def test_lessons_have_substantial_body() -> None:
    """Every lesson should have real content (not placeholder)."""
    for lesson in LESSONS:
        assert len(lesson.body_md) > 400, (
            f"Lesson {lesson.id!r} body is too short ({len(lesson.body_md)} chars)"
        )
