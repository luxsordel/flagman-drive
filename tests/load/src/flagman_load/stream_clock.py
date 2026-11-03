"""Telemetry delay on the staff stream, free of the clock difference with the server."""

from datetime import datetime


class StreamClock:
    """Measures how old the newest telemetry is when it reaches this socket.

    The load generator and the server do not share a clock (a Docker VM or a droplet may be seconds
    off), so the delay is counted on the server's time. Every message carries the time the server
    sent it: the smallest gap to the local receipt time is the clock difference plus the fastest
    delivery seen, so a backlog on the socket still shows up as delay.
    """

    def __init__(self) -> None:
        self._offset_s: float | None = None
        self._newest: datetime | None = None

    def observe(self, received_at: datetime, as_of: datetime) -> None:
        offset = (received_at - as_of).total_seconds()
        self._offset_s = offset if self._offset_s is None else min(self._offset_s, offset)

    def lag_ms(self, received_at: datetime, recorded: list[datetime]) -> float | None:
        """The delay of a message with new telemetry; None if it only repeats known telemetry.

        A reservation also announces its car, with the telemetry of a few seconds ago: that age
        is not a delay.
        """
        if not recorded or self._offset_s is None:
            return None
        newest = max(recorded)
        if self._newest is not None and newest <= self._newest:
            return None
        self._newest = newest
        lag_s = (received_at - newest).total_seconds() - self._offset_s
        return max(lag_s, 0.0) * 1000
