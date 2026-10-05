"""Parse, render and update Markdown backlog tickets."""

from __future__ import annotations

import fcntl
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from cc_tandem.effort import Effort


class BacklogError(ValueError):
    """Invalid backlog content or an unsafe requested update."""


class TicketNotFoundError(BacklogError):
    """The requested ticket does not exist."""


class DuplicateTicketError(BacklogError):
    """A ticket ID occurs more than once."""


class MissingStatusError(BacklogError):
    """A ticket has no status line outside code fences."""


class InvalidStatusError(BacklogError):
    """A status is not supported."""


class StatusConflictError(BacklogError):
    """The ticket's current status differs from the caller's expectation."""


class Status(StrEnum):
    """Recognised backlog states."""

    TODO = "TODO"
    IN_PROGRESS = "IN PROGRESS"
    DONE = "DONE"
    BLOCKED = "BLOCKED (needs owner decision)"
    UNREFINED = "UNREFINED"


@dataclass(frozen=True)
class Ticket:
    """Ticket content independent of Markdown layout."""

    id: str
    title: str
    effort: Effort
    body: str
    status: Status
    note: str = ""


@dataclass(frozen=True)
class _LocatedTicket:
    ticket: Ticket
    status_line: int


_HEADING = re.compile(r"^## ([^\s:]+): (.+)$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def _status(value: Status | str) -> Status:
    try:
        return Status(value)
    except ValueError:
        raise InvalidStatusError(f"Unrecognised status: {value!r}") from None


def _single_line(value: str, name: str) -> None:
    if "\n" in value or "\r" in value:
        raise BacklogError(f"{name} must be a single line")


def _outside_fences(lines: list[str]) -> list[int]:
    indices: list[int] = []
    fence = ""
    length = 0
    for index, line in enumerate(lines):
        match = _FENCE.match(line.rstrip("\r\n"))
        if fence:
            if match and match[1][0] == fence and len(match[1]) >= length and not match[2].strip():
                fence = ""
        elif match and not (match[1][0] == "`" and "`" in match[2]):
            fence, length = match[1][0], len(match[1])
        else:
            indices.append(index)
    return indices


def _locate(text: str) -> list[_LocatedTicket]:
    lines = text.splitlines(keepends=True)
    outside = set(_outside_fences(lines))
    headings = [
        (index, match)
        for index, line in enumerate(lines)
        if index in outside and (match := _HEADING.fullmatch(line.rstrip("\r\n")))
    ]
    found: list[_LocatedTicket] = []
    seen: set[str] = set()
    for position, (start, heading) in enumerate(headings):
        ticket_id, title = heading.groups()
        if ticket_id in seen:
            raise DuplicateTicketError(f"Duplicate ticket ID: {ticket_id}")
        seen.add(ticket_id)
        end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        metadata: dict[str, list[int]] = {
            key: [
                index
                for index in range(start + 1, end)
                if index in outside and lines[index].startswith(f"{key}:")
            ]
            for key in ("Effort", "Status")
        }
        if not metadata["Status"]:
            raise MissingStatusError(f"Ticket {ticket_id} has no Status line")
        if len(metadata["Status"]) != 1 or len(metadata["Effort"]) != 1:
            raise BacklogError(f"Ticket {ticket_id} requires one Effort and one Status line")
        effort_index, status_index = metadata["Effort"][0], metadata["Status"][0]
        effort_text = lines[effort_index].removeprefix("Effort:").strip()
        try:
            effort = Effort.parse(effort_text)
        except ValueError as error:
            raise BacklogError(f"Ticket {ticket_id}: {error}") from error
        status_text = lines[status_index].removeprefix("Status:").strip()
        value, separator, note = status_text.partition(" — ")
        body_indices = [
            index for index in range(start + 1, end) if index not in (effort_index, status_index)
        ]
        while body_indices and not lines[body_indices[-1]].strip("\r\n"):
            body_indices.pop()
        # Only a rule after the status can be a ticket separator. A rule in the
        # body before the status is part of the ticket's content.
        if (
            body_indices
            and body_indices[-1] > status_index
            and body_indices[-1] in outside
            and lines[body_indices[-1]].rstrip("\r\n") == "---"
        ):
            body_indices.pop()
        body = "".join(lines[index] for index in body_indices).strip("\r\n")
        found.append(
            _LocatedTicket(
                Ticket(ticket_id, title, effort, body, _status(value), note if separator else ""),
                status_index,
            )
        )
    return found


def parse(text: str) -> list[Ticket]:
    """Parse all ticket sections, rejecting malformed metadata and duplicate IDs."""
    return [located.ticket for located in _locate(text)]


def read(path: Path) -> list[Ticket]:
    """Read UTF-8 without translating CRLF newlines."""
    return parse(path.read_bytes().decode("utf-8"))


def get_ticket(tickets: list[Ticket], ticket_id: str) -> Ticket:
    """Find a ticket by its exact ID."""
    for ticket in tickets:
        if ticket.id == ticket_id:
            return ticket
    raise TicketNotFoundError(f"Ticket not found: {ticket_id}")


def set_status(
    path: Path,
    ticket_id: str,
    status: Status | str,
    note: str,
    *,
    expected_status: Status | str,
) -> None:
    """Change one line, checking current state under a cooperating-writer file lock.

    Writers using this function serialize their read/check/write operations. External
    editors do not participate in the advisory lock.
    """
    requested, expected = _status(status), _status(expected_status)
    _single_line(note, "note")
    with path.open("r+b") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        text = stream.read().decode("utf-8")
        located = _locate(text)
        ticket = get_ticket([item.ticket for item in located], ticket_id)
        if ticket.status != expected:
            raise StatusConflictError(
                f"Ticket {ticket_id}: expected {expected}, found {ticket.status}"
            )
        index = next(item.status_line for item in located if item.ticket.id == ticket_id)
        lines = text.splitlines(keepends=True)
        original = lines[index]
        ending = "\r\n" if original.endswith("\r\n") else "\n" if original.endswith("\n") else ""
        suffix = f" — {note}" if note else ""
        lines[index] = f"Status: {requested}{suffix}{ending}"
        stream.seek(0)
        stream.write("".join(lines).encode("utf-8"))
        stream.truncate()


def render(ticket: Ticket) -> str:
    """Render parsed content in the standard ticket layout."""
    for name, value in (("id", ticket.id), ("title", ticket.title), ("note", ticket.note)):
        _single_line(value, name)
    if not _HEADING.fullmatch(f"## {ticket.id}: {ticket.title}"):
        raise BacklogError("Invalid ticket ID or title")
    status = _status(ticket.status)
    suffix = f" — {ticket.note}" if ticket.note else ""
    result = (
        f"## {ticket.id}: {ticket.title}\nEffort: {Effort.parse(ticket.effort)}\n\n"
        f"{ticket.body}\n\nStatus: {status}{suffix}\n"
    )
    if parse(result) != [ticket]:
        raise BacklogError("Ticket body contains conflicting metadata or an unclosed fence")
    return result


def _reported(reported: str) -> str:
    # Prefix every line, including empty ones, without stripping the reporter's words.
    return "**Reported (verbatim):**\n\n" + "\n".join(f"> {line}" for line in reported.split("\n"))


def render_ticket(
    ticket_id: str,
    title: str,
    effort: Effort,
    reported: str,
    interpretation: str,
    *,
    status: Status = Status.TODO,
    note: str = "",
) -> str:
    """Render a refined ticket with the original report and interpretation below."""
    interpretation = interpretation.strip("\r\n")
    body = f"{_reported(reported)}\n\n**Interpretation:**\n\n{interpretation}".rstrip("\r\n")
    return render(Ticket(ticket_id, title, effort, body, status, note))


def render_raw(ticket_id: str, title: str, effort: Effort, reported: str) -> str:
    """Render an unrefined report without adding an interpretation."""
    return render(Ticket(ticket_id, title, effort, _reported(reported), Status.UNREFINED))
