# THROWAWAY fixture for the ASH gate filter test. Never merged. Not real secrets.
import subprocess  # Bandit B404, low: expected DROPPED
import urllib.request

db_password = "not-a-real-password-gate-test"  # detect-secrets keyword: expected KEPT if scored high


def fetch(url):
    return urllib.request.urlopen(url).read()  # Bandit B310, 8.0: expected KEPT


def run(cmd):
    return subprocess.run(cmd, check=True)
