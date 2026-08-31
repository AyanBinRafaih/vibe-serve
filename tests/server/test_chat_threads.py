"""Per-thread experiment chat: registry, routing, replay, and wire shapes."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from vibesys.server import RunSupervisor
from vibesys.server.chat_options import ChatRunSettings
from vibesys.server.events import ChatData, ChatThreadCreatedData, EventType, RunEvent, make_event
from vibesys.server.protocol import (
    ChatOptionsQuery,
    ChatQuery,
    ChatThreadCreateQuery,
    ChatThreadInfo,
    Response,
)
from vibesys.server.service import SupervisionService
from vibesys.server.supervisor import ChatThreadFactory, ChatThreadHandle


def _factory(
    calls: list[tuple[str, str | None, str | None, str | None]], answer: str
) -> ChatThreadFactory:
    def factory(
        thread_id: str, driver: str | None, provider: str | None, model: str | None
    ) -> ChatThreadHandle:
        calls.append((thread_id, driver, provider, model))
        return ChatThreadHandle(
            spec=ChatThreadCreatedData(
                thread_id=thread_id,
                driver=driver or "agentshim",
                provider=provider or "codex",
                model=model or "gpt-default",
                created_at=datetime.now(UTC),
            ),
            handler=lambda question: f"{answer}: {question}",
        )

    return factory


def _events(path: Path) -> list[dict]:
    return [
        json.loads(line) for line in (path / "run-events.jsonl").read_text().splitlines() if line
    ]


def test_created_thread_routes_chat_and_stamps_its_events(tmp_path):  # noqa: ANN001, ANN201
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    supervisor.set_chat_handler(lambda question: f"default: {question}")
    calls: list[tuple[str, str | None, str | None, str | None]] = []
    supervisor.set_chat_thread_factory(_factory(calls, "omnigent-claude"))

    spec = supervisor.create_chat_thread(driver="omnigent", provider="claude", model="opus")

    assert calls == [(spec.thread_id, "omnigent", "claude", "opus")]
    assert supervisor.chat("what changed?", thread_id=spec.thread_id) == (
        "omnigent-claude: what changed?"
    )
    # The default thread is untouched by thread routing.
    assert supervisor.chat("what changed?") == "default: what changed?"

    events = _events(tmp_path)
    created = [event for event in events if event["type"] == "chat_thread_created"]
    assert len(created) == 1
    assert created[0]["chat_thread_id"] == spec.thread_id
    assert created[0]["data"]["driver"] == "omnigent"
    assert created[0]["data"]["provider"] == "claude"
    assert created[0]["data"]["model"] == "opus"
    chats = [event for event in events if event["type"] == "chat"]
    assert [event["chat_thread_id"] for event in chats] == [spec.thread_id, None]
    assert chats[0]["agent_kind"] == "chat"


def test_unknown_thread_gets_a_clear_error_answer_without_an_event(tmp_path):  # noqa: ANN001, ANN201
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)

    answer = supervisor.chat("hello?", thread_id="missing-thread")

    assert "Unknown experiment chat thread 'missing-thread'" in answer
    assert all(event["type"] != "chat" for event in _events(tmp_path))


def test_thread_creation_without_a_factory_is_rejected(tmp_path):  # noqa: ANN001, ANN201
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)

    with pytest.raises(RuntimeError, match="chat threads are not available"):
        supervisor.create_chat_thread()


def test_invalid_combination_error_from_factory_propagates(tmp_path):  # noqa: ANN001, ANN201
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)

    rejection = "agent driver 'omnigent' does not support provider 'gemini'"

    def rejecting_factory(*_args: object) -> ChatThreadHandle:
        raise ValueError(rejection)

    supervisor.set_chat_thread_factory(rejecting_factory)

    with pytest.raises(ValueError, match="does not support provider 'gemini'"):
        supervisor.create_chat_thread(driver="omnigent", provider="gemini")
    assert all(event["type"] != "chat_thread_created" for event in _events(tmp_path))


def test_threads_replay_from_the_event_log_and_rebuild_on_demand(tmp_path):  # noqa: ANN001, ANN201
    first = RunSupervisor()
    first.attach(tmp_path)
    first.set_chat_thread_factory(_factory([], "first"))
    spec = first.create_chat_thread(driver="agentshim", provider="claude")
    assert first.chat("why did round two regress so much?", thread_id=spec.thread_id)

    resumed = RunSupervisor()
    resumed.attach(tmp_path)
    replayed = resumed.chat_threads()
    assert [thread.thread_id for thread in replayed] == [spec.thread_id]
    # The replayed spec carries the backend-derived title from the first turn.
    assert replayed[0].title == "why did round two regress so much?"

    # Without a factory the thread is known but cannot answer.
    unavailable = resumed.chat("still there?", thread_id=spec.thread_id)
    assert "cannot answer right now" in unavailable

    calls: list[tuple[str, str | None, str | None, str | None]] = []
    resumed.set_chat_thread_factory(_factory(calls, "rebuilt"))
    assert resumed.chat("still there?", thread_id=spec.thread_id) == "rebuilt: still there?"
    # The handler was rebuilt from the recorded spec, not from defaults.
    assert calls == [(spec.thread_id, "agentshim", "claude", "gpt-default")]


def test_first_message_titles_an_untitled_thread_once(tmp_path):  # noqa: ANN001, ANN201
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    supervisor.set_chat_thread_factory(_factory([], "answer"))
    spec = supervisor.create_chat_thread()
    assert spec.title == ""

    supervisor.chat("explain why the benchmark throughput regressed in round four", spec.thread_id)
    supervisor.chat("and round five?", thread_id=spec.thread_id)

    chats = [event for event in _events(tmp_path) if event["type"] == "chat"]
    # Long first lines cut on a word boundary near 40 characters.
    assert chats[0]["data"]["thread_title"] == "explain why the benchmark throughput…"
    assert chats[1]["data"]["thread_title"] is None
    assert supervisor.chat_threads()[0].title == "explain why the benchmark throughput…"


def test_explicit_title_is_authoritative(tmp_path):  # noqa: ANN001, ANN201
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    supervisor.set_chat_thread_factory(_factory([], "answer"))

    spec = supervisor.create_chat_thread(title="  perf deep dive  ")
    supervisor.chat("first question", thread_id=spec.thread_id)

    assert spec.title == "perf deep dive"
    chats = [event for event in _events(tmp_path) if event["type"] == "chat"]
    assert chats[0]["data"]["thread_title"] is None
    assert supervisor.chat_threads()[0].title == "perf deep dive"


def test_cleared_threads_stop_routing_but_keep_replayable_specs(tmp_path):  # noqa: ANN001, ANN201
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    supervisor.set_chat_thread_factory(_factory([], "answer"))
    spec = supervisor.create_chat_thread()

    supervisor.clear_chat_threads_and_drain()

    assert "cannot answer right now" in supervisor.chat("hello", thread_id=spec.thread_id)
    assert [thread.thread_id for thread in supervisor.chat_threads()] == [spec.thread_id]


def test_service_creates_threads_and_routes_threaded_chat(tmp_path):  # noqa: ANN001, ANN201
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    supervisor.set_chat_thread_factory(_factory([], "thread-agent"))
    service = SupervisionService(supervisor)

    created = service.execute(ChatThreadCreateQuery(provider="claude"))
    assert created.chat_thread is not None
    assert created.chat_thread.provider == "claude"
    assert created.chat_thread.driver == "agentshim"
    assert any(event.type is EventType.CHAT_THREAD_CREATED for event in created.events)

    response = service.execute(
        ChatQuery(text="what changed?", thread_id=created.chat_thread.thread_id)
    )
    assert response.chat is not None
    assert response.chat.answer == "thread-agent: what changed?"
    assert response.chat.thread_id == created.chat_thread.thread_id
    chat_events = [event for event in response.events if event.type is EventType.CHAT]
    assert [event.chat_thread_id for event in chat_events] == [created.chat_thread.thread_id]


def test_chat_options_group_by_provider_and_mark_the_run_model(tmp_path):  # noqa: ANN001, ANN201
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    supervisor.set_chat_run_settings(
        ChatRunSettings(
            driver="omnigent",
            provider="codex",
            model="gpt-5.5-run",
            role_models=("gpt-5.6-outer", "gpt-5.5-run"),
        )
    )
    service = SupervisionService(supervisor)

    options = service.execute(ChatOptionsQuery()).chat_options
    assert options is not None

    # Only providers the run's configured driver supports, and no driver field
    # anywhere in the response: a client never chooses one.
    assert [group.provider for group in options.providers] == ["claude", "codex"]
    assert "driver" not in options.model_dump()

    codex = next(group for group in options.providers if group.provider == "codex")
    assert codex.models[0].model == "gpt-5.5-run"
    assert codex.models[0].source == "run"
    assert codex.models[0].default is True
    # The role override follows the run model; the duplicate role entry is not
    # repeated, and the curated suggestions come last.
    assert [option.model for option in codex.models[:2]] == ["gpt-5.5-run", "gpt-5.6-outer"]
    assert codex.models[1].source == "role"
    assert {option.source for option in codex.models[2:]} == {"suggested"}

    # Another provider gets suggestions only, and nothing is marked default.
    claude = next(group for group in options.providers if group.provider == "claude")
    assert {option.source for option in claude.models} == {"suggested"}
    assert [option.model for option in claude.models if option.default] == []
    assert sum(option.default for group in options.providers for option in group.models) == 1


def test_chat_options_are_absent_before_a_run_context_attaches(tmp_path):  # noqa: ANN001, ANN201
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)

    response = SupervisionService(supervisor).execute(ChatOptionsQuery())

    # None distinguishes bootstrap from a run that genuinely offers nothing.
    assert response.ok is True
    assert response.chat_options is None


def test_chat_thread_create_defaults_its_driver_to_the_runs(tmp_path):  # noqa: ANN001, ANN201
    supervisor = RunSupervisor()
    supervisor.attach(tmp_path)
    calls: list[tuple[str, str | None, str | None, str | None]] = []
    supervisor.set_chat_thread_factory(_factory(calls, "thread-agent"))
    service = SupervisionService(supervisor)

    # The client sends provider and model only; the driver stays the run's.
    created = service.execute(ChatThreadCreateQuery(provider="claude", model="opus"))

    assert created.chat_thread is not None
    assert calls == [(created.chat_thread.thread_id, None, "claude", "opus")]
    assert created.chat_thread.driver == "agentshim"


def test_chat_thread_wire_shapes_round_trip():  # noqa: ANN201
    request = ChatThreadCreateQuery(driver="omnigent", provider="codex", model="o4", title="t")
    assert ChatThreadCreateQuery.model_validate_json(request.model_dump_json()) == request

    query = ChatQuery(text="why?", thread_id="thread-1")
    assert ChatQuery.model_validate_json(query.model_dump_json()) == query
    # Old clients omit the field entirely and land on the default thread.
    assert ChatQuery.model_validate({"type": "query.chat", "text": "why?"}).thread_id is None

    event = make_event(
        EventType.CHAT_THREAD_CREATED,
        chat_thread_id="thread-1",
        agent_kind="chat",
        data=ChatThreadCreatedData(
            thread_id="thread-1",
            title="",
            driver="agentshim",
            provider="claude",
            model="opus",
            created_at=datetime.now(UTC),
        ),
    )
    restored = RunEvent.model_validate_json(event.model_dump_json())
    assert restored.chat_thread_id == "thread-1"
    assert isinstance(restored.data, ChatThreadCreatedData)
    assert restored.data.provider == "claude"

    chat_event = make_event(
        EventType.CHAT,
        "why?",
        chat_thread_id="thread-1",
        data=ChatData(answer="because", thread_title="why?"),
    )
    restored_chat = RunEvent.model_validate_json(chat_event.model_dump_json())
    assert isinstance(restored_chat.data, ChatData)
    assert restored_chat.data.thread_title == "why?"

    assert ChatOptionsQuery.model_validate_json(ChatOptionsQuery().model_dump_json()).type == (
        "query.chat_options"
    )

    response = Response.model_validate_json(
        Response(
            request_id="r1",
            chat_thread=ChatThreadInfo(
                thread_id="thread-1",
                driver="agentshim",
                provider="claude",
                model="opus",
            ),
        ).model_dump_json()
    )
    assert response.chat_thread is not None
    assert response.chat_thread.thread_id == "thread-1"
