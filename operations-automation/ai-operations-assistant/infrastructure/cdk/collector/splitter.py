"""G.O.A.T. Network Agent — Traffic_Mirror_Collector per-VNI splitter.

This script runs on the single EC2 collector instance under systemd. It
listens for VXLAN-encapsulated mirrored traffic on UDP/4789 and writes one
pcap file per VNI per rotation window so the downstream uploader can map
each closed file to exactly one ``capture_id``.

Behavior matches Task 22 / Reqs 6.1-6.4:

  * Reads each frame's outer UDP/4789 + VXLAN header to extract the
    24-bit Virtual Network Identifier (VNI). Strips the outer Ethernet/
    IP/UDP/VXLAN headers and writes the inner Ethernet frame to a per-
    VNI rotating pcap file. This yields raw pcap files that look like
    they were captured directly off the source ENI.
  * Looks up the corresponding ``capture_id`` for each VNI in the
    Vni_Lookup_Table (DynamoDB). Frames whose VNI is not present in the
    table (e.g., because a session is being torn down or never existed)
    are dropped silently — this prevents stale sessions from polluting
    the pcap output and provides defense-in-depth against orphaned
    Traffic Mirror sessions in the same account that were not created
    by this Network Agent deployment.
  * Caches Vni_Lookup_Table reads in-memory with a 30-second TTL so that
    a steady-state session does not generate one DynamoDB read per pcap
    rotation. The cache is per-process and lazy-populated on first
    sight of a given VNI.
  * Rotates each VNI's pcap file at 100 MB or 60 seconds (whichever
    comes first) and retains at most 10 closed files per VNI in the
    local rotation directory before reclaiming disk by deleting the
    oldest. The companion uploader watches that directory with
    ``inotifywait`` and uploads files to S3 within 60 seconds of
    rotation. The 10-file cap leaves headroom for the uploader to
    complete an in-flight transfer without the splitter overwriting
    the target file.

Operational defaults (overridable via environment variables; see
``COLLECTOR_*`` constants below):

  * ``COLLECTOR_INTERFACE``         — Linux interface to capture on.
                                       Defaults to ``eth0``, the primary
                                       ENI underlay. The splitter captures
                                       VXLAN-encapsulated mirrored traffic
                                       with a BPF filter ``udp port 4789``
                                       so it sees the full outer headers
                                       including the VNI.
  * ``COLLECTOR_OUTPUT_DIR``        — Local directory for rotated pcaps.
                                       Defaults to ``/var/lib/goat-collector``.
                                       The systemd unit creates this
                                       directory at boot with
                                       ``ec2-user`` ownership.
  * ``VNI_LOOKUP_TABLE``            — DynamoDB table name for the
                                       VNI → ``capture_id`` mapping
                                       (matches the agent's
                                       ``ENV_VNI_LOOKUP_TABLE``).
  * ``VNI_LOOKUP_TTL_SECONDS``      — In-process cache TTL for
                                       Vni_Lookup_Table reads.
                                       Defaults to ``30`` per the
                                       design's "VNI to capture_id
                                       mapping" section.
  * ``ROTATION_BYTES``              — File rotation size threshold.
                                       Defaults to ``104857600``
                                       (100 MiB) per Req 6.2.
  * ``ROTATION_SECONDS``            — File rotation time threshold.
                                       Defaults to ``60`` per Req 6.2.
  * ``MAX_FILES_PER_VNI``           — Maximum closed pcap files per
                                       VNI in the rotation directory.
                                       Defaults to ``10`` per Req 6.2.

The script intentionally avoids exotic third-party libraries beyond
``scapy``, ``boto3``, and the Python standard library. ``scapy`` is the
canonical choice for VXLAN parsing in Python and is preferred over
``gopacket`` here because the AL2023 base image already includes a
modern Python runtime; bringing in Go for one collector helper would be
disproportionate to the demo footprint.
"""

from __future__ import annotations

import logging
import os
import signal
import struct
import sys
import threading
import time
from collections import OrderedDict, defaultdict
from typing import Any, Dict, Optional, Tuple

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError
from scapy.all import (  # type: ignore[import-untyped]
    AsyncSniffer as Sniffer,
    Ether,
    Packet,
    PcapWriter,
    UDP,
)
from scapy.layers.vxlan import VXLAN  # type: ignore[import-untyped]

