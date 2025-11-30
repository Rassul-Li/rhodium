#!/usr/bin/env python3
"""
chaos.py
Initializes the SQLite database using SQLAlchemy 2.x declarations.
"""
from __future__ import annotations

import os
import hashlib
import secrets
import uuid
import pathlib
import argparse
from datetime import datetime, timezone
from functools import partial

from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Date, ForeignKey

from sqlalchemy.orm import DeclarativeBase, relationship, Session, Mapped, mapped_column
from sqlalchemy.types import BLOB

def get_node_id() -> str:	# 16-bit hex string
	mac_int = uuid.getnode()	# 48-bit int
	mac_bytes = mac_int.to_bytes(6, "big") 
	mac_hash = hashlib.sha256(mac_bytes).digest()
	return mac_hash[0:2].hex()

class Seed:
	def __init__(self):
		self.value = secrets.randbits(56)
	def __call__(self):
		return self.value
	def hex(self):
		return f"{self.value:014x}"

# Engine Factory

def make_engine(db_path: pathlib.Path):
	url = f"sqlite:///{db_path}"
	return create_engine(url, pool_size=10, max_overflow=20, pool_pre_ping=True)

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

def ensure_parameter(session: Session, key: str, value_fn):
	row = session.get(SysParameters, key)
	if row is None:
		v = value_fn()
		session.add(SysParameters(key=key, value=str(v)))
		return v
	return row.value

def ensure_counter(session: Session, key: str, initial_fn):
	row = session.get(Counters, key)
	if row is None:
		v = initial_fn()
		session.add(Counters(key=key, value=int(v)))
		return v
	return row.value

def print_parameters(session: Session):
	print("\n[sys_parameters]")
	rows = session.query(SysParameters).all()
	if not rows:
		print("  (empty)")
	for r in rows:
		print(f"{r.key:<16}: {r.value}  (changed: {r.last_change})")

	print("\n[counters]")
	rows = session.query(Counters).all()
	if not rows:
		print("  (empty)")
	for r in rows:
		print(f"{r.key:<16}: {r.value}  (changed: {r.last_change})")

def initialize_parameters(session: Session):
	seed = Seed()
	ensure_parameter(session, "node_id", get_node_id)
	ensure_parameter(session, "trng_seed", seed.hex)
	ensure_parameter(session, "first_boot", partial(datetime.now, tz=timezone.utc))

	ensure_counter(session, "primary_counter", seed)

	session.commit()

def main():
	parser = argparse.ArgumentParser(description="Rhodium parameter initializer")
	parser.add_argument("--no-preserve-db", action="store_true", help="Drop and recreate DB")
	parser.add_argument('--path', type=pathlib.Path, default=pathlib.Path("/home/rhodium/db"))
	args = parser.parse_args()

	DB_PATH = args.path.joinpath("rhodium.db")

	print(f'DB_PATH: {DB_PATH}')
	DB_PATH.parent.mkdir(parents=True, exist_ok=True)
	engine = make_engine(DB_PATH)

	if not DB_PATH.exists() or args.no_preserve_db:
		print("Creating DB schema...")
		Base.metadata.drop_all(engine)
		Base.metadata.create_all(engine)

		with Session(engine) as session:
			print("Initializing parameters...")
			initialize_parameters(session)
	
	if DB_PATH.exists():
		with Session(engine) as session:
			print_parameters(session)

if __name__ == "__main__":
	main()