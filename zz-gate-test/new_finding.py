# THROWAWAY test fixture for the ASH gate experiment. Never merged.
# Expected: Bandit B310 (security-severity 8.0) -> a NEW high alert that should block.
import urllib.request


def fetch(url):
    return urllib.request.urlopen(url).read()
