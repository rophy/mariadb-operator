#!/usr/bin/env python3
"""
MariaDB Failover Test Client

This client continuously writes records with an increasing sequence number
to test failover behavior using idempotent UPSERT operations. It uses hybrid
attempt tracking to observe both client-side and database-side retry behavior.

Tracking metrics:
- Write successes and failures
- Connection errors and reconnections
- Write latency
- Hybrid attempt tracking:
  * attempt_count: Total client attempts for each sequence (including failures)
  * upsert_count: Number of successful UPSERTs (1=insert, 2+=duplicate handling)

The client uses UPSERT (INSERT ... ON DUPLICATE KEY UPDATE) to ensure:
- No duplicate sequences even if write succeeds but client doesn't see response
- Failed writes are retried with the same sequence number
- Data consistency is guaranteed under extreme failover conditions
- Full observability of retry behavior during failover events

Usage:
    python main.py --host mariadb-cluster --port 3306 --user root --password secret

Environment variables:
    MARIADB_HOST: Database hostname
    MARIADB_PORT: Database port (default: 3306)
    MARIADB_USER: Database username (default: root)
    MARIADB_PASSWORD: Database password
    MARIADB_DATABASE: Database name (default: test)
    WRITE_INTERVAL: Seconds between writes (default: 1.0)
    CLIENT_ID: Unique client identifier (default: hostname)
"""

import argparse
import logging
import os
import socket
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import mysql.connector
from mysql.connector import Error as MySQLError

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


@dataclass
class WriteResult:
    """Result of a write operation"""
    sequence: int
    success: bool
    latency_ms: float
    error: Optional[str] = None
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


