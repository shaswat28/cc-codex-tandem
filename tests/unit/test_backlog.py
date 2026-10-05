"""Ticket parsing, rendering and byte-preserving status updates."""

from pathlib import Path

import pytest

from cc_tandem.backlog import (
    BacklogError,
    DuplicateTicketError,
    InvalidStatusError,
    MissingStatusError,
    Status,
    StatusConflictError,
    Ticket,
    TicketNotFoundError,
    get_ticket,
    parse,
    read,
    render,
    render_raw,
    render_ticket,
    set_status,
)
from cc_tandem.effort import Effort

FIXTURES = Path(__file__).parents[1] / "fixtures"


def sample(ticket_id: str = "T-01", status: Status = Status.TODO) -> str:
    return render(Ticket(ticket_id, "A task", Effort.MEDIUM, "Some words.", status))


@pytest.mark.parametrize("status", list(Status))
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_round_trip(status: Status, newline: str) -> None:
    text = sample(status=status).replace("\n", newline)
    ticket = parse(text)[0]
    assert ticket == Ticket("T-01", "A task", Effort.MEDIUM, "Some words.", status)
    assert parse(render(ticket)) == [ticket]


def test_note_and_multiple_tickets() -> None:
    first = Ticket("T-01", "First", Effort.LOW, "Body", Status.DONE, "tested — complete")
    text = "# Tickets\n\n" + render(first) + "\n---\n\n" + sample("T-02")
    tickets = parse(text)
    assert tickets[0] == first
    assert get_ticket(tickets, "T-02").status == Status.TODO
    assert parse(render(tickets[0])) == [first]


@pytest.mark.parametrize("fence", ["```", "~~~~"])
def test_fenced_metadata_and_headings(fence: str) -> None:
    body = (
        f"Before\n{fence}text\nStatus: NOT A STATUS\nEffort: xhigh\n"
        f"## T-01: Not a heading\n{fence[0] * 2}\n{fence}\nAfter"
    )
    ticket = Ticket("T-01", "Fences", Effort.HIGH, body, Status.TODO)
    assert parse(render(ticket)) == [ticket]


def test_fence_closer_requires_matching_marker_and_no_info() -> None:
    body = "```python\n~~~\n``` still open\nStatus: ignored\n````\nDone"
    assert parse(render(Ticket("T-01", "Fence", Effort.LOW, body, Status.TODO)))[0].body == body


def test_backticks_in_info_do_not_open_fence() -> None:
    body = "```not`a`fence"
    assert parse(render(Ticket("T-01", "Body", Effort.LOW, body, Status.TODO)))[0].body == body


@pytest.mark.parametrize(
    "reported", ["exact  spaces  ", "first\n\nlast\n", "", "## X\nStatus: DONE"]
)
def test_reported_verbatim(reported: str) -> None:
    refined = render_ticket("T-01", "Report", Effort.MEDIUM, reported, "Plan here.")
    raw = render_raw("T-02", "Raw", Effort.LOW, reported)
    for text in (refined, raw):
        quote = text.split("**Reported (verbatim):**\n\n", 1)[1].split("\n\n", 1)[0]
        assert "\n".join(line.removeprefix("> ") for line in quote.split("\n")) == reported
        assert parse(render(parse(text)[0])) == parse(text)
    assert "**Interpretation:**\n\nPlan here." in refined
    assert "**Interpretation:**" not in raw
    assert parse(raw)[0].status == Status.UNREFINED


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("final_newline", [True, False])
def test_update_changes_exactly_one_line(tmp_path: Path, newline: str, final_newline: bool) -> None:
    body = "```\nStatus: TODO\n```\nUnicode café."
    target = render(Ticket("T-02", "Target", Effort.LOW, body, Status.TODO))
    text = "# Tickets\n\n" + sample() + "\n---\n\n" + target
    if not final_newline:
        text = text.rstrip("\n")
    before = text.replace("\n", newline).encode()
    path = tmp_path / "BACKLOG.md"
    path.write_bytes(before)
    set_status(path, "T-02", Status.DONE, "validated ✓", expected_status=Status.TODO)
    old_lines, new_lines = (
        before.splitlines(keepends=True),
        path.read_bytes().splitlines(keepends=True),
    )
    changed = [
        i for i, (old, new) in enumerate(zip(old_lines, new_lines, strict=True)) if old != new
    ]
    assert changed == [len(old_lines) - 1]
    ending = newline if final_newline else ""
    assert new_lines[-1] == f"Status: DONE — validated ✓{ending}".encode()
    assert read(path)[0].status == Status.TODO
    assert read(path)[1].status == Status.DONE


def test_conflict_does_not_overwrite_or_update_next_ticket(tmp_path: Path) -> None:
    path = tmp_path / "BACKLOG.md"
    path.write_text(sample(status=Status.DONE) + "\n---\n\n" + sample("T-02"))
    before = path.read_bytes()
    with pytest.raises(StatusConflictError, match="expected TODO, found DONE"):
        set_status(path, "T-01", Status.IN_PROGRESS, "", expected_status=Status.TODO)
    assert path.read_bytes() == before
    set_status(path, "T-01", Status.IN_PROGRESS, "", expected_status=Status.DONE)
    assert read(path)[0].status == Status.IN_PROGRESS
    assert read(path)[1].status == Status.TODO


