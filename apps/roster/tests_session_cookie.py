import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase


ROOT = Path(__file__).resolve().parents[2]


class RosterSessionCookieNameTests(SimpleTestCase):
    def cookie_name(self, extra_env):
        env = os.environ.copy()
        env.pop("ROSTER_SESSION_COOKIE_NAME", None)
        env.update(extra_env)
        env["DJANGO_SETTINGS_MODULE"] = "config.settings.development"
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import django; django.setup(); "
                "from django.conf import settings; "
                "print(settings.SESSION_COOKIE_NAME)",
            ],
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip().splitlines()[-1]

    def test_unset_name_stays_sessionid(self):
        self.assertEqual(self.cookie_name({}), "sessionid")

    def test_environment_name_is_used(self):
        self.assertEqual(
            self.cookie_name({"ROSTER_SESSION_COOKIE_NAME": "roster_sessionid"}),
            "roster_sessionid",
        )