class MariaDBClient:
    """MariaDB client that continuously writes with increasing sequence"""

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        client_id: str,
        write_interval: float = 1.0
    ):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.client_id = client_id
        self.write_interval = write_interval

        self.connection: Optional[mysql.connector.MySQLConnection] = None
        self.current_sequence = 0
        self.current_sequence_attempts = 0  # Track attempts for current sequence
        self.total_writes = 0
        self.successful_writes = 0
        self.failed_writes = 0
        self.connection_errors = 0
        self.last_success_time: Optional[datetime] = None
        self.last_error_time: Optional[datetime] = None

    def connect(self) -> bool:
        """Establish connection to MariaDB"""
        try:
            logger.info(f"Connecting to MariaDB at {self.host}:{self.port} as {self.user}")
            self.connection = mysql.connector.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=self.database,
                autocommit=True,
                connection_timeout=5,
                # Do NOT use connection pooling - it causes pool exhaustion during failovers
                # Each reconnection should get a fresh connection
            )
            logger.info(f"Successfully connected to MariaDB (server version: {self.connection.get_server_info()})")
            return True
        except MySQLError as e:
            logger.error(f"Failed to connect to MariaDB: {e}")
            self.connection_errors += 1
            return False

    def disconnect(self):
        """Close connection to MariaDB"""
        if self.connection and self.connection.is_connected():
            self.connection.close()
            logger.info("Disconnected from MariaDB")

    def ensure_connection(self) -> bool:
        """Ensure connection is alive, reconnect if necessary"""
        if self.connection and self.connection.is_connected():
            return True

        logger.warning("Connection lost, attempting to reconnect...")
        return self.connect()

    def initialize_table(self) -> bool:
        """Create the test table if it doesn't exist"""
        try:
            if not self.ensure_connection():
                return False

            cursor = self.connection.cursor()

            # Create table with composite primary key (client_id, sequence) for idempotency
            # This ensures no duplicate sequences per client, enabling safe UPSERT operations
            #
            # Hybrid tracking:
            # - attempt_count: Total client attempts for this sequence (from client memory)
            # - upsert_count: Number of times UPSERT touched this row (1=insert, 2+=update)
            create_table_query = """
                CREATE TABLE IF NOT EXISTS failover_test (
                    client_id VARCHAR(255) NOT NULL,
                    sequence BIGINT NOT NULL,
                    write_timestamp DATETIME(6) NOT NULL,
                    hostname VARCHAR(255),
                    attempt_count INT DEFAULT 1,
                    upsert_count INT DEFAULT 1,
                    first_attempt_ts DATETIME(6),
                    last_update_ts DATETIME(6),
                    PRIMARY KEY (client_id, sequence),
                    INDEX idx_timestamp (write_timestamp)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
            cursor.execute(create_table_query)
            logger.info("Table 'failover_test' initialized with idempotent schema")

            # Get the maximum sequence for this client to resume from
            cursor.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM failover_test WHERE client_id = %s",
                (self.client_id,)
            )
            max_seq = cursor.fetchone()[0]
            self.current_sequence = max_seq
            logger.info(f"Resuming from sequence {self.current_sequence}")

            cursor.close()
            return True

        except MySQLError as e:
            logger.error(f"Failed to initialize table: {e}")
            return False

    def write_record(self) -> WriteResult:
        """Write using idempotent UPSERT with hybrid attempt tracking"""
        self.total_writes += 1
        self.current_sequence_attempts += 1  # Increment on every attempt (including failures)
        start_time = time.time()

        try:
            if not self.ensure_connection():
                # Connection failed, will retry same sequence in next loop iteration
                # attempt_count stays incremented for next try
                latency_ms = (time.time() - start_time) * 1000
                self.failed_writes += 1
                self.last_error_time = datetime.now()
                return WriteResult(
                    sequence=self.current_sequence,
                    success=False,
                    latency_ms=latency_ms,
                    error="Connection lost"
                )

            cursor = self.connection.cursor()

            # UPSERT with hybrid tracking:
            # - attempt_count: From client memory (total attempts including failures)
            # - upsert_count: Managed by DB (1 on insert, increments on duplicate)
            upsert_query = """
                INSERT INTO failover_test
                    (client_id, sequence, write_timestamp, hostname,
                     attempt_count, upsert_count, first_attempt_ts, last_update_ts)
                VALUES (%s, %s, NOW(6), %s, %s, 1, NOW(6), NOW(6))
                ON DUPLICATE KEY UPDATE
                    attempt_count = VALUES(attempt_count),
                    upsert_count = upsert_count + 1,
                    last_update_ts = NOW(6),
                    hostname = VALUES(hostname)
            """

            cursor.execute(upsert_query, (
                self.client_id,
                self.current_sequence,
                self.get_current_primary_host(),
                self.current_sequence_attempts  # Pass in-memory attempt count
            ))

            latency_ms = (time.time() - start_time) * 1000
            cursor.close()

            self.successful_writes += 1
            self.last_success_time = datetime.now()

            logger.info(
                f"✓ Wrote sequence {self.current_sequence} "
                f"(attempts: {self.current_sequence_attempts}, latency: {latency_ms:.2f}ms, "
                f"success_rate: {self.get_success_rate():.1f}%)"
            )

            # Success! Move to next sequence and reset attempt counter
            self.current_sequence += 1
            self.current_sequence_attempts = 0

            return WriteResult(
                sequence=self.current_sequence - 1,  # Return the sequence we just wrote
                success=True,
                latency_ms=latency_ms
            )

        except MySQLError as e:
            latency_ms = (time.time() - start_time) * 1000
            self.failed_writes += 1
            self.last_error_time = datetime.now()

            error_msg = str(e)
            logger.warning(
                f"⚠ Failed sequence {self.current_sequence} (attempt {self.current_sequence_attempts}): "
                f"{error_msg} (latency: {latency_ms:.2f}ms, will retry)"
            )

            # Check if this is a read-only error (failover in progress)
            if "read-only" in error_msg.lower() or "read only" in error_msg.lower():
                logger.warning("⚠ Database is read-only - failover may be in progress!")

            # Don't increment sequence - will retry same sequence in next loop iteration
            # attempt_count already incremented above, will be higher on next try
            return WriteResult(
                sequence=self.current_sequence,
                success=False,
                latency_ms=latency_ms,
                error=error_msg
            )

    def get_current_primary_host(self) -> Optional[str]:
        """Get the hostname of the current primary server"""
        try:
            cursor = self.connection.cursor()
            cursor.execute("SELECT @@hostname")
            result = cursor.fetchone()
            cursor.close()
            return result[0] if result else None
        except MySQLError:
            return None

    def get_success_rate(self) -> float:
        """Calculate write success rate"""
        if self.total_writes == 0:
            return 100.0
        return (self.successful_writes / self.total_writes) * 100

    def print_stats(self):
        """Print current statistics"""
        logger.info("=" * 60)
        logger.info(f"Client Statistics (ID: {self.client_id})")
        logger.info("=" * 60)
        logger.info(f"Current Sequence:     {self.current_sequence}")
        logger.info(f"Total Write Attempts: {self.total_writes}")
        logger.info(f"Successful Writes:    {self.successful_writes}")
        logger.info(f"Failed Writes:        {self.failed_writes}")
        logger.info(f"Connection Errors:    {self.connection_errors}")
        logger.info(f"Success Rate:         {self.get_success_rate():.2f}%")

        if self.last_success_time:
            logger.info(f"Last Success:         {self.last_success_time}")
        if self.last_error_time:
            logger.info(f"Last Error:           {self.last_error_time}")

        logger.info("=" * 60)

    def run(self):
        """Main loop - continuously write records"""
        logger.info(f"Starting failover test client (ID: {self.client_id})")
        logger.info(f"Write interval: {self.write_interval}s")

        # Initialize connection and table
        if not self.connect():
            logger.error("Failed to establish initial connection. Exiting.")
            sys.exit(1)

        if not self.initialize_table():
            logger.error("Failed to initialize table. Exiting.")
            sys.exit(1)

        # Print stats every N writes
        stats_interval = 10
        next_stats_print = stats_interval

        try:
            while True:
                # Write record
                result = self.write_record()

                # Print stats periodically
                if self.total_writes >= next_stats_print:
                    self.print_stats()
                    next_stats_print += stats_interval

                # Wait before next write
                time.sleep(self.write_interval)

        except KeyboardInterrupt:
            logger.info("\nReceived interrupt signal, shutting down...")
            self.print_stats()
        finally:
            self.disconnect()


def get_default_client_id() -> str:
    """Generate default client ID from hostname"""
    try:
        hostname = socket.gethostname()
        return f"client-{hostname}"
    except Exception:
        return f"client-{os.getpid()}"


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description='MariaDB Failover Test Client')

    parser.add_argument(
        '--host',
        default=os.getenv('MARIADB_HOST', 'localhost'),
        help='MariaDB host (default: localhost or MARIADB_HOST env var)'
    )
    parser.add_argument(
        '--port',
        type=int,
        default=int(os.getenv('MARIADB_PORT', '3306')),
        help='MariaDB port (default: 3306 or MARIADB_PORT env var)'
    )
    parser.add_argument(
        '--user',
        default=os.getenv('MARIADB_USER', 'root'),
        help='MariaDB user (default: root or MARIADB_USER env var)'
    )
    parser.add_argument(
        '--password',
        default=os.getenv('MARIADB_PASSWORD', ''),
        help='MariaDB password (default: MARIADB_PASSWORD env var)'
    )
    parser.add_argument(
        '--database',
        default=os.getenv('MARIADB_DATABASE', 'test'),
        help='MariaDB database (default: test or MARIADB_DATABASE env var)'
    )
    parser.add_argument(
        '--client-id',
        default=os.getenv('CLIENT_ID', get_default_client_id()),
        help='Unique client identifier (default: hostname or CLIENT_ID env var)'
    )
    parser.add_argument(
        '--write-interval',
        type=float,
        default=float(os.getenv('WRITE_INTERVAL', '1.0')),
        help='Seconds between writes (default: 1.0 or WRITE_INTERVAL env var)'
    )

    args = parser.parse_args()

    # Validate required parameters
    if not args.password:
        logger.error("Password is required (use --password or MARIADB_PASSWORD env var)")
        sys.exit(1)

    # Create and run client
    client = MariaDBClient(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        database=args.database,
        client_id=args.client_id,
        write_interval=args.write_interval
    )

    client.run()


if __name__ == '__main__':
    main()