# ---------------------------------------------------------------------------
# Logging — single STDERR stream so journald captures everything.
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=os.environ.get("COLLECTOR_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s collector[%(process)d] %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("goat.collector.splitter")


# ---------------------------------------------------------------------------
# Configuration constants — all overridable via environment variables.
# Defaults match the design's tuning values; operators should tune via
# environment, never by editing this file.
# ---------------------------------------------------------------------------

INTERFACE = os.environ.get("COLLECTOR_INTERFACE", "ens5")
BPF_FILTER = os.environ.get("COLLECTOR_BPF_FILTER", "udp port 4789")
OUTPUT_DIR = os.environ.get("COLLECTOR_OUTPUT_DIR", "/var/lib/goat-collector")
VNI_LOOKUP_TABLE = os.environ.get("VNI_LOOKUP_TABLE", "")
VNI_LOOKUP_TTL_SECONDS = float(os.environ.get("VNI_LOOKUP_TTL_SECONDS", "30"))
ROTATION_BYTES = int(os.environ.get("ROTATION_BYTES", str(100 * 1024 * 1024)))
ROTATION_SECONDS = float(os.environ.get("ROTATION_SECONDS", "60"))
MAX_FILES_PER_VNI = int(os.environ.get("MAX_FILES_PER_VNI", "10"))

# Sentinel value cached when a VNI lookup returned "no row". We still
# cache misses for the same TTL window to avoid hammering DynamoDB with
# the same negative lookup on every frame.
_VNI_LOOKUP_MISS = object()


# ---------------------------------------------------------------------------
# Vni_Lookup_Table cache
# ---------------------------------------------------------------------------


class VniLookupCache:
    """In-process TTL cache for ``vni → capture_id`` lookups.

    The cache is intentionally tiny (one entry per active VNI; the
    Capture_Concurrency_Limit caps the universe of active VNIs at
    ``5 captures × 3 ENIs = 15``). An ``OrderedDict`` is sufficient and
    keeps insertion order so the eviction loop is O(N) on the rare
    cache-cleanup path.
    """

    def __init__(self, table_name: str, ttl_seconds: float) -> None:
        if not table_name:
            raise RuntimeError(
                "VNI_LOOKUP_TABLE environment variable is required but not set"
            )
        self._table_name = table_name
        self._ttl_seconds = ttl_seconds
        self._cache: "OrderedDict[int, Tuple[float, Any]]" = OrderedDict()
        self._lock = threading.Lock()
        # Bounded timeouts + retries so a transient DynamoDB slowdown
        # cannot block frame ingestion indefinitely. Critically, the
        # call is also wrapped in broad exception handling in
        # ``_fetch_capture_id`` so a timeout NEVER propagates into the
        # scapy sniffer callback (which would close the listen socket
        # and silently stop all packet processing).
        self._client = boto3.client(
            "dynamodb",
            config=BotoConfig(
                connect_timeout=3,
                read_timeout=3,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )

    def lookup(self, vni: int) -> Optional[str]:
        """Return the ``capture_id`` for ``vni`` or ``None`` if absent.

        ``None`` covers two cases that callers do not need to
        distinguish: (a) the row was never present and (b) the row was
        present but the read returned a malformed item. In both cases
        the splitter drops the frame.
        """

        now = time.monotonic()

        with self._lock:
            entry = self._cache.get(vni)
            if entry is not None:
                expires_at, value = entry
                if expires_at > now:
                    return None if value is _VNI_LOOKUP_MISS else value
                # Expired — fall through to refresh.
                self._cache.pop(vni, None)

        # Miss or expired — refresh from DynamoDB. The lock is intentionally
        # released around the network call so a slow DynamoDB response does
        # not block frame ingestion for unrelated VNIs.
        capture_id = self._fetch_capture_id(vni)

        with self._lock:
            value: Any = capture_id if capture_id is not None else _VNI_LOOKUP_MISS
            self._cache[vni] = (now + self._ttl_seconds, value)
            # Soft cap so the cache cannot grow unboundedly under attack.
            while len(self._cache) > 1024:
                self._cache.popitem(last=False)

        return capture_id

    def _fetch_capture_id(self, vni: int) -> Optional[str]:
        try:
            response = self._client.get_item(
                TableName=self._table_name,
                Key={"vni": {"N": str(vni)}},
                ConsistentRead=False,
            )
        except (ClientError, BotoCoreError) as exc:
            # BotoCoreError covers connect/read timeouts and endpoint
            # connection failures (e.g. ConnectTimeoutError). These are
            # transient — treat as a cache miss (drop this frame) and
            # let the next lookup retry. Crucially we must NOT let this
            # propagate to the scapy callback, which would close the
            # sniffer socket and stop all capture.
            log.warning(
                "DynamoDB get_item failed for vni=%s table=%s: %s",
                vni,
                self._table_name,
                exc,
            )
            return None
        except Exception as exc:  # noqa: BLE001 — defensive catch-all
            # Any unexpected error (e.g. credential refresh failure) must
            # also be swallowed so the sniffer thread survives.
            log.warning(
                "Unexpected error during VNI lookup vni=%s table=%s: %s",
                vni,
                self._table_name,
                exc,
            )
            return None

        item = response.get("Item") or {}
        capture_id_attr = item.get("capture_id") or {}
        capture_id = capture_id_attr.get("S")
        if not isinstance(capture_id, str) or not capture_id:
            return None
        return capture_id


# ---------------------------------------------------------------------------
# Per-VNI rotating pcap writer
# ---------------------------------------------------------------------------


class RotatingPcapWriter:
    """Owns a single VNI's rotating pcap file pair.

    The writer keeps one open pcap file at a time. Rotation closes the
    current file (so ``inotifywait`` fires ``CLOSE_WRITE`` and the
    uploader picks it up) and opens a new one. After rotation, if the
    closed-file count for this VNI exceeds ``MAX_FILES_PER_VNI``, the
    oldest closed file is deleted so disk does not fill up if the
    uploader stalls.
    """

    def __init__(self, vni: int, capture_id: str) -> None:
        self.vni = vni
        self.capture_id = capture_id
        self._dir = os.path.join(OUTPUT_DIR, capture_id)
        os.makedirs(self._dir, mode=0o755, exist_ok=True)
        self._writer: Optional[PcapWriter] = None
        self._current_path: Optional[str] = None
        self._opened_at: float = 0.0
        self._bytes_written: int = 0

    def write(self, inner_frame: Packet) -> None:
        now = time.monotonic()
        if self._should_rotate(now):
            self._rotate(now)
        if self._writer is None:
            self._open_new_file(now)
        # ``scapy.PcapWriter.write`` is synchronous; wrap in try/except
        # so a single corrupt frame does not kill the splitter.
        try:
            self._writer.write(inner_frame)  # type: ignore[union-attr]
            # ``len(inner_frame)`` is an approximate accounting of bytes
            # written; the pcap record header adds a fixed overhead per
            # frame but counting wire bytes is a sufficient proxy for
            # rotation thresholds (Req 6.2 cares about file size, not
            # exact byte parity).
            self._bytes_written += len(inner_frame)
        except Exception as exc:  # pragma: no cover — defensive
            log.warning(
                "Failed to write frame for vni=%s capture_id=%s: %s",
                self.vni,
                self.capture_id,
                exc,
            )

    def close(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:  # pragma: no cover — defensive
                pass
            self._writer = None
            self._current_path = None
            self._bytes_written = 0

    # -- internals -----------------------------------------------------

    def _should_rotate(self, now: float) -> bool:
        if self._writer is None:
            return False
        if self._bytes_written >= ROTATION_BYTES:
            return True
        if (now - self._opened_at) >= ROTATION_SECONDS:
            return True
        return False

    def _rotate(self, now: float) -> None:
        self.close()
        self._enforce_file_cap()

    def _open_new_file(self, now: float) -> None:
        # Filename uses an ISO-like UTC timestamp (no slashes/colons)
        # so the uploader can use the basename directly as an S3 key
        # suffix.
        timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        filename = f"vni-{self.vni}-{timestamp}.pcap"
        path = os.path.join(self._dir, filename)
        # ``append=False`` to start a new pcap with a fresh global header.
        # ``sync=True`` would fsync each frame, which is more durability
        # than the demo needs and would hurt throughput; the default
        # buffered behavior is fine because rotation calls ``close()``
        # which flushes the OS buffer.
        self._writer = PcapWriter(path, append=False, sync=False)
        self._current_path = path
        self._opened_at = now
        self._bytes_written = 0
        log.debug(
            "Opened pcap vni=%s capture_id=%s path=%s",
            self.vni,
            self.capture_id,
            path,
        )

    def _enforce_file_cap(self) -> None:
        try:
            entries = sorted(
                (
                    os.path.join(self._dir, name)
                    for name in os.listdir(self._dir)
                    if name.endswith(".pcap")
                ),
                key=lambda p: os.path.getmtime(p),
            )
        except FileNotFoundError:
            return
        excess = len(entries) - MAX_FILES_PER_VNI
        for path in entries[: max(excess, 0)]:
            try:
                os.unlink(path)
                log.warning(
                    "Reclaimed disk: deleted oldest pcap %s (vni=%s)",
                    path,
                    self.vni,
                )
            except FileNotFoundError:
                pass


# ---------------------------------------------------------------------------
# Frame demux
# ---------------------------------------------------------------------------


class Demultiplexer:
    """Routes inner frames to per-VNI rotating writers."""

    def __init__(self, lookup: VniLookupCache) -> None:
        self._lookup = lookup
        self._writers: Dict[int, RotatingPcapWriter] = {}
        self._stats = defaultdict(int)
        self._lock = threading.Lock()

    def handle_frame(self, packet: Packet) -> None:
        """scapy ``prn`` callback — must NEVER raise.

        Any exception raised in a scapy ``prn`` callback propagates into
        the sniffer's recv loop and causes scapy to close the listen
        socket, silently halting all packet processing while the service
        still appears "active". We therefore swallow every exception here
        and only increment an error counter so the failure is visible in
        the periodic stats log without killing the sniffer.
        """
        try:
            self._handle_frame_impl(packet)
        except Exception as exc:  # noqa: BLE001 — protect the sniffer thread
            self._stats["frames_handler_errors"] += 1
            log.warning("handle_frame swallowed an exception: %s", exc)

    def _handle_frame_impl(self, packet: Packet) -> None:
        # The splitter captures on the UNDERLAY interface (``eth0``) with
        # a BPF filter ``udp port 4789``, so each packet is a full
        # Ethernet/IP/UDP/VXLAN-encapsulated mirrored frame. We extract
        # the VNI from the outer VXLAN header, then strip the outer
        # encapsulation to write only the inner Ethernet frame to the
        # per-VNI pcap file.
        vni = _extract_vni(packet)
        if vni is None:
            self._stats["frames_no_vni"] += 1
            return

        capture_id = self._lookup.lookup(vni)
        if capture_id is None:
            self._stats["frames_dropped_unknown_vni"] += 1
            return

        writer = self._writers.get(vni)
        if writer is None or writer.capture_id != capture_id:
            # New VNI or VNI whose mapping changed (e.g., capture
            # cleaned up + reused). Close the previous writer first
            # so its file rotates out cleanly.
            if writer is not None:
                writer.close()
            writer = RotatingPcapWriter(vni=vni, capture_id=capture_id)
            self._writers[vni] = writer

        # Strip outer-VXLAN bookkeeping if scapy parsed it. The "inner"
        # frame the writer wants is the Ethernet frame post-VXLAN-decap.
        inner = _inner_ethernet(packet)
        if inner is None:
            self._stats["frames_no_inner_eth"] += 1
            return
        writer.write(inner)
        self._stats["frames_written"] += 1

    def shutdown(self) -> None:
        with self._lock:
            for writer in self._writers.values():
                writer.close()
            self._writers.clear()

    def stats_snapshot(self) -> Dict[str, int]:
        return dict(self._stats)


def _extract_vni(packet: Packet) -> Optional[int]:
    """Return the VXLAN VNI for ``packet`` or ``None`` if unavailable.

    Kernel VXLAN devices expose the VNI two ways depending on how
    scapy is invoked:

      1. As a per-packet ``tun_id`` set on the VXLAN layer when scapy
         dissects the outer headers itself (common when the kernel
         interface is the underlay device, e.g. ``eth0`` listening for
         UDP/4789 traffic).
      2. As metadata on the ``packet`` object when the kernel has
         already stripped the outer headers and only the inner
         Ethernet frame plus a tunnel-id auxdata field is delivered.
    """

    if packet.haslayer(VXLAN):
        try:
            return int(packet[VXLAN].vni)
        except (AttributeError, TypeError):
            pass
    # Fall back to manual outer-header parsing for cases where scapy
    # cannot dissect the VXLAN layer (e.g., truncated frames).
    if packet.haslayer(UDP) and getattr(packet[UDP], "dport", None) == 4789:
        payload = bytes(packet[UDP].payload)
        if len(payload) >= 8:
            # VXLAN header layout (RFC 7348, 8 bytes):
            #   flags(1) reserved(3) vni(3) reserved(1)
            vni_bytes = payload[4:7]
            return int.from_bytes(vni_bytes, byteorder="big")
    return None


def _inner_ethernet(packet: Packet) -> Optional[Packet]:
    """Return the inner Ethernet frame from a VXLAN-encapsulated packet."""

    if packet.haslayer(VXLAN):
        try:
            return packet[VXLAN].payload  # type: ignore[no-any-return]
        except Exception:  # pragma: no cover — defensive
            return None
    if packet.haslayer(Ether):
        # Kernel-decapsulated packet — already an inner Ethernet frame.
        return packet
    return None


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def main() -> int:
    log.info(
        "starting splitter interface=%s filter=%s output_dir=%s table=%s ttl=%ss "
        "rotation_bytes=%s rotation_seconds=%s max_files=%s",
        INTERFACE,
        BPF_FILTER,
        OUTPUT_DIR,
        VNI_LOOKUP_TABLE,
        VNI_LOOKUP_TTL_SECONDS,
        ROTATION_BYTES,
        ROTATION_SECONDS,
        MAX_FILES_PER_VNI,
    )

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    lookup = VniLookupCache(
        table_name=VNI_LOOKUP_TABLE,
        ttl_seconds=VNI_LOOKUP_TTL_SECONDS,
    )
    demux = Demultiplexer(lookup=lookup)

    # ``scapy.AsyncSniffer`` (imported above as ``Sniffer``) runs the BPF
    # capture in a worker thread so the main thread can handle signals and
    # emit periodic stats logs. NOTE: scapy exposes the threaded sniffer as
    # ``AsyncSniffer``; there is no class named ``Sniffer``. The
    # ``store=False`` flag is critical: without it, scapy buffers every
    # frame in memory, which would OOM the t3.small under sustained load.
    #
    # We capture on the UNDERLAY interface (``eth0``) with a BPF filter
    # ``udp port 4789`` so scapy sees the full VXLAN encapsulation,
    # including the VNI in the outer header. Capturing on ``vxlan0`` would
    # only see kernel-decapsulated inner frames with the VNI stripped —
    # making it impossible to route frames to per-capture pcap files.
    sniffer = Sniffer(
        iface=INTERFACE,
        filter=BPF_FILTER,
        prn=demux.handle_frame,
        store=False,
    )

    stop = threading.Event()

    def _handle_signal(signum: int, _frame: Any) -> None:
        log.info("received signal %s, shutting down", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    sniffer.start()
    log.info("splitter ready")

    def _sniffer_alive() -> bool:
        """Best-effort check that the AsyncSniffer thread is still running."""
        try:
            if getattr(sniffer, "running", False):
                return True
            thread = getattr(sniffer, "thread", None)
            return bool(thread is not None and thread.is_alive())
        except Exception:  # pragma: no cover — defensive
            return False

    try:
        last_stats_at = time.monotonic()
        while not stop.is_set():
            stop.wait(timeout=5.0)
            now = time.monotonic()

            # Watchdog: if the sniffer thread has died (e.g. scapy closed
            # the listen socket after an unexpected error), restart it so
            # capture resumes instead of silently halting while the
            # service still reports "active". This is the safety net that
            # backs up the exception-swallowing in handle_frame.
            if not stop.is_set() and not _sniffer_alive():
                log.warning(
                    "sniffer thread is not alive — restarting it "
                    "(stats so far: %s)",
                    demux.stats_snapshot(),
                )
                try:
                    try:
                        sniffer.stop()
                    except Exception:  # pragma: no cover — defensive
                        pass
                    sniffer = Sniffer(
                        iface=INTERFACE,
                        filter=BPF_FILTER,
                        prn=demux.handle_frame,
                        store=False,
                    )
                    sniffer.start()
                    log.info("sniffer restarted")
                except Exception as exc:  # noqa: BLE001 — keep main loop alive
                    log.error("failed to restart sniffer: %s", exc)

            if now - last_stats_at >= 30.0:
                log.info("stats: %s", demux.stats_snapshot())
                last_stats_at = now
    finally:
        try:
            sniffer.stop()
        except Exception:  # pragma: no cover — defensive
            pass
        demux.shutdown()
        log.info("splitter exited cleanly stats=%s", demux.stats_snapshot())

    return 0


if __name__ == "__main__":  # pragma: no cover — entry point
    sys.exit(main())
