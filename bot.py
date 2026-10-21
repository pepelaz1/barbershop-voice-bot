"""Testable orchestration core for an outbound barbershop voice bot.

No network access or real calls are made by this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol
import re


class Scenario(StrEnum):
    CONFIRM = "подтверждение"
    FEEDBACK = "7дней"
    REMINDER = "40дней"
    RETURN = "90дней"
    LOST = "потерянный"


class Outcome(StrEnum):
    NO_ANSWER = "недозвон"
    CALLBACK = "перезвонить"
    CONFIRMED = "подтвердил"
    INTERESTED = "интерес к записи"
    COMPLAINT = "жалоба"
    DECLINED = "отказ от звонков"
    COMPLETED = "разговор завершён"


@dataclass(frozen=True)
class Client:
    id: str
    name: str
    phone: str
    last_visit: datetime | None = None
    next_booking: datetime | None = None
    next_booking_id: str | None = None
    booking_confirmed: bool = False
    opted_out: bool = False
    lost: bool = False
    master: str | None = None


@dataclass(frozen=True)
class CallRequest:
    key: str
    scenario: Scenario
    client: Client
    test: bool = False
    context: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CallResult:
    outcome: Outcome
    reason: str | None = None
    feedback: str | None = None
    requested_service: str | None = None
    requested_master: str | None = None
    requested_time: str | None = None
    agreed_extras: tuple[str, ...] = ()
    applied_extras: tuple[str, ...] = ()
    crm_booking_id: str | None = None


class Crm(Protocol):
    def get_client(self, identifier: str) -> Client | None: ...
    def save_call_result(self, request: CallRequest, result: CallResult) -> None: ...


class Dialer(Protocol):
    def call(self, request: CallRequest) -> CallResult: ...


class Notifier(Protocol):
    def send(self, message: str) -> None: ...


class BotService:
    def __init__(self, crm: Crm, dialer: Dialer, notifier: Notifier,
                 admin_ids: set[str], *, start_hour: int, end_hour: int,
                 max_attempts: int = 2) -> None:
        if not 0 <= start_hour < end_hour <= 24 or max_attempts < 1:
            raise ValueError("Invalid calling policy")
        self.crm, self.dialer, self.notifier = crm, dialer, notifier
        self.admin_ids = admin_ids
        self.start_hour, self.end_hour = start_hour, end_hour
        self.max_attempts = max_attempts
        self.results: list[tuple[datetime, CallRequest, CallResult]] = []
        self.processed_commands: set[str] = set()
        self.handled_events: set[str] = set()
        self.attempts: dict[str, int] = {}

    @staticmethod
    def choose_scenario(client: Client, now: datetime) -> Scenario | None:
        if client.opted_out:
            return None
        # An imminent booking takes precedence. A future booking suppresses
        # reactivation and reminder calls.
        if client.next_booking:
            until = client.next_booking - now
            if not client.booking_confirmed and timedelta(0) <= until <= timedelta(hours=1):
                return Scenario.CONFIRM
            return None
        if client.lost:
            return Scenario.LOST
        if not client.last_visit:
            return None
        age = now - client.last_visit
        if age >= timedelta(days=90):
            return Scenario.RETURN
        if age >= timedelta(days=40):
            return Scenario.REMINDER
        if timedelta(days=7) <= age < timedelta(days=8):
            return Scenario.FEEDBACK
        return None

    @staticmethod
    def event_key(client: Client, scenario: Scenario) -> str:
        anchor = client.next_booking_id or (client.last_visit.isoformat() if client.last_visit else "lost")
        return f"{client.id}:{scenario}:{anchor}"

    def run_scheduled(self, client_id: str, now: datetime) -> CallResult | None:
        if not self.start_hour <= now.hour < self.end_hour:
            return None
        client = self.crm.get_client(client_id)  # re-read before each call
        if not client:
            return None
        scenario = self.choose_scenario(client, now)
        if not scenario:
            return None
        key = self.event_key(client, scenario)
        if key in self.handled_events or self.attempts.get(key, 0) >= self.max_attempts:
            return None
        self.attempts[key] = self.attempts.get(key, 0) + 1
        request = CallRequest(key, scenario, client)
        return self._perform(request, now)

    def handle_command(self, admin_id: str, message_id: str, command: str,
                       now: datetime) -> CallResult | None:
        if admin_id not in self.admin_ids:
            raise PermissionError("Administrator access required")
        if message_id in self.processed_commands:
            return None
        parts = command.strip().split(maxsplit=3)
        if not parts or parts[0] not in ("/call", "/test_call"):
            raise ValueError("Unknown command")
        if parts[0] == "/test_call":
            if len(parts) != 4:
                raise ValueError("Expected /test_call <scenario> <name> <phone>")
            _, raw_scenario, name, phone = parts
            if not re.fullmatch(r"\+7\d{10}", phone):
                raise ValueError("Expected a Russian phone in +7XXXXXXXXXX format")
            scenario = Scenario(raw_scenario)
            client = Client(id=f"test:{message_id}", name=name, phone=phone)
            request = CallRequest(f"test:{message_id}", scenario, client, test=True,
                                  context={"visit": "test data", "extras": "test list"})
        else:
            if len(parts) != 3:
                raise ValueError("Expected /call <scenario> <client ID or phone>")
            _, raw_scenario, identifier = parts
            scenario = Scenario(raw_scenario)
            if not self.start_hour <= now.hour < self.end_hour:
                raise ValueError("Outside approved calling hours")
            client = self.crm.get_client(identifier)
            if not client or client.opted_out:
                raise ValueError("Client unavailable or opted out")
            request = CallRequest(f"manual:{message_id}", scenario, client)
        self.processed_commands.add(message_id)
        return self._perform(request, now)

    def _perform(self, request: CallRequest, now: datetime) -> CallResult:
        result = self.dialer.call(request)
        self.results.append((now, request, result))
        if not request.test:
            self.crm.save_call_result(request, result)
            if result.outcome != Outcome.NO_ANSWER:
                self.handled_events.add(self.event_key(request.client, request.scenario))
        marker = "ТЕСТ: " if request.test else ""
        extras = ", ".join(result.agreed_extras) or "нет"
        self.notifier.send(f"{marker}{request.scenario} · {request.client.name} "
                           f"(ID {request.client.id}) · {result.outcome} · "
                           f"доп. услуги согласованы: {extras}" +
                           (f" · причина: {result.reason}" if result.reason else ""))
        return result

    def report(self, start: datetime, end: datetime) -> dict[str, int]:
        rows = [(req, result) for at, req, result in self.results
                if start <= at < end and not req.test]
        return {
            "calls": len(rows),
            "conversations": sum(result.outcome != Outcome.NO_ANSWER for _, result in rows),
            "confirmations": sum(result.outcome == Outcome.CONFIRMED for _, result in rows),
            "requests": sum(result.outcome == Outcome.INTERESTED for _, result in rows),
            "bookings": sum(bool(result.crm_booking_id) for _, result in rows),
            "complaints": sum(result.outcome == Outcome.COMPLAINT for _, result in rows),
            "opt_outs": sum(result.outcome == Outcome.DECLINED for _, result in rows),
        }
