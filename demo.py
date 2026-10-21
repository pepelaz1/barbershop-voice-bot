"""Synthetic offline demonstration; no live calls or messages."""
from datetime import datetime, timedelta
from bot import BotService, CallResult, Client, Outcome


class DemoCrm:
    def __init__(self, clients):
        self.clients = {c.id: c for c in clients}
        self.saved = []

    def get_client(self, identifier):
        return self.clients.get(identifier) or next(
            (c for c in self.clients.values() if c.phone == identifier), None)

    def save_call_result(self, request, result):
        self.saved.append((request, result))


class DemoDialer:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or CallResult(Outcome.COMPLETED)

    def call(self, request):
        self.calls.append(request)
        return self.result


class DemoNotifier:
    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)


if __name__ == "__main__":
    now = datetime(2026, 10, 21, 12, 0)
    crm = DemoCrm([
        Client("C-101", "Александр", "+79990000001", next_booking=now + timedelta(minutes=45),
               next_booking_id="B-101"),
        Client("C-102", "Мария", "+79990000002", last_visit=now - timedelta(days=40)),
    ])
    dialer, notifier = DemoDialer(), DemoNotifier()
    service = BotService(crm, dialer, notifier, {"admin-1"}, start_hour=10, end_hour=20)
    for client_id in crm.clients:
        service.run_scheduled(client_id, now)
    service.handle_command("admin-1", "M-1", "/test_call 40дней Сергей +79990000003", now)
    print("Звонки (симуляция):", len(dialer.calls))
    print("Изменения CRM (симуляция):", len(crm.saved))
    print("Уведомления:")
    print(*notifier.messages, sep="\n")
    print("Отчёт:", service.report(now - timedelta(days=1), now + timedelta(days=1)))
