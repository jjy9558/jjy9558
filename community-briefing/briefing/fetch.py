"""Polite HTTP fetching: User-Agent, per-host delay, ETag/Last-Modified cache, optional robots.txt."""
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
import urllib.robotparser
from urllib.parse import urlsplit


class FetchError(Exception):
    pass


def decode(data: bytes) -> str:
    """Decode HTML bytes; Korean sites are often UTF-8 or CP949 (EUC-KR)."""
    for enc in ("utf-8", "cp949"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


class Fetcher:
    def __init__(self, user_agent, delay=2.0, timeout=20, cache_dir=None,
                 respect_robots=True, opener=None, sleep=time.sleep, clock=time.monotonic):
        self.user_agent, self.delay, self.timeout = user_agent, delay, timeout
        self.cache_dir, self.respect_robots = cache_dir, respect_robots
        self.opener = opener or urllib.request.urlopen
        self.sleep, self.clock = sleep, clock
        self._last = {}    # host -> last request time
        self._robots = {}  # host -> RobotFileParser | None

    def _wait(self, host, delay):
        delay = self.delay if delay is None else delay
        last = self._last.get(host)
        if last is not None:
            remaining = last + delay - self.clock()
            if remaining > 0:
                self.sleep(remaining)
        self._last[host] = self.clock()

    def _cache_paths(self, url):
        key = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return os.path.join(self.cache_dir, key + ".body"), os.path.join(self.cache_dir, key + ".json")

    def allowed(self, url):
        parts = urlsplit(url)
        host = parts.scheme + "://" + parts.netloc
        if host not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                req = urllib.request.Request(host + "/robots.txt", headers={"User-Agent": self.user_agent})
                with self.opener(req, timeout=self.timeout) as resp:
                    rp.parse(decode(resp.read()).splitlines())
            except Exception:
                rp = None  # robots.txt missing/unreachable -> allow
            self._robots[host] = rp
        rp = self._robots[host]
        return rp is None or rp.can_fetch(self.user_agent, url)

    def get(self, url, delay=None, check_robots=False) -> bytes:
        if check_robots and self.respect_robots and not self.allowed(url):
            raise FetchError("robots.txt disallows " + url)
        headers = {"User-Agent": self.user_agent}
        body_path = meta_path = None
        meta = {}
        if self.cache_dir:
            os.makedirs(self.cache_dir, exist_ok=True)
            body_path, meta_path = self._cache_paths(url)
            if os.path.exists(meta_path) and os.path.exists(body_path):
                with open(meta_path, encoding="utf-8") as f:
                    meta = json.load(f)
                if meta.get("etag"):
                    headers["If-None-Match"] = meta["etag"]
                if meta.get("last_modified"):
                    headers["If-Modified-Since"] = meta["last_modified"]
        self._wait(urlsplit(url).netloc, delay)
        try:
            with self.opener(urllib.request.Request(url, headers=headers), timeout=self.timeout) as resp:
                data = resp.read()
                etag, modified = resp.headers.get("ETag"), resp.headers.get("Last-Modified")
        except urllib.error.HTTPError as e:
            if e.code == 304 and body_path:
                with open(body_path, "rb") as f:
                    return f.read()
            raise FetchError("HTTP %s for %s" % (e.code, url))
        except Exception as e:  # URLError, timeout, ...
            raise FetchError("%s: %s" % (url, e))
        if body_path:
            with open(body_path, "wb") as f:
                f.write(data)
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump({"url": url, "etag": etag, "last_modified": modified}, f)
        return data
