import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot import BotService, CallResult, Client, Outcome, Scenario
from demo import DemoCrm, DemoDialer, DemoNotifier


NOW = datetime(2026, 10, 21, 12)


def make_service(clients, outcome=Outcome.COMPLETED):
    crm = DemoCrm(clients)
    dialer = DemoDialer(CallResult(outcome))
    notifier = DemoNotifier()
    return BotService(crm, dialer, notifier, {"admin"}, start_hour=9, end_hour=20), crm, dialer, notifier


class BotTests(unittest.TestCase):
    def test_five_scenarios_and_priority(self):
        scenarios = [
            (Client("1", "A", "+79990000001", next_booking=NOW + timedelta(minutes=30), next_booking_id="B"), Scenario.CONFIRM),
            (Client("2", "B", "+79990000002", last_visit=NOW - timedelta(days=7)), Scenario.FEEDBACK),
            (Client("3", "C", "+79990000003", last_visit=NOW - timedelta(days=40)), Scenario.REMINDER),
            (Client("4", "D", "+79990000004", last_visit=NOW - timedelta(days=90)), Scenario.RETURN),
            (Client("5", "E", "+79990000005", lost=True), Scenario.LOST),
        ]
        for client, expected in scenarios:
            self.assertEqual(BotService.choose_scenario(client, NOW), expected)
        self.assertIsNone(BotService.choose_scenario(
            Client("6", "F", "+79990000006", last_visit=NOW - timedelta(days=100),
                   next_booking=NOW + timedelta(days=3)), NOW))

    def test_opt_out_hours_and_duplicate(self):
        c = Client("1", "A", "+79990000001", last_visit=NOW - timedelta(days=40))
        service, crm, dialer, _ = make_service([c])
        self.assertIsNone(service.run_scheduled("1", NOW.replace(hour=8)))
        self.assertIsNotNone(service.run_scheduled("1", NOW))
        self.assertIsNone(service.run_scheduled("1", NOW))
        self.assertEqual(len(dialer.calls), 1)
        crm.clients["1"] = Client("1", "A", "+79990000001", last_visit=c.last_visit, opted_out=True)
        self.assertIsNone(service.run_scheduled("1", NOW))

    def test_test_command_isolated_and_idempotent(self):
        service, crm, dialer, notifier = make_service([])
        cmd = "/test_call 40дней Александр +79990000001"
        with self.assertRaises(PermissionError):
            service.handle_command("outsider", "m1", cmd, NOW)
        service.handle_command("admin", "m1", cmd, NOW)
        self.assertIsNone(service.handle_command("admin", "m1", cmd, NOW))
        service.handle_command("admin", "m2", cmd, NOW)
        self.assertEqual(len(dialer.calls), 2)
        self.assertEqual(crm.saved, [])
        self.assertEqual(service.report(NOW - timedelta(days=1), NOW + timedelta(days=1))["calls"], 0)
        self.assertTrue(all(message.startswith("ТЕСТ:") for message in notifier.messages))

    def test_manual_call_checks_opt_out(self):
        c = Client("1", "A", "+79990000001", opted_out=True)
        service, _, _, _ = make_service([c])
        with self.assertRaises(ValueError):
            service.handle_command("admin", "m1", "/call 40дней 1", NOW)

    def test_no_answer_retry_limit(self):
        c = Client("1", "A", "+79990000001", last_visit=NOW - timedelta(days=40))
        service, _, dialer, _ = make_service([c], Outcome.NO_ANSWER)
        for _ in range(3):
            service.run_scheduled("1", NOW)
        self.assertEqual(len(dialer.calls), 2)


if __name__ == "__main__":
    unittest.main()
