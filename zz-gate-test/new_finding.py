# THROWAWAY fixture for the fingerprint test. Never merged.
# Expected: Bandit B310 -> the ONLY new alert on the PR.
import urllib.request


def fetch(url):
    return urllib.request.urlopen(url).read()
