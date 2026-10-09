"""Start and end shifts; save the handover note each one leaves."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Engine, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from database.models import HandoverNote, Manager, Shift
from database.session import get_session

OPEN = Shift.ended_at.is_(None)


def open_shift(manager_id: str, engine: Engine | None = None) -> Shift | None:
    statement = select(Shift).where(Shift.manager_id == manager_id, OPEN)
    with get_session(engine) as session:
        return session.scalar(statement)


def latest_shift(manager_id: str, engine: Engine | None = None) -> Shift | None:
    """The manager's open shift, else their most recently started one."""
    statement = (
        select(Shift)
        .where(Shift.manager_id == manager_id)
        .order_by(Shift.ended_at.is_not(None), Shift.started_at.desc())
        .limit(1)
    )
    with get_session(engine) as session:
        return session.scalar(statement)


def note_for(shift_id: UUID, engine: Engine | None = None) -> str | None:
    statement = select(HandoverNote.note).where(HandoverNote.shift_id == shift_id)
    with get_session(engine) as session:
        return session.scalar(statement)


def start_shift(store_id: str, manager_id: str, engine: Engine | None = None) -> Shift:
    """Open a shift unless one is already open; return the open one."""
    statement = (
        insert(Shift)
        .values(store_id=store_id, manager_id=manager_id)
        .on_conflict_do_nothing(index_elements=["manager_id"], index_where=OPEN)
    )
    with get_session(engine) as session:
        session.execute(statement)
        return session.scalars(
            select(Shift).where(Shift.manager_id == manager_id, OPEN),
        ).one()


def write_note(
    session: Session,
    shift_id: UUID,
    manager_id: str,
    note: str,
) -> UUID | None:
    """Insert or replace the note of an open shift; its id, or None if ended.

    Locks the shift, so ending it waits for the note and the note can't land
    on a shift that has just ended.
    """
    shift = session.scalar(
        select(Shift)
        .where(Shift.id == shift_id, Shift.manager_id == manager_id, OPEN)
        .with_for_update(),
    )
    if shift is None:
        return None
    return session.execute(
        insert(HandoverNote)
        .values(
            store_id=shift.store_id,
            manager_id=manager_id,
            shift_id=shift_id,
            note=note,
        )
        .on_conflict_do_update(
            index_elements=[HandoverNote.shift_id],
            set_={"note": note},
        )
        .returning(HandoverNote.id),
    ).scalar_one()


def save_note(
    shift_id: UUID,
    manager_id: str,
    note: str,
    engine: Engine | None = None,
) -> bool:
    """Save the note of an open shift; False if it has ended."""
    with get_session(engine) as session:
        return write_note(session, shift_id, manager_id, note) is not None


def end_shift(
    shift_id: UUID,
    manager_id: str,
    note: str,
    engine: Engine | None = None,
) -> tuple[datetime, UUID] | None:
    """Save the final note and end the shift together.

    Returns when it ended and the note's id; None if it had already ended.
    """
    with get_session(engine) as session:
        note_id = write_note(session, shift_id, manager_id, note)
        if note_id is None:
            return None
        ended = session.execute(
            update(Shift)
            .where(Shift.id == shift_id)
            .values(ended_at=func.now())
            .returning(Shift.ended_at),
        ).scalar_one()
        assert ended is not None
        return ended, note_id


def ended_shifts(
    store_id: str,
    since: datetime,
    limit: int,
    engine: Engine | None = None,
) -> list[tuple[Shift, Manager, str | None]]:
    """The store's shifts ended since `since`, newest first, with manager and note."""
    statement = (
        select(Shift, Manager, HandoverNote.note)
        .join(Manager, Manager.manager_id == Shift.manager_id)
        .outerjoin(HandoverNote, HandoverNote.shift_id == Shift.id)
        .where(Shift.store_id == store_id, Shift.ended_at >= since)
        .order_by(Shift.ended_at.desc())
        .limit(limit)
    )
    with get_session(engine) as session:
        return [
            (shift, manager, note)
            for shift, manager, note in session.execute(statement)
        ]


def latest_handover(
    store_id: str,
    engine: Engine | None = None,
) -> tuple[Shift, Manager, str] | None:
    """The store's most recently ended shift that left a note."""
    statement = (
        select(Shift, Manager, HandoverNote.note)
        .join(Manager, Manager.manager_id == Shift.manager_id)
        .join(HandoverNote, HandoverNote.shift_id == Shift.id)
        .where(
            Shift.store_id == store_id,
            Shift.ended_at.is_not(None),
            func.trim(HandoverNote.note) != "",
        )
        .order_by(Shift.ended_at.desc())
        .limit(1)
    )
    with get_session(engine) as session:
        row = session.execute(statement).first()
        return None if row is None else (row[0], row[1], row[2])


def handover_by_note(
    note_id: UUID,
    engine: Engine | None = None,
) -> tuple[Shift, Manager, str] | None:
    """The shift, manager and text of one handover note."""
    statement = (
        select(Shift, Manager, HandoverNote.note)
        .join(HandoverNote, HandoverNote.shift_id == Shift.id)
        .join(Manager, Manager.manager_id == Shift.manager_id)
        .where(HandoverNote.id == note_id)
    )
    with get_session(engine) as session:
        row = session.execute(statement).first()
        return None if row is None else (row[0], row[1], row[2])
