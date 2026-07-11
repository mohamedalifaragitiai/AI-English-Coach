"""Rate limiting middleware using sliding window algorithm.

Protects API endpoints from abuse while allowing burst traffic.
"""

import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from backend.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class RateLimitConfig:
    """Rate limit configuration."""

    requests_per_minute: int = 60
    requests_per_hour: int = 1000
    burst_size: int = 10
    websocket_connections_per_user: int = 2


class SlidingWindowCounter:
    """Sliding window rate limiter using sub-windows.

    More accurate than fixed window, less memory than sliding log.
    """

    def __init__(self, window_seconds: int, max_requests: int) -> None:
        """Initialize counter.

        Args:
            window_seconds: Window size in seconds
            max_requests: Maximum requests per window
        """
        self.window_seconds = window_seconds
        self.max_requests = max_requests
        self.sub_window_seconds = max(1, window_seconds // 10)
        self.num_sub_windows = window_seconds // self.sub_window_seconds

        # key -> {sub_window_id -> count}
        self._counters: dict[str, dict[int, int]] = defaultdict(dict)
        self._last_cleanup = time.time()

    def is_allowed(self, key: str) -> tuple[bool, int]:
        """Check if request is allowed.

        Args:
            key: Rate limit key (e.g., IP address or user ID)

        Returns:
            Tuple of (allowed, remaining_requests)
        """
        now = time.time()
        current_window = int(now // self.sub_window_seconds)

        # Cleanup old entries periodically
        if now - self._last_cleanup > 60:
            self._cleanup(current_window)
            self._last_cleanup = now

        # Count requests in sliding window
        counters = self._counters[key]
        total = 0

        for window_id in range(current_window - self.num_sub_windows + 1, current_window + 1):
            total += counters.get(window_id, 0)

        if total >= self.max_requests:
            return False, 0

        # Increment counter for current sub-window
        counters[current_window] = counters.get(current_window, 0) + 1

        remaining = max(0, self.max_requests - total - 1)
        return True, remaining

    def _cleanup(self, current_window: int) -> None:
        """Remove old sub-window entries."""
        cutoff = current_window - self.num_sub_windows - 1

        for key in list(self._counters.keys()):
            counters = self._counters[key]
            old_windows = [w for w in counters if w < cutoff]
            for w in old_windows:
                del counters[w]
            if not counters:
                del self._counters[key]


class RateLimiter:
    """Rate limiter with multiple time windows."""

    def __init__(self, config: RateLimitConfig | None = None) -> None:
        """Initialize rate limiter.

        Args:
            config: Rate limit configuration
        """
        self.config = config or RateLimitConfig()

        self.minute_limiter = SlidingWindowCounter(60, self.config.requests_per_minute)
        self.hour_limiter = SlidingWindowCounter(3600, self.config.requests_per_hour)

        # Track WebSocket connections per user
        self._ws_connections: dict[str, int] = defaultdict(int)

    def check_request(self, key: str) -> tuple[bool, dict[str, int]]:
        """Check if a request should be allowed.

        Args:
            key: Rate limit key

        Returns:
            Tuple of (allowed, headers_dict)
        """
        minute_allowed, minute_remaining = self.minute_limiter.is_allowed(key)
        hour_allowed, hour_remaining = self.hour_limiter.is_allowed(key)

        allowed = minute_allowed and hour_allowed

        headers = {
            "X-RateLimit-Limit-Minute": str(self.config.requests_per_minute),
            "X-RateLimit-Remaining-Minute": str(minute_remaining),
            "X-RateLimit-Limit-Hour": str(self.config.requests_per_hour),
            "X-RateLimit-Remaining-Hour": str(hour_remaining),
        }

        if not allowed:
            retry_after = 60 if not minute_allowed else 3600
            headers["Retry-After"] = str(retry_after)

        return allowed, headers

    def check_websocket(self, user_id: str) -> bool:
        """Check if a WebSocket connection should be allowed.

        Args:
            user_id: User ID

        Returns:
            True if connection is allowed
        """
        current = self._ws_connections.get(user_id, 0)
        if current >= self.config.websocket_connections_per_user:
            return False
        self._ws_connections[user_id] = current + 1
        return True

    def release_websocket(self, user_id: str) -> None:
        """Release a WebSocket connection.

        Args:
            user_id: User ID
        """
        if user_id in self._ws_connections:
            self._ws_connections[user_id] = max(0, self._ws_connections[user_id] - 1)
            if self._ws_connections[user_id] == 0:
                del self._ws_connections[user_id]


class RateLimitMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware for rate limiting."""

    def __init__(
        self,
        app,
        rate_limiter: RateLimiter | None = None,
        key_func: Callable[[Request], str] | None = None,
        exclude_paths: list[str] | None = None,
    ) -> None:
        """Initialize middleware.

        Args:
            app: FastAPI app
            rate_limiter: Rate limiter instance
            key_func: Function to extract rate limit key from request
            exclude_paths: Paths to exclude from rate limiting
        """
        super().__init__(app)
        self.rate_limiter = rate_limiter or RateLimiter()
        self.key_func = key_func or self._default_key
        self.exclude_paths = exclude_paths or ["/health", "/metrics"]

    def _default_key(self, request: Request) -> str:
        """Default key function using client IP."""
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next) -> Response:
        """Process request with rate limiting."""
        # Skip rate limiting for excluded paths
        if request.url.path in self.exclude_paths:
            return await call_next(request)

        # Skip WebSocket requests (handled separately)
        if request.headers.get("upgrade", "").lower() == "websocket":
            return await call_next(request)

        key = self.key_func(request)
        allowed, headers = self.rate_limiter.check_request(key)

        if not allowed:
            logger.warning(
                "rate_limit_exceeded",
                key=key,
                path=request.url.path,
            )
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Please try again later."},
                headers=headers,
            )

        response = await call_next(request)

        # Add rate limit headers to response
        for header, value in headers.items():
            response.headers[header] = value

        return response


# Global rate limiter instance
_rate_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    """Get the global rate limiter instance."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter


def init_rate_limiter(config: RateLimitConfig | None = None) -> RateLimiter:
    """Initialize the global rate limiter.

    Args:
        config: Rate limit configuration

    Returns:
        Rate limiter instance
    """
    global _rate_limiter
    _rate_limiter = RateLimiter(config)
    return _rate_limiter
