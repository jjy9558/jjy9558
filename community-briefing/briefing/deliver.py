"""Optional delivery: ntfy push notification (https://ntfy.sh or self-hosted)."""
import urllib.request
from urllib.parse import quote, urlencode


def ntfy_send(server, topic, title, message, token="", click=None, opener=urllib.request.urlopen):
    # Title goes in the query string: HTTP headers cannot carry Korean (non-latin-1) text.
    params = {"title": title, "markdown": "yes"}
    if click:
        params["click"] = click
    url = "%s/%s?%s" % (server.rstrip("/"), quote(topic), urlencode(params))
    headers = {"Authorization": "Bearer " + token} if token else {}
    req = urllib.request.Request(url, data=message.encode("utf-8"), headers=headers, method="POST")
    with opener(req, timeout=15) as resp:
        return getattr(resp, "status", 200)
