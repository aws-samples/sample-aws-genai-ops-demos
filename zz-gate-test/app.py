# THROWAWAY fixture for the ASH gate filter test. Never merged. Not real secrets.
import subprocess  # Bandit B404, low: expected DROPPED
import urllib.request

db_password = "not-a-real-password-gate-test"  # detect-secrets keyword: expected KEPT if scored high


def fetch(url):
    return urllib.request.urlopen(url).read()  # nosec B310 - test override


def run(cmd):
    return subprocess.run(cmd, check=True)
