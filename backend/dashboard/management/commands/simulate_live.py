"""
python manage.py simulate_live [--url http://127.0.0.1:8000] [--interval 1]

Needs the server running in another terminal. See dashboard/simulator.py.
"""
from django.core.management.base import BaseCommand, CommandError

from dashboard import simulator


class Command(BaseCommand):
    help = "Simulate phones driving into a traffic jam, sending live GPS and reports to a running server."

    def add_arguments(self, parser):
        parser.add_argument("--url", default="http://127.0.0.1:8000", help="Server address.")
        parser.add_argument("--interval", type=float, default=1.0,
                            help="Real seconds per simulated minute (default 1; use 0 for instant).")
        parser.add_argument("--employer-user", default="demo_employer")
        parser.add_argument("--employer-pass", default=simulator.PASSWORD)
        parser.add_argument("--authority-user", default="demo_authority")
        parser.add_argument("--authority-pass", default=simulator.PASSWORD)

    def handle(self, *args, **o):
        self.stdout.write("Open the employer page in your browser now; it refreshes by itself.\n")
        try:
            simulator.run(
                o["url"], interval=o["interval"],
                employer=(o["employer_user"], o["employer_pass"]),
                authority=(o["authority_user"], o["authority_pass"]),
                log=self.stdout.write,
            )
        except simulator.SimulationError as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS("Simulation finished."))
