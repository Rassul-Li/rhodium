#!/usr/bin/env python3
"""
app.py
Flask 3.x + SQLAlchemy 2.x application for the Rhodium DB schema.

Supports:
- Listing items
- Creating items
- Editing items
- UUID-like 16-byte IDs encoded as hex for URL routing
"""

from __future__ import annotations

import os
import binascii
from datetime import datetime, date
import argparse
import pathlib

from flask import (
	Flask, render_template, request, redirect, url_for, flash, jsonify
)
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session

# Import your models
from chaos import (
	Base, Item, SysParameters, Counters,
	make_engine
)

# ---------------------------------------------------------------------
#  ID Generation
# ---------------------------------------------------------------------

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
	session.commit()

	ctr_bytes = new_value.to_bytes(7, "big")

	return ts_bytes + mac_bytes + ctr_bytes   # 7 + 2 + 7 = 16 bytes


# ---------------------------------------------------------------------
#  Flask App Setup
# ---------------------------------------------------------------------

app = Flask(__name__)
app.secret_key = os.getenv("RHODIUM_SECRET", "pineapple-pizza-extravaganza")

engine = make_engine(pathlib.Path(DB_PATH))

# Ensure DB exists
Base.metadata.create_all(engine)


# ---------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------

def hexid(b: bytes) -> str:
	"""Convert BLOB(16) to readable hex string."""
	return binascii.hexlify(b).decode("ascii")


def unhexid(s: str) -> bytes:
	"""Convert 32-char hex string back to 16 bytes."""
	try:
		b = binascii.unhexlify(s)
		if len(b) != 16:
			raise ValueError
		return b
	except Exception:
		raise ValueError("Invalid item ID")


# ---------------------------------------------------------------------
#  Routes
# ---------------------------------------------------------------------

@app.route("/")
def index():
	"""List all items sorted by priority and due_date."""
	with Session(engine) as session:
		rows = session.execute(
			select(Item).order_by(Item.priority.desc(), Item.due_date.asc().nulls_last())
		).scalars().all()

	return render_template("index.html", items=rows)


@app.route("/create", methods=["GET", "POST"])
def create():
	if request.method == "POST":
		title = request.form["title"].strip()
		if not title:
			flash("Title is required.", "danger")
			return redirect(url_for("create"))

		description = request.form.get("description") or None
		recurring = request.form.get("recurring") or None
		priority = int(request.form.get("priority") or 0)
		due_raw = request.form.get("due_date")

		due_date = datetime.fromisoformat(due_raw) if due_raw else None

		with Session(engine) as session:
			item_id = generate_item_id(session)

			itm = Item(
				id=item_id,
				title=title,
				description=description,
				due_date=due_date,
				recurring=recurring,
				priority=priority,
			)
			session.add(itm)
			session.commit()

		flash("Item created.", "success")
		return redirect(url_for("index"))

	return render_template("create.html")


@app.route("/edit/<string:item_hex>", methods=["GET", "POST"])
def edit(item_hex):
	try:
		item_id = unhexid(item_hex)
	except ValueError:
		return "Invalid ID", 400

	with Session(engine) as session:
		item = session.get(Item, item_id)

		if item is None:
			return "Item not found", 404

		if request.method == "POST":
			title = request.form["title"]
			description = request.form.get("description") or None
			recurring = request.form.get("recurring") or None
			priority = int(request.form.get("priority") or 0)
			status = request.form.get("status") or item.status

			due_raw = request.form.get("due_date")
			due_date = datetime.fromisoformat(due_raw) if due_raw else None

			completed_at = item.completed_at
			if status == "done" and item.completed_at is None:
				completed_at = datetime.now(timezone.utc)

			session.execute(
				update(Item)
				.where(Item.id == item_id)
				.values(
					title=title,
					description=description,
					due_date=due_date,
					recurring=recurring,
					priority=priority,
					status=status,
					completed_at=completed_at,
				)
			)
			session.commit()

			flash("Item updated.", "success")
			return redirect(url_for("index"))

	return render_template("edit.html", item=item, item_hex=item_hex)


@app.route("/api/today")
def api_today():
	"""Return all items due today or overdue."""
	now = datetime.now(timezone.utc)

	with Session(engine) as session:
		rows = session.execute(
			select(Item)
			.where(Item.status != "done")
			.where((Item.due_date == None) | (Item.due_date <= now))
			.order_by(Item.priority.desc())
		).scalars().all()

	return jsonify([
		{
			"id": hexid(i.id),
			"title": i.title,
			"description": i.description,
			"due_date": i.due_date.isoformat() if i.due_date else None,
			"status": i.status,
		}
		for i in rows
	])


# ---------------------------------------------------------------------

if __name__ == "__main__":
	parser = argparse.ArgumentParser(description="Rhodium hestia runtime")
	parser.add_argument('--path', type=pathlib.Path, default=pathlib.Path("/home/rhodium/db"))
	args = parser.parse_args()

	DB_PATH = args.path.joinpath("rhodium.db")

	print(f'DB_PATH: {DB_PATH}')

	DB_URI = f"sqlite:///{DB_PATH}"
	app.run(host="0.0.0.0", port=80)