def test_missing_ticket(tmp_path: Path) -> None:
    path = tmp_path / "BACKLOG.md"
    path.write_text(sample())
    with pytest.raises(TicketNotFoundError, match="T-99"):
        get_ticket(read(path), "T-99")
    with pytest.raises(TicketNotFoundError):
        set_status(path, "T-99", Status.DONE, "", expected_status=Status.TODO)
    assert path.read_text() == sample()


def test_duplicate_ids() -> None:
    with pytest.raises(DuplicateTicketError, match="T-01"):
        parse(sample() + sample())


def test_malformed_fixture_and_update(tmp_path: Path) -> None:
    path = tmp_path / "BACKLOG.md"
    before = (FIXTURES / "backlog-malformed.md").read_bytes()
    path.write_bytes(before)
    with pytest.raises(MissingStatusError, match="T-01"):
        read(path)
    with pytest.raises(MissingStatusError, match="T-01"):
        set_status(path, "T-01", Status.DONE, "", expected_status=Status.TODO)
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "text",
    [
        sample().replace("Effort: medium\n", ""),
        sample().replace("Effort: medium", "Effort: low\nEffort: medium"),
        sample().replace("Status: TODO", "Status: TODO\nStatus: DONE"),
        sample().replace("Effort: medium", "Effort: xhigh"),
    ],
)
def test_invalid_metadata(text: str) -> None:
    with pytest.raises(BacklogError):
        parse(text)


def test_unknown_status(tmp_path: Path) -> None:
    with pytest.raises(InvalidStatusError, match="MYSTERY"):
        parse(sample().replace("Status: TODO", "Status: MYSTERY"))
    path = tmp_path / "BACKLOG.md"
    path.write_text(sample())
    with pytest.raises(InvalidStatusError):
        set_status(path, "T-01", "MYSTERY", "", expected_status=Status.TODO)
    with pytest.raises(InvalidStatusError):
        set_status(path, "T-01", Status.DONE, "", expected_status="MYSTERY")
    assert path.read_text() == sample()


@pytest.mark.parametrize("note", ["line\nbreak", "line\rbreak"])
def test_update_rejects_multiline_note(tmp_path: Path, note: str) -> None:
    path = tmp_path / "BACKLOG.md"
    path.write_text(sample())
    with pytest.raises(BacklogError, match="single line"):
        set_status(path, "T-01", Status.DONE, note, expected_status=Status.TODO)
    assert path.read_text() == sample()


@pytest.mark.parametrize("ticket_id", ["bad id", "bad:id", "", "T-01\n## T-02"])
def test_render_invalid_id(ticket_id: str) -> None:
    with pytest.raises(BacklogError):
        render_raw(ticket_id, "Title", Effort.LOW, "Report")


@pytest.mark.parametrize("body", ["Status: DONE", "Effort: high", "```\nUnclosed"])
def test_render_conflicting_body(body: str) -> None:
    with pytest.raises(BacklogError):
        render(Ticket("T-01", "Bad body", Effort.LOW, body, Status.TODO))


def test_empty_document_and_empty_body() -> None:
    assert parse("# Tickets\n") == []
    ticket = Ticket("T-01", "Empty", Effort.LOW, "", Status.TODO)
    assert parse(render(ticket)) == [ticket]
    assert parse(render(ticket) + "\n---\n")[0].body == ""


def test_current_backlog_parses() -> None:
    tickets = read(Path(__file__).parents[2] / "BACKLOG.md")
    assert get_ticket(tickets, "T-03").effort == Effort.MEDIUM


@pytest.mark.parametrize("body", ["---", "Text\n---"])
def test_body_horizontal_rule_is_preserved(body: str) -> None:
    ticket = Ticket("T-01", "Rule", Effort.LOW, body, Status.TODO)
    assert parse(render(ticket) + "\n---\n") == [ticket]


def test_render_rejects_noncanonical_body() -> None:
    with pytest.raises(BacklogError, match="conflicting metadata"):
        render(Ticket("T-01", "Body", Effort.LOW, "\nBody\n", Status.TODO))


def test_interpretation_with_trailing_newlines() -> None:
    text = render_ticket("T-01", "Title", Effort.LOW, "Report", "Plan\n")
    assert parse(text)[0].body.endswith("Plan")


def test_concurrent_updates_detect_conflict(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path / "BACKLOG.md"
    path.write_text(sample())

    def update(status: Status) -> bool:
        try:
            set_status(path, "T-01", status, "", expected_status=Status.TODO)
        except StatusConflictError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, [Status.DONE, Status.IN_PROGRESS]))
    assert sorted(results) == [False, True]
    assert read(path)[0].status in (Status.DONE, Status.IN_PROGRESS)
