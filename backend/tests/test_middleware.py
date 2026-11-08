"""Unit tests for the fixed-window rate limiter used by RateLimitMiddleware."""

from collections import defaultdict

from app.middleware import RateLimitMiddleware, _Bucket


def make_limiter():
    return RateLimitMiddleware.__new__(RateLimitMiddleware)  # skip BaseHTTPMiddleware.__init__ (no app needed)


def test_rate_limit_allows_up_to_the_limit_then_blocks():
    limiter = make_limiter()
    buckets = defaultdict(_Bucket)
    for i in range(5):
        ok, _ = limiter._hit(buckets, "user-a", limit=5)
        assert ok, f"request {i} should be allowed"
    ok, retry_after = limiter._hit(buckets, "user-a", limit=5)
    assert not ok
    assert retry_after > 0


def test_rate_limit_is_isolated_per_key():
    limiter = make_limiter()
    buckets = defaultdict(_Bucket)
    for _ in range(5):
        assert limiter._hit(buckets, "user-a", limit=5)[0]
    # a different key must not be affected by user-a's usage
    assert limiter._hit(buckets, "user-b", limit=5)[0]


def test_rate_limit_window_resets(monkeypatch):
    limiter = make_limiter()
    buckets = defaultdict(_Bucket)
    current_time = [1000.0]
    monkeypatch.setattr("app.middleware.time.time", lambda: current_time[0])

    for _ in range(5):
        assert limiter._hit(buckets, "user-a", limit=5)[0]
    assert not limiter._hit(buckets, "user-a", limit=5)[0]

    current_time[0] += 61  # past the 60s window
    assert limiter._hit(buckets, "user-a", limit=5)[0]
