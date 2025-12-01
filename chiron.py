"""
chiron.py
Helper module for reused functions
"""
from __future__ import annotations

import re
import sys
import binascii
from functools import partial

from datetime import datetime, timezone
import pathlib

from sqlalchemy import create_engine, Integer, String, DateTime, update
from sqlalchemy.orm import DeclarativeBase, Session, Mapped, mapped_column
from sqlalchemy.types import BLOB

def hexid(b: bytes) -> str:
	return binascii.hexlify(b).decode("ascii")

def unhexid(s: str) -> bytes:
	try:
		b = binascii.unhexlify(s)
		if len(b) != 16:
			raise ValueError("Invalid item ID length")
		return b
	except Exception:
		raise ValueError("Invalid item ID")

def get_secret(db_dir: pathlib.Path, regen: bool) -> str:
	secret_file = db_dir.joinpath('rhodium_secret')
	
	if regen or not secret_file.exists():
		import secrets
		secret = secrets.token_hex(32)
		
		try:
			secret_file.parent.mkdir(parents=True, exist_ok=True)
			secret_file.write_text(secret)
			secret_file.chmod(0o600)
			return secret
		except (PermissionError, OSError) as e:
			print(f"FATAL: Cannot write secret: {e}", file=sys.stderr)
			sys.exit(1)
	
	try:
		secret = secret_file.read_text().strip()
		
		# Validate: exactly 64 hexadecimal characters
		if not re.match(r'^[0-9a-f]{64}$', secret):
			print(f"FATAL: Secret file corrupted (invalid format)", file=sys.stderr)
			print(f"  Expected: 64 hex characters, got: {len(secret)} chars", file=sys.stderr)
			print(f"  Delete {secret_file} and restart to regenerate", file=sys.stderr)
			sys.exit(1)
		
		return secret
		
	except (PermissionError, OSError) as e:
		print(f"FATAL: Cannot read secret: {e}", file=sys.stderr)
		sys.exit(1)

def generate_item_id(session: Session) -> bytes:
	"""
	Generates a 16-byte monotonic ID with structure:
	  [56-bit timestamp_ms][16-bit mac-hash][56-bit counter]

	All values are stored big-endian and concatenated to 16 bytes.
	"""

	# 1. Monotonic ms timestamp (56 bits)
	ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
	ts_bytes = ts_ms.to_bytes(7, "big")   # 56 bits / 7 bytes

	# 2. 16-bit MAC-hash fragment
	row = session.get(SysParameters, "node_id")
	if row is None:
		raise RuntimeError("Database missing SysParameters.node_id")
	node_id_hex = row.value				# e.g. "ab45"
	mac_bytes = bytes.fromhex(node_id_hex) # 2 bytes

	# 3. Increment 56-bit counter
	ctr_row = session.get(Counters, "primary_counter")
	if ctr_row is None:
		raise RuntimeError("Database missing Counters.primary_counter")

	new_value = ctr_row.value + 1
	session.execute(
		update(Counters)
		.where(Counters.key == "primary_counter")
		.values(value=new_value, last_change=datetime.now(timezone.utc))
	)

	ctr_bytes = new_value.to_bytes(7, "big")

	return ts_bytes + mac_bytes + ctr_bytes

# ORM models

class Base(DeclarativeBase):
	pass

class SysParameters(Base):
	__tablename__ = "SysParameters"
	key: Mapped[str] = mapped_column(String, primary_key=True)
	value: Mapped[str] = mapped_column(String, nullable=False)
	last_change: Mapped[datetime] = mapped_column(DateTime, default=partial(datetime.now, tz=timezone.utc))

class Counters(Base):
	__tablename__ = "Counters"
	key: Mapped[str] = mapped_column(String, primary_key=True)
	value: Mapped[int] = mapped_column(Integer, nullable=False)
	last_change: Mapped[datetime] = mapped_column(DateTime, default=partial(datetime.now, tz=timezone.utc))

class Item(Base):
	__tablename__ = "items"

	id: Mapped[bytes] = mapped_column(BLOB(16), primary_key=True)
	title: Mapped[str] = mapped_column(nullable=False)
	description: Mapped[str | None] = mapped_column(nullable=True)
	status: Mapped[str] = mapped_column(String, default="pending", nullable=False)
	due_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
	created_at: Mapped[datetime] = mapped_column(DateTime, default=partial(datetime.now, tz=timezone.utc))
	completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
	recurring: Mapped[str | None] = mapped_column(String(64), nullable=True)
	priority: Mapped[int] = mapped_column(Integer, default=0)

# engine factory

def make_engine(db_path: pathlib.Path):
	url = f"sqlite:///{db_path}"
	return create_engine(url, pool_size=10, max_overflow=20, pool_pre_ping=True)