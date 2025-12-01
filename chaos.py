#!/usr/bin/env python3
"""
chaos.py
Initializes the SQLite database using SQLAlchemy 2.x declarations.
"""
from __future__ import annotations


import hashlib
import secrets
import uuid
import pathlib
import argparse
from typing import Callable, Any
from datetime import datetime, timezone
from functools import partial

from sqlalchemy.orm import Session

import chiron

def get_node_id() -> str:	# 16-bit hex string
	mac_int = uuid.getnode()	# 48-bit int
	mac_bytes = mac_int.to_bytes(6, "big") 
	mac_hash = hashlib.sha256(mac_bytes).digest()
	return mac_hash[0:2].hex()

class Seed:
	def __init__(self):
		self.value = secrets.randbits(56)
	def __call__(self) -> int:
		return self.value
	def hex(self) -> str:
		return f"{self.value:014x}"

def ensure_parameter(session: Session, key: str, value_fn: Callable[[], Any]) -> str:
	row = session.get(chiron.SysParameters, key)
	if row is None:
		v = value_fn()
		session.add(chiron.SysParameters(key=key, value=str(v)))
		return v
	return row.value

def ensure_counter(session: Session, key: str, initial_fn: Callable[[], Any]) -> int:
	row = session.get(chiron.Counters, key)
	if row is None:
		v = initial_fn()
		session.add(chiron.Counters(key=key, value=int(v)))
		return v
	return row.value

def print_parameters(session: Session):
	print("\n[sys_parameters]")
	rows = session.query(chiron.SysParameters).all()
	if not rows:
		print("  (empty)")
	for r in rows:
		print(f"{r.key:<16}: {r.value}  (changed: {r.last_change})")

	print("\n[counters]")
	rows = session.query(chiron.Counters).all()
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
	engine = chiron.make_engine(DB_PATH)

	if not DB_PATH.exists() or args.no_preserve_db:
		print("Creating DB schema...")
		chiron.Base.metadata.drop_all(engine)
		chiron.Base.metadata.create_all(engine)

		with Session(engine) as session:
			print("Initializing parameters...")
			initialize_parameters(session)
	
	if DB_PATH.exists():
		with Session(engine) as session:
			print_parameters(session)

if __name__ == "__main__":
	main()